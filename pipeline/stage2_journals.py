"""Stage 2 — Filter the mirrored corpus down to journal articles.

Reads every works partition of the snapshot, keeps records that pass the
rules in config.yaml (stage2: work type, journal as primary source, not
paratext/retracted/xpac, DOI, language, abstract) and writes them as Parquet
— one file per partition — with flat columns for analysis plus the full
original record in `raw_json`. Every dropped record is counted under the
first rule it failed, so the report shows a complete funnel.

    python -m pipeline.stage2_journals                       # full run (resumes)
    python -m pipeline.stage2_journals --max-partitions 5    # smoke test
    python -m pipeline.stage2_journals --restart             # discard previous stage 2/3 output
"""
from __future__ import annotations

import argparse
import gzip
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.lib import duck, layout, storage
from pipeline.lib.config import Config, Stage2Config, add_common_args, config_from_args
from pipeline.lib.parallel import fmt_duration, log, run_tasks, setup_logging
from pipeline.lib.records import (DROP_REASONS, JOURNAL_SCHEMA, WORK_SCHEMA, JSONDecodeError,
                                  journal_row, loads, screen, work_row)
from pipeline.lib.report import Report, pct

BATCH_ROWS = 10_000
PLACEHOLDER_TOPIC = ("T13497", 0.9879)   # topic OpenAlex assigns when it has no text to classify

# ---------------------------------------------------------------------------
# Worker (runs in child processes)
# ---------------------------------------------------------------------------

_CRIT: Stage2Config | None = None


def _init_worker(crit: dict) -> None:
    global _CRIT
    _CRIT = Stage2Config(**crit)


def _local_input(task: dict) -> tuple[Path, bool]:
    if storage.is_s3(task["uri"]):
        dest = Path(task["tmp_dir"]) / f"{task['name']}.gz"
        return storage.download(task["uri"], dest), True
    return Path(task["uri"]), False


def process_partition(task: dict) -> dict:
    crit = _CRIT
    started = time.monotonic()
    tmp_dir = Path(task["tmp_dir"])
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local, is_tmp = _local_input(task)
    out_local = tmp_dir / f"{task['name']}.parquet"

    drops: Counter = Counter()
    by_type: Counter = Counter()
    by_source_type: Counter = Counter()
    type_x_source: Counter = Counter()
    by_year_in: Counter = Counter()
    n_in = kept = fallback = 0
    errors: list[str] = []
    batch: list[dict] = []

    try:
        with pq.ParquetWriter(out_local, WORK_SCHEMA, compression="zstd") as writer, \
                gzip.open(local, "rb") as fh:
            for line_no, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                n_in += 1
                try:
                    rec = loads(raw)
                except JSONDecodeError as e:
                    drops["json_error"] += 1
                    if len(errors) < 5:
                        errors.append(f"line {line_no}: {e}")
                    continue
                wtype = rec.get("type") or "(none)"
                stype = ((rec.get("primary_location") or {}).get("source") or {}).get("type") or "(no source)"
                by_type[wtype] += 1
                by_source_type[stype] += 1
                type_x_source[f"{wtype}|{stype}"] += 1
                by_year_in[str(rec.get("publication_year"))] += 1

                reason, loc, from_fallback = screen(rec, crit)
                if reason:
                    drops[reason] += 1
                    continue
                kept += 1
                fallback += from_fallback
                batch.append(work_row(rec, loc, from_fallback, raw, crit.store_raw_json))
                if len(batch) >= BATCH_ROWS:
                    writer.write_table(pa.Table.from_pylist(batch, WORK_SCHEMA))
                    batch = []
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, WORK_SCHEMA))
        storage.upload(out_local, task["out_parquet"])
    finally:
        out_local.unlink(missing_ok=True)
        if is_tmp:
            local.unlink(missing_ok=True)

    stats = {
        "name": task["name"], "source_uri": task["uri"], "bytes_in": task["size"],
        "records_in": n_in, "kept": kept, "kept_via_fallback": fallback,
        "drops": dict(drops), "by_type": dict(by_type), "by_source_type": dict(by_source_type),
        "type_x_source": dict(type_x_source), "by_year_in": dict(by_year_in),
        "errors": errors, "seconds": round(time.monotonic() - started, 1),
    }
    storage.write_json(task["out_stats"], stats)   # written last: marks the partition done
    return stats


