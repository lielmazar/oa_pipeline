"""Stage 1 — Mirror the OpenAlex snapshot into your own bucket.

Copies s3://openalex/data/jsonl/<entity>/ (works, sources) server-side into
<storage_root>/snapshots/<snapshot_id>/data/<entity>/. OpenAlex replaces the
public snapshot with every quarterly release; the mirror pins the exact inputs
of your review. The snapshot id is the OpenAlex release date from its
manifest. Re-running skips files already copied (same size), so it is safe to
resume after an interruption.

    python -m pipeline.stage1_mirror                    # mirror latest release
    python -m pipeline.stage1_mirror --dry-run          # inventory + report only
    python -m pipeline.stage1_mirror --max-files 3      # smoke test
"""
from __future__ import annotations

import argparse
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from pipeline.lib import layout, storage
from pipeline.lib.config import Config, add_common_args, config_from_args
from pipeline.lib.parallel import fmt_duration, log, setup_logging
from pipeline.lib.report import Report, num

S3_STANDARD_USD_PER_GB_MONTH = 0.023


def _rel_key(url: str, entity: str) -> str | None:
    marker = f"/{entity}/"
    return entity + "/" + url.split(marker, 1)[1] if marker in url else None


def _manifest_uri(source: str, entity: str) -> str | None:
    """Per-entity manifest: manifest.json (2025-11+ layout) or manifest (legacy)."""
    for name in ("manifest.json", "manifest"):
        uri = storage.join(source, entity, name)
        if storage.exists(uri):
            return uri
    return None


def inventory(source: str, entity: str) -> dict:
    """List an entity's partition files, merging OpenAlex's manifest (record
    counts, release date) with the actual bucket listing (sizes). Only
    updated_date=... partitions count; side files such as deleted_ids.csv.gz
    are ignored."""
    listing = {storage.relative(f.uri, source): f for f in
               storage.list_files(storage.join(source, entity), suffix=".gz")
               if "updated_date=" in f.uri}
    manifest_uri = _manifest_uri(source, entity)
    counts: dict[str, int | None] = {}
    release_date = None
    if manifest_uri:
        m = storage.read_json(manifest_uri)
        release_date = m.get("date")
        for entry in m.get("files") or m.get("entries") or []:
            rel = _rel_key(entry.get("url", ""), entity)
            if rel:
                counts[rel] = (entry.get("meta") or {}).get("record_count")
    files = [{
        "key": rel,
        "source_uri": f.uri,
        "size": f.size,
        "record_count": counts.get(rel),
        "updated_date": layout.updated_date(rel),
    } for rel, f in sorted(listing.items())]
    return {
        "files": files,
        "manifest_found": manifest_uri is not None,
        "manifest_uri": manifest_uri,
        "release_date": release_date,
        "in_manifest_not_in_bucket": sorted(set(counts) - set(listing)),
        "in_bucket_not_in_manifest": sorted(set(listing) - set(counts)) if manifest_uri else [],
    }


def _copy_one(src: str, dst: str, src_size: int) -> str:
    if storage.size(dst) == src_size:
        return "skipped"
    storage.copy(src, dst)
    return "copied"


