"""Download the public OFAC SDN list, record provenance, and write parsed entries.

Run:  .venv/bin/python -m scripts.download_ofac            # live download
      .venv/bin/python -m scripts.download_ofac --from-dir DIR   # parse local copies
      .venv/bin/python -m scripts.download_ofac --snapshot snapshots/ofac/2026-11-24
          # committed snapshot behind every result and the demo (sha256-checked)

Writes (all under data/, which is gitignored):
  data/ofac/raw/<UTC timestamp>/SDN.CSV, ALT.CSV, SDN_COMMENTS.CSV   exact bytes received
  data/ofac/entries.jsonl    one SanctionsEntry per line
  data/ofac/manifest.json    source URL, retrieved_at, sha256 and byte size per file,
                             encoding used, row counts and orphan rows

Why the manifest: screening results must be reproducible for audit. Every later
match can point back to the exact list snapshot (sha256) it was screened against.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from screening.ofac import parse_sdn

# OFAC Sanctions List Service export endpoints. Not yet confirmed against a live
# download (the SLS site renders links with JavaScript); check them on first run.
BASE_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/"
FILES = {"sdn": "SDN.CSV", "alt": "ALT.CSV", "comments": "SDN_COMMENTS.CSV"}
REQUIRED = {"sdn", "alt"}
USER_AGENT = "sanctions-screening-portfolio/0.1 (research; low-volume)"
MIN_EXPECTED_SDN_ROWS = 1000  # the real list has many thousands; fewer means a bad download

Fetcher = Callable[[str], bytes]


def http_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def decode(raw: bytes) -> tuple[str, str]:
    """UTF-8 first; fall back to cp1252 (common for legacy Windows exports). Record which."""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252"


def run(
    out_dir: Path,
    fetch: Fetcher = http_fetch,
    base_url: str = BASE_URL,
    min_rows: int = MIN_EXPECTED_SDN_ROWS,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    raw_dir = out_dir / "raw" / stamp
    raw_dir.mkdir(parents=True, exist_ok=True)

    texts: dict[str, str] = {}
    files_meta: dict[str, dict] = {}
    for key, filename in FILES.items():
        url = base_url + filename
        try:
            raw = fetch(url)
        except Exception as exc:  # noqa: BLE001 - optional file may be absent
            if key in REQUIRED:
                raise RuntimeError(f"Required file {filename} failed: {exc}") from exc
            files_meta[filename] = {"url": url, "status": f"not downloaded: {exc}"}
            continue
        (raw_dir / filename).write_bytes(raw)
        text, encoding = decode(raw)
        texts[key] = text
        files_meta[filename] = {
            "url": url,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "encoding": encoding,
        }

    entries, report = parse_sdn(texts["sdn"], texts.get("alt", ""), texts.get("comments", ""))
    if report.sdn_rows < min_rows:
        raise RuntimeError(
            f"Only {report.sdn_rows} SDN rows (expected >= {min_rows}); refusing to overwrite entries."
        )

    with (out_dir / "entries.jsonl").open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(entry.model_dump_json() + "\n")

    manifest = {
        "source": "OFAC SDN list (legacy CSV)",
        "retrieved_at": now.isoformat(),
        "raw_dir": str(raw_dir.relative_to(out_dir)),
        "files": files_meta,
        "counts": {
            "sdn_rows": report.sdn_rows,
            "alt_rows": report.alt_rows,
            "comment_rows": report.comment_rows,
            "entries_written": len(entries),
            "orphan_alt_rows": len(report.orphan_alt_rows),
            "orphan_comment_rows": len(report.orphan_comment_rows),
        },
        "orphan_alt_ent_nums": report.orphan_alt_rows[:50],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def local_fetcher(directory: Path) -> Fetcher:
    def fetch(url: str) -> bytes:
        return (directory / url.rsplit("/", 1)[-1]).read_bytes()

    return fetch


def snapshot_fetcher(directory: Path) -> tuple[Fetcher, dict]:
    """Read a committed snapshot: gzipped CSVs plus snapshot.json with the original
    retrieval time and per-file sha256 of the uncompressed bytes. Every file is checked
    against its recorded sha256 before parsing; a mismatch stops the load."""
    meta = json.loads((directory / "snapshot.json").read_text())
    expected = {name: info["sha256"] for name, info in meta["files"].items() if "sha256" in info}

    def fetch(url: str) -> bytes:
        name = url.rsplit("/", 1)[-1]
        if name not in expected:
            raise FileNotFoundError(f"{name} is not part of snapshot {directory}")
        raw = gzip.decompress((directory / f"{name}.gz").read_bytes())
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected[name]:
            raise RuntimeError(f"{name}: sha256 {digest[:12]}... does not match snapshot.json {expected[name][:12]}...")
        return raw

    return fetch, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data/ofac"))
    parser.add_argument("--from-dir", type=Path, help="parse files already on disk instead of downloading")
    parser.add_argument("--snapshot", type=Path, help="load a committed snapshot (gzipped, sha256-checked)")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args(argv)
    if args.from_dir and args.snapshot:
        parser.error("use --from-dir or --snapshot, not both")

    if args.snapshot:
        # Keep the original URL and retrieval time so the manifest matches the one the
        # committed results were produced from.
        fetch, meta = snapshot_fetcher(args.snapshot)
        manifest = run(args.out, fetch=fetch, base_url=args.base_url,
                       now=datetime.fromisoformat(meta["retrieved_at"]))
    else:
        fetch = local_fetcher(args.from_dir) if args.from_dir else http_fetch
        manifest = run(args.out, fetch=fetch, base_url=args.base_url if not args.from_dir else "local/")
    print(json.dumps(manifest["counts"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