# ---------------------------------------------------------------------------
# Journals table from the `sources` entity
# ---------------------------------------------------------------------------

def build_journals(cfg: Config, manifest: dict, out_uri: str) -> int:
    files = manifest["entities"].get("sources", {}).get("files", [])
    if not files:
        log.warning("Snapshot has no `sources` entity — skipping journals table "
                    "(mirror it with: python -m pipeline.stage1_mirror --entities works sources)")
        return 0
    tmp = Path(cfg.tmp_dir) / "journals.parquet"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for f in files:
        local = Path(cfg.tmp_dir) / "sources_part.gz"
        src = storage.download(f["uri"], local) if storage.is_s3(f["uri"]) else Path(f["uri"])
        with gzip.open(src, "rb") as fh:
            for raw in fh:
                if raw.strip():
                    row = journal_row(loads(raw))
                    if row:
                        rows.append(row)
        if src == local:
            local.unlink(missing_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, JOURNAL_SCHEMA), tmp, compression="zstd")
    storage.upload(tmp, out_uri)
    tmp.unlink(missing_ok=True)
    log.info("Journals table: %s journals → %s", f"{len(rows):,}", out_uri)
    return len(rows)


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------

def ensure_run(cfg: Config, snapshot_id: str, restart: bool) -> dict:
    """Pin the run to one snapshot and one set of stage 2 rules."""
    run_json = storage.join(layout.run_root(cfg), "run.json")
    wanted = {"snapshot_id": snapshot_id, "stage2": asdict(cfg.stage2)}
    if restart:
        for stage in ("stage2", "stage3"):
            n = storage.delete_prefix(layout.stage_root(cfg, stage))
            log.info("--restart: removed %s files from %s", f"{n:,}", stage)
    if storage.exists(run_json):
        current = storage.read_json(run_json)
        if not restart and {k: current.get(k) for k in wanted} != wanted:
            raise SystemExit(
                f"Run {cfg.run_id!r} was created with a different snapshot or stage2 settings.\n"
                f"  existing: {({k: current.get(k) for k in wanted})}\n  requested: {wanted}\n"
                "Use a new --run-id, or --restart to discard this run's stage 2 and 3 outputs.")
        if not restart:
            return current
    run = {"run_id": cfg.run_id, "created_at": datetime.now(timezone.utc).isoformat(), **wanted}
    storage.write_json(run_json, run)
    return run


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument("--max-partitions", type=int, help="Process at most N partitions (smoke test)")
    p.add_argument("--restart", action="store_true", help="Delete this run's stage 2/3 outputs first")
    p.add_argument("--report-only", action="store_true", help="Rebuild the report from existing outputs")
    args = p.parse_args(argv)
    cfg = config_from_args(args)
    setup_logging(Path("logs"), "stage2")
    started = time.monotonic()

    snapshot_id = layout.resolve_snapshot(cfg)
    manifest = storage.read_json(layout.snapshot_manifest(cfg, snapshot_id))
    ensure_run(cfg, snapshot_id, args.restart)
    root = layout.stage_root(cfg, "stage2")
    log.info("Stage 2 · run %s · snapshot %s%s → %s", cfg.run_id, snapshot_id,
             "" if manifest["complete"] else " (PARTIAL snapshot)", root)

    tasks = []
    for f in manifest["entities"]["works"]["files"]:
        name = layout.partition_name(f["key"])
        tasks.append({"name": name, "uri": f["uri"], "size": f["size"],
                      "out_parquet": storage.join(root, "parquet", f"{name}.parquet"),
                      "out_stats": storage.join(root, "stats", f"{name}.json"),
                      "tmp_dir": str(Path(cfg.tmp_dir) / "stage2")})
    total_partitions = len(tasks)
    if args.max_partitions:
        tasks = tasks[:args.max_partitions]

    done = {Path(f.uri).stem for f in storage.list_files(storage.join(root, "stats"), suffix=".json")}
    pending = [t for t in tasks if t["name"] not in done]
    log.info("%s partitions in scope, %s already done, %s to process with %d workers",
             f"{len(tasks):,}", f"{len(tasks) - len(pending):,}", f"{len(pending):,}", cfg.n_workers)

    failures: list[dict] = []
    if pending and not args.report_only:
        results = run_tasks(pending, process_partition, cfg.n_workers,
                            initializer=_init_worker, initargs=(asdict(cfg.stage2),))
        failures = [r for r in results if r.get("failed")]

    journals_uri = storage.join(root, "journals.parquet")
    if not storage.exists(journals_uri) and not args.report_only:
        build_journals(cfg, manifest, journals_uri)

    stats = [storage.read_json(f.uri) for f in storage.list_files(storage.join(root, "stats"), suffix=".json")]
    summary = summarize(stats, total_partitions, failures, manifest, cfg)
    summary["duration_sec"] = round(time.monotonic() - started, 1)
    html = build_report(cfg, summary, root, journals_uri)
    storage.write_text(storage.join(root, "report.html"), html, "text/html; charset=utf-8")
    storage.write_json(storage.join(root, "report.json"), summary)
    log.info("Kept %s of %s records (%s). Report: %s", f"{summary['kept']:,}",
             f"{summary['records_in']:,}", pct(summary["kept"], summary["records_in"]),
             storage.join(root, "report.html"))
    return summary