def mirror(cfg: Config, entities: list[str], max_files: int | None, dry_run: bool) -> dict:
    source = cfg.stage1.source.rstrip("/")
    started = time.monotonic()
    inv = {}
    for entity in entities:
        log.info("Listing %s/%s ...", source, entity)
        inv[entity] = inventory(source, entity)
        n = len(inv[entity]["files"])
        log.info("  %s: %s files, %.1f GB", entity, f"{n:,}",
                 sum(f["size"] for f in inv[entity]["files"]) / 1e9)
        if n == 0:
            raise SystemExit(f"No .gz files found under {source}/{entity}")

    # OpenAlex writes the top-level manifest.json last: present = release complete.
    top_manifest = storage.join(source, "manifest.json")
    release_complete = storage.exists(top_manifest)
    if not release_complete:
        log.warning("No %s — OpenAlex may be mid-release; the mirror could be inconsistent.", top_manifest)
    release = (storage.read_json(top_manifest).get("date") if release_complete else None) or next(
        (d["release_date"] for d in inv.values() if d["release_date"]), None)
    works_dates = [f["updated_date"] for f in inv.get("works", {}).get("files", []) if f["updated_date"]]
    snapshot_id = cfg.snapshot_id or release or (max(works_dates) if works_dates
                                                 else datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    dest_root = layout.snapshot_root(cfg, snapshot_id)
    log.info("Snapshot id: %s → %s", snapshot_id, dest_root)

    jobs = []
    for entity, data in inv.items():
        files = data["files"][:max_files] if max_files else data["files"]
        data["files"] = files
        for f in files:
            f["uri"] = storage.join(dest_root, "data", f["key"])
            jobs.append(f)

    outcome: Counter = Counter()
    failures: list[dict] = []
    if not dry_run:
        bytes_total = sum(j["size"] for j in jobs) or 1
        bytes_done = 0
        last = 0.0
        with ThreadPoolExecutor(max_workers=cfg.stage1.copy_concurrency) as pool:
            futs = {pool.submit(_copy_one, j["source_uri"], j["uri"], j["size"]): j for j in jobs}
            for fut in as_completed(futs):
                j = futs[fut]
                try:
                    outcome[fut.result()] += 1
                except Exception as e:  # noqa: BLE001 — reported, run can be resumed
                    outcome["failed"] += 1
                    failures.append({"key": j["key"], "error": f"{type(e).__name__}: {e}"})
                    log.error("FAILED %s: %s", j["key"], e)
                bytes_done += j["size"]
                now = time.monotonic()
                if now - last > 30 or sum(outcome.values()) == len(jobs):
                    last = now
                    el = now - started
                    log.info("copy %s/%s files · %.1f / %.1f GB · %s elapsed",
                             f"{sum(outcome.values()):,}", f"{len(jobs):,}", bytes_done / 1e9,
                             bytes_total / 1e9, fmt_duration(el))
        for entity, data in inv.items():
            if data["manifest_uri"]:
                name = data["manifest_uri"].rstrip("/").rsplit("/", 1)[-1]
                storage.copy(data["manifest_uri"], storage.join(dest_root, "data", entity, name))
        if release_complete:
            storage.copy(top_manifest, storage.join(dest_root, "data", "manifest.json"))

    complete = not dry_run and not failures and not max_files and release_complete
    manifest = {
        "snapshot_id": snapshot_id,
        "openalex_release": release,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "complete": complete,
        "partial_reason": ("dry run" if dry_run else f"--max-files {max_files}" if max_files
                           else f"{len(failures)} failed copies" if failures
                           else "OpenAlex release manifest missing" if not release_complete else None),
        "entities": {e: {"files": [{k: f[k] for k in ("key", "uri", "size", "record_count", "updated_date")}
                                   for f in d["files"]],
                         "total_size": sum(f["size"] for f in d["files"]),
                         "total_records": sum(f["record_count"] or 0 for f in d["files"]),
                         "manifest_found": d["manifest_found"],
                         "in_manifest_not_in_bucket": d["in_manifest_not_in_bucket"],
                         "in_bucket_not_in_manifest": d["in_bucket_not_in_manifest"]}
                     for e, d in inv.items()},
    }
    summary = {
        "stage": "stage1_mirror", "snapshot_id": snapshot_id, "destination": dest_root,
        "dry_run": dry_run, "complete": complete, "duration_sec": round(time.monotonic() - started, 1),
        "files": len(jobs), "bytes": sum(j["size"] for j in jobs),
        "copied": outcome["copied"], "skipped_existing": outcome["skipped"],
        "failed": outcome["failed"], "failures": failures,
    }
    if not dry_run:
        storage.write_json(layout.snapshot_manifest(cfg, snapshot_id), manifest)
    report = build_report(cfg, manifest, summary)
    storage.write_text(storage.join(dest_root, "stage1_report.html"), report, "text/html; charset=utf-8")
    storage.write_json(storage.join(dest_root, "stage1_report.json"), summary)
    log.info("Report: %s", storage.join(dest_root, "stage1_report.html"))
    return summary


def build_report(cfg: Config, manifest: dict, s: dict) -> str:
    r = Report(f"Stage 1 · Snapshot mirror {s['snapshot_id']}",
               f"OpenAlex release {manifest['openalex_release'] or 'unknown'} · "
               f"{manifest['source']} → {s['destination']}")
    gb = s["bytes"] / 1e9
    total_records = sum(e["total_records"] for e in manifest["entities"].values())
    r.kpis([
        ("Files", s["files"], "partition files in scope"),
        ("Size", f"{gb:,.1f} GB", "compressed JSONL"),
        ("Records", total_records, "per OpenAlex manifests"),
        ("Copied now", s["copied"], f"{s['skipped_existing']:,} already present"),
        ("Failed", s["failed"], "re-run to retry" if s["failed"] else "none"),
        ("Duration", fmt_duration(s["duration_sec"]), "dry run" if s["dry_run"] else ""),
    ])
    if not s["complete"]:
        r.section("Status")
        r.warning(f"This snapshot is <b>partial</b> ({manifest['partial_reason']}). Later stages will "
                  "process only the files listed here.")

    r.section("Entities")
    rows = []
    for e, d in manifest["entities"].items():
        rows.append([e, len(d["files"]), f"{d['total_size'] / 1e9:,.2f} GB", d["total_records"],
                     "yes" if d["manifest_found"] else "no (listing only)"])
    r.raw(r.table(["Entity", "Files", "Size", "Records", "OpenAlex manifest"], rows, numeric={1, 2, 3}))
    issues = [(e, k, "listed in manifest but missing from bucket") for e, d in manifest["entities"].items()
              for k in d["in_manifest_not_in_bucket"]]
    issues += [(e, k, "in bucket but not in manifest") for e, d in manifest["entities"].items()
               for k in d["in_bucket_not_in_manifest"]]
    if issues:
        r.warning(f"{len(issues)} manifest/listing discrepancies — OpenAlex may have been mid-release.")
        r.raw(r.table(["Entity", "File", "Issue"], [list(i) for i in issues[:50]]))

    works = manifest["entities"].get("works")
    if works:
        by_month: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for f in works["files"]:
            m = (f["updated_date"] or "unknown")[:7]
            by_month[m][0] += f["record_count"] or 0
            by_month[m][1] += f["size"]
        months = sorted(by_month)
        r.section("Works partitions by update month",
                  "OpenAlex partitions works by the date each record was last updated, not by publication date.")
        r.grid(r.columns([(m, by_month[m][0]) for m in months], "Records per update month"),
               r.bars(sorted(((m, v[0]) for m, v in by_month.items()), key=lambda x: -x[1])[:12],
                      "Largest update months (records)"))

    r.section("Cost and next step")
    r.text(f"S3 Standard storage for this mirror: about <b>${gb * S3_STANDARD_USD_PER_GB_MONTH:,.2f} per month</b> "
           f"({gb:,.1f} GB × ${S3_STANDARD_USD_PER_GB_MONTH}/GB). Server-side copies inside us-east-1 have no "
           "data-transfer charge.")
    if cfg.aws_region != "us-east-1":
        r.warning(f"Your aws_region is {num(cfg.aws_region)}; copying from us-east-1 adds inter-region "
                  "transfer charges (about $0.02/GB).")
    if s["failures"]:
        r.raw(r.table(["File", "Error"], [[f["key"], f["error"]] for f in s["failures"][:50]], title="Failures"))
    r.code(f"python -m pipeline.stage2_journals --snapshot-id {s['snapshot_id']}")
    return r.to_html()


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument("--entities", nargs="+", help="Entities to mirror (default from config: works sources)")
    p.add_argument("--max-files", type=int, help="Copy only the first N files per entity (smoke test)")
    p.add_argument("--dry-run", action="store_true", help="List and report without copying")
    args = p.parse_args(argv)
    cfg = config_from_args(args)
    setup_logging(Path("logs"), "stage1")
    return mirror(cfg, args.entities or cfg.stage1.entities, args.max_files, args.dry_run)


if __name__ == "__main__":
    main()