# ---------------------------------------------------------------------------
# Summary + report
# ---------------------------------------------------------------------------

def _merge(stats: list[dict], key: str) -> Counter:
    c: Counter = Counter()
    for s in stats:
        c.update(s.get(key) or {})
    return c


def summarize(stats: list[dict], total_partitions: int, failures: list[dict],
              manifest: dict, cfg: Config) -> dict:
    records_in = sum(s["records_in"] for s in stats)
    kept = sum(s["kept"] for s in stats)
    return {
        "stage": "stage2_journals", "run_id": cfg.run_id, "snapshot_id": manifest["snapshot_id"],
        "snapshot_complete": manifest["complete"],
        "complete": manifest["complete"] and len(stats) == total_partitions and not failures,
        "partitions_done": len(stats), "partitions_total": total_partitions,
        "partitions_failed": [{"name": f["name"], "error": f["error"]} for f in failures],
        "records_in": records_in, "kept": kept,
        "kept_via_fallback": sum(s["kept_via_fallback"] for s in stats),
        "drops": dict(_merge(stats, "drops")),
        "input_by_type": dict(_merge(stats, "by_type").most_common()),
        "input_by_source_type": dict(_merge(stats, "by_source_type").most_common()),
        "input_type_x_source": dict(_merge(stats, "type_x_source").most_common(40)),
        "input_by_year": dict(_merge(stats, "by_year_in")),
        "json_errors": [e for s in stats for e in s.get("errors", [])][:20],
        "criteria": asdict(cfg.stage2),
    }


def _enabled_steps(crit: dict) -> list[str]:
    steps = ["json_error", "wrong_type"]
    if crit["require_journal_source"]:
        steps += ["no_source", "source_not_journal"]
    steps += [s for s, flag in (("paratext", "exclude_paratext"), ("retracted", "exclude_retracted"),
                                ("xpac", "exclude_xpac"), ("no_doi", "require_doi")) if crit[flag]]
    if crit["languages"]:
        steps += (["language"] if crit.get("allow_unknown_language") else ["language_unknown", "language"])
    if crit["require_abstract"]:
        steps.append("no_abstract")
    return steps


def build_report(cfg: Config, s: dict, root: str, journals_uri: str) -> str:
    crit = s["criteria"]
    r = Report(f"Stage 2 · Journal articles · run {s['run_id']}",
               f"snapshot {s['snapshot_id']} · output {root}")
    r.kpis([
        ("Records scanned", s["records_in"], f"{s['partitions_done']:,} of {s['partitions_total']:,} partitions"),
        ("Journal articles kept", s["kept"], pct(s["kept"], s["records_in"]) + " of scanned"),
        ("Dropped", s["records_in"] - s["kept"], "see funnel"),
        ("Failed partitions", len(s["partitions_failed"]), "re-run to retry" if s["partitions_failed"] else "none"),
        ("Duration", fmt_duration(s["duration_sec"]), "this invocation"),
    ])
    if not s["complete"]:
        reasons = []
        if not s["snapshot_complete"]:
            reasons.append("the snapshot mirror is partial")
        if s["partitions_done"] < s["partitions_total"]:
            reasons.append(f"{s['partitions_total'] - s['partitions_done']:,} partitions not processed yet")
        if s["partitions_failed"]:
            reasons.append(f"{len(s['partitions_failed'])} partitions failed")
        r.section("Status")
        r.warning("Results are <b>incomplete</b>: " + "; ".join(reasons) + ".")

    r.section("Selection funnel", "Each record is counted once, under the first rule it fails.")
    r.raw(r.funnel("All records in scanned partitions", s["records_in"],
                   [(DROP_REASONS[k], s["drops"].get(k, 0)) for k in _enabled_steps(crit)]))
    r.raw("<h3>Rules</h3>" + r.table(["Setting", "Value"], [[k, str(v)] for k, v in crit.items()]))

    r.section("Input corpus composition", "All scanned records, before filtering.")
    total = s["records_in"]
    r.grid(r.bars(list(s["input_by_type"].items())[:15], "Work type", total),
           r.bars(list(s["input_by_source_type"].items())[:15], "Primary source type", total))
    r.raw(r.table(["Work type", "Primary source type", "Records", "Share"],
                  [[*k.split("|", 1), v, pct(v, total, 2)] for k, v in list(s["input_type_x_source"].items())[:20]],
                  numeric={2, 3}, title="Type × source type (top 20)"))

    if s["kept"]:
        _kept_sections(r, cfg, s, root, journals_uri)

    r.section("Explore the data")
    r.text("Stage 2 output is Parquet (one file per partition). Query it with the explore tool or DuckDB:")
    r.code("python -m pipeline.explore tables\n"
           "python -m pipeline.explore sql \"SELECT journal_name, count(*) n FROM stage2 GROUP BY 1 ORDER BY n DESC LIMIT 20\"\n"
           "python -m pipeline.explore sql \"SELECT * FROM stage2 s JOIN journals j USING (journal_id) "
           "WHERE j.two_yr_mean_citedness > 5 LIMIT 10\"\n"
           f"# DuckDB directly:  SELECT * FROM {duck.parquet_source(storage.join(root, 'parquet'))} LIMIT 10;")
    if s["partitions_failed"]:
        r.raw(r.table(["Partition", "Error"], [[f["name"], f["error"]] for f in s["partitions_failed"]],
                      title="Failed partitions"))
    if s["json_errors"]:
        r.raw(r.table(["JSON errors (first 20)"], [[e] for e in s["json_errors"]]))
    r.code(f"Next: python -m pipeline.stage3_keywords --run-id {s['run_id']}")
    return r.to_html()


def _kept_sections(r: Report, cfg: Config, s: dict, root: str, journals_uri: str) -> None:
    con = duck.connect(cfg.storage_root, cfg.aws_region)
    con.execute(f"CREATE VIEW kept AS SELECT * EXCLUDE (raw_json) FROM {duck.parquet_source(storage.join(root, 'parquet'))}")
    has_journals = storage.exists(journals_uri)
    if has_journals:
        con.execute(f"CREATE VIEW journals AS SELECT * FROM read_parquet('{journals_uri}')")
    kept = s["kept"]

    r.section("Journal articles kept")
    years = duck.rows(con, "SELECT publication_year, count(*) FROM kept WHERE publication_year BETWEEN 1900 AND 2100 "
                           "GROUP BY 1 ORDER BY 1")
    r.raw(r.columns([(y, n) for y, n in years], "Publication year"))
    r.grid(
        r.bars(duck.pairs(con, "SELECT field, count(*) n FROM kept GROUP BY 1 ORDER BY n DESC LIMIT 15"),
               "Field (primary topic)", kept),
        r.bars(duck.pairs(con, "SELECT oa_status, count(*) n FROM kept GROUP BY 1 ORDER BY n DESC"),
               "Open-access status", kept),
        r.bars(duck.pairs(con, "SELECT c, count(*) n FROM (SELECT unnest(countries) c FROM kept) "
                               "GROUP BY 1 ORDER BY n DESC LIMIT 15"), "Author countries (records)", kept),
        r.bars(duck.pairs(con, "SELECT publisher, count(*) n FROM kept GROUP BY 1 ORDER BY n DESC LIMIT 15"),
               "Publishers", kept),
    )

    r.section("Journals", "Who published the kept articles, and how those journals rank. "
              "2-yr mean citedness is OpenAlex's impact-factor-like metric; compare it only within a field.")
    n_journals = duck.rows(con, "SELECT count(DISTINCT journal_id) FROM kept")[0][0]
    lists = duck.pairs(con, "SELECT l, count(*) n FROM (SELECT unnest(journal_listed_in) l FROM kept) GROUP BY 1 ORDER BY n DESC")
    flags = duck.rows(con, "SELECT count(*) FILTER (journal_is_core), count(*) FILTER (journal_is_in_doaj), "
                           "count(*) FILTER (journal_from_fallback) FROM kept")[0]
    r.kpis([("Distinct journals", n_journals, ""),
            ("In CWTS core journals", flags[0], pct(flags[0], kept)),
            ("In DOAJ journals", flags[1], pct(flags[1], kept)),
            ("Journal from fallback location", flags[2], "allow_location_fallback")])
    if has_journals:
        top = duck.rows(con, """
            SELECT k.journal_name, k.publisher, k.journal_issn_l, count(*) n,
                   any_value(j.two_yr_mean_citedness), any_value(j.h_index), any_value(j.top_field),
                   any_value(k.journal_is_core)
            FROM kept k LEFT JOIN journals j USING (journal_id)
            GROUP BY k.journal_id, k.journal_name, k.publisher, k.journal_issn_l ORDER BY n DESC LIMIT 25""")
        rows = [[a, b, c, d, round(e, 2) if e is not None else None, f, g, "yes" if h else ""]
                for a, b, c, d, e, f, g, h in top]
        r.raw(r.table(["Journal", "Publisher", "ISSN-L", "Articles", "2-yr citedness", "h-index",
                       "Top field", "Core"], rows, numeric={3, 4, 5}, title="Top 25 journals by articles kept"))
    else:
        top = duck.rows(con, "SELECT journal_name, publisher, journal_issn_l, count(*) n FROM kept "
                             "GROUP BY 1,2,3 ORDER BY n DESC LIMIT 25")
        r.raw(r.table(["Journal", "Publisher", "ISSN-L", "Articles"], [list(t) for t in top],
                      numeric={3}, title="Top 25 journals by articles kept"))
        r.warning("No journals table: mirror the <code>sources</code> entity in stage 1 to add journal metrics.")
    r.raw(r.bars(lists, "Journal list membership (articles)", kept))

    r.section("Data quality")
    q = duck.rows(con, f"""
        SELECT count(*) FILTER (primary_topic_id = '{PLACEHOLDER_TOPIC[0]}'
                                AND round(primary_topic_score, 4) = {PLACEHOLDER_TOPIC[1]}),
               count(*) FILTER (abstract_text IS NULL OR len(abstract_text) < 300),
               count(*) FILTER (fwci IS NULL), count(*) FILTER (authors_count = 0),
               count(*) FILTER (publication_year IS NULL)
        FROM kept""")[0]
    r.raw(r.table(["Check", "Articles", "Share", "Why it matters"], [
        ["Placeholder topic (T13497 'Hermeneutics…', score 0.9879)", q[0], pct(q[0], kept, 2),
         "OpenAlex's fallback when it has no text; topic fields are meaningless"],
        ["Abstract shorter than 300 characters", q[1], pct(q[1], kept, 2), "Truncated or junk abstracts"],
        ["No FWCI", q[2], pct(q[2], kept, 2), "Citation normalisation unavailable"],
        ["No authors", q[3], pct(q[3], kept, 2), "Usually editorial or front matter"],
        ["No publication year", q[4], pct(q[4], kept, 2), ""],
    ], numeric={1, 2}))
    con.close()


if __name__ == "__main__":
    main()
