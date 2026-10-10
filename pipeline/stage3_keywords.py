"""Stage 3 — Keep journal articles whose title/abstract match every keyword group.

Reads stage 2 Parquet, matches the keyword groups from research_schema.json
(e.g. a fuel term AND an impact term) and writes the matches with full
provenance: which terms, stems and subgroups hit, and whether in the title.

Outputs (under runs/<run_id>/stage3/):
    parquet/<partition>.parquet   matches per partition
    results.parquet               all matches in one file
    filtered_records.sqlite       same rows (without raw_json) for SQLite tools / the LLM step
    report.html / report.json     analytics, keyword diagnostics, sample matches

    python -m pipeline.stage3_keywords
    python -m pipeline.stage3_keywords --restart        # after editing keywords
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sqlite3
import time
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.lib import duck, layout, storage
from pipeline.lib.config import Config, add_common_args, config_from_args
from pipeline.lib.keywords import KeywordGroup, load_keyword_groups, match_groups
from pipeline.lib.parallel import fmt_duration, log, run_tasks, setup_logging
from pipeline.lib.records import COLUMN_DOCS, WORK_SCHEMA
from pipeline.lib.report import Report, pct

BATCH_ROWS = 5_000


def group_fields(groups: list[KeywordGroup]) -> list[tuple[str, pa.DataType, str]]:
    out = []
    for g in groups:
        out += [
            (f"matched_{g.key}_terms", pa.list_(pa.string()), f"{g.name}: terms found in title/abstract"),
            (f"matched_{g.key}_stems", pa.list_(pa.string()), f"{g.name}: normalised/stemmed form of those terms"),
            (f"matched_{g.key}_subgroups", pa.list_(pa.string()), f"{g.name}: subgroups of the matched terms"),
            (f"{g.key}_in_title", pa.bool_(), f"{g.name}: at least one term is in the title"),
        ]
    return out


def result_schema(groups: list[KeywordGroup]) -> pa.Schema:
    return pa.schema(list(WORK_SCHEMA) + [pa.field(n, t) for n, t, _ in group_fields(groups)])


def pattern_label(groups: list[KeywordGroup], hits: list[bool]) -> str:
    keys = [g.key for g, h in zip(groups, hits) if h]
    return " + ".join(keys) if keys else "none"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

_GROUPS: list[KeywordGroup] = []


def _init_worker(keywords_path: str) -> None:
    global _GROUPS
    _GROUPS, _ = load_keyword_groups(Path(keywords_path))


def process_partition(task: dict) -> dict:
    groups = _GROUPS
    started = time.monotonic()
    tmp_dir = Path(task["tmp_dir"])
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if storage.is_s3(task["uri"]):
        local = storage.download(task["uri"], tmp_dir / f"{task['name']}.in.parquet")
    else:
        local = Path(task["uri"])
    out_local = tmp_dir / f"{task['name']}.parquet"
    schema = result_schema(groups)

    n_in = matched = 0
    patterns: Counter = Counter()
    term_all = {g.key: Counter() for g in groups}
    term_matched = {g.key: Counter() for g in groups}
    subgroup_matched = {g.key: Counter() for g in groups}
    pairs: Counter = Counter()
    in_title: Counter = Counter()

    try:
        with pq.ParquetWriter(out_local, schema, compression="zstd") as writer:
            for batch in pq.ParquetFile(local).iter_batches(batch_size=BATCH_ROWS):
                titles = batch.column("title").to_pylist()
                abstracts = batch.column("abstract_text").to_pylist()
                mask: list[bool] = []
                extra: dict[str, list] = {n: [] for n, _, _ in group_fields(groups)}
                for title, abstract in zip(titles, abstracts):
                    n_in += 1
                    ms = match_groups(groups, title or "", abstract or "")
                    hits = [m.hit for m in ms]
                    patterns[pattern_label(groups, hits)] += 1
                    for g, m in zip(groups, ms):
                        term_all[g.key].update(m.terms)
                    ok = all(hits)
                    mask.append(ok)
                    if not ok:
                        continue
                    matched += 1
                    for g, m in zip(groups, ms):
                        extra[f"matched_{g.key}_terms"].append(m.terms)
                        extra[f"matched_{g.key}_stems"].append(m.stems)
                        extra[f"matched_{g.key}_subgroups"].append(m.subgroups)
                        extra[f"{g.key}_in_title"].append(m.in_title)
                        term_matched[g.key].update(m.terms)
                        subgroup_matched[g.key].update(m.subgroups)
                    if len(ms) >= 2:
                        for a in ms[0].subgroups:
                            for b in ms[1].subgroups:
                                pairs[f"{a}|{b}"] += 1
                    n_title = sum(m.in_title for m in ms)
                    in_title["all_groups" if n_title == len(ms) else "some_groups" if n_title else "abstract_only"] += 1
                if any(mask):
                    tbl = pa.Table.from_batches([batch.filter(pa.array(mask))])
                    for name, typ, _ in group_fields(groups):
                        tbl = tbl.append_column(pa.field(name, typ), pa.array(extra[name], typ))
                    writer.write_table(tbl.select(schema.names))
        storage.upload(out_local, task["out_parquet"])
    finally:
        out_local.unlink(missing_ok=True)
        if storage.is_s3(task["uri"]):
            local.unlink(missing_ok=True)

    stats = {
        "name": task["name"], "records_in": n_in, "matched": matched,
        "patterns": dict(patterns), "in_title": dict(in_title), "subgroup_pairs": dict(pairs),
        "term_hits_all": {k: dict(v) for k, v in term_all.items()},
        "term_hits_matched": {k: dict(v) for k, v in term_matched.items()},
        "subgroup_hits_matched": {k: dict(v) for k, v in subgroup_matched.items()},
        "seconds": round(time.monotonic() - started, 1),
    }
    storage.write_json(task["out_stats"], stats)
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument("--keywords", type=Path, help="Keyword file (default: stage3.keywords_file in config)")
    p.add_argument("--restart", action="store_true", help="Delete this run's stage 3 outputs first")
    p.add_argument("--report-only", action="store_true", help="Rebuild the report from existing outputs")
    args = p.parse_args(argv)
    cfg = config_from_args(args)
    setup_logging(Path("logs"), "stage3")
    started = time.monotonic()

    s2 = layout.stage_root(cfg, "stage2")
    root = layout.stage_root(cfg, "stage3")
    if not storage.exists(storage.join(layout.run_root(cfg), "run.json")):
        raise SystemExit(f"Run {cfg.run_id!r} has no stage 2 output — run stage 2 first.")
    if args.restart:
        log.info("--restart: removed %s files from stage3", f"{storage.delete_prefix(root):,}")

    kw_path = args.keywords or cfg.resolve_path(cfg.stage3.keywords_file)
    groups, kw_hash = load_keyword_groups(kw_path)
    kw_uri = storage.join(root, "keywords.json")
    section = json.loads(kw_path.read_text(encoding="utf-8"))
    section = section.get("keywords", section)
    if storage.exists(kw_uri):
        prev = storage.read_json(kw_uri)
        if prev.get("sha256") != kw_hash:
            raise SystemExit("Keywords changed since this run's stage 3 started. "
                             "Re-run with --restart (or use a new --run-id).")
    else:
        storage.write_json(kw_uri, {"sha256": kw_hash, "source": str(kw_path), "keywords": section})
    log.info("Stage 3 · run %s · %d keyword groups: %s", cfg.run_id, len(groups),
             ", ".join(f"{g.key} ({len(g.terms)} terms)" for g in groups))

    s2_stats = {Path(f.uri).stem: storage.read_json(f.uri)
                for f in storage.list_files(storage.join(s2, "stats"), suffix=".json")}
    s2_report = storage.join(s2, "report.json")
    s2_summary = storage.read_json(s2_report) if storage.exists(s2_report) else {}
    if not s2_summary.get("complete"):
        log.warning("Stage 2 is not complete — stage 3 covers only the %s partitions done so far.",
                    f"{len(s2_stats):,}")

    tasks = [{"name": name, "uri": storage.join(s2, "parquet", f"{name}.parquet"),
              "size": st.get("bytes_in", 0),
              "out_parquet": storage.join(root, "parquet", f"{name}.parquet"),
              "out_stats": storage.join(root, "stats", f"{name}.json"),
              "tmp_dir": str(Path(cfg.tmp_dir) / "stage3")} for name, st in sorted(s2_stats.items())]
    done = {Path(f.uri).stem for f in storage.list_files(storage.join(root, "stats"), suffix=".json")}
    pending = [t for t in tasks if t["name"] not in done]
    log.info("%s partitions, %s already done, %s to process with %d workers",
             f"{len(tasks):,}", f"{len(tasks) - len(pending):,}", f"{len(pending):,}", cfg.n_workers)

    failures: list[dict] = []
    if pending and not args.report_only:
        results = run_tasks(pending, process_partition, cfg.n_workers,
                            initializer=_init_worker, initargs=(str(kw_path),))
        failures = [r for r in results if r.get("failed")]

    stats = [storage.read_json(f.uri) for f in storage.list_files(storage.join(root, "stats"), suffix=".json")]
    summary = summarize(stats, groups, kw_hash, len(tasks), failures, s2_summary, cfg)
    if not args.report_only or not storage.exists(storage.join(root, "results.parquet")):
        consolidate(cfg, root, groups)
    summary["duration_sec"] = round(time.monotonic() - started, 1)
    storage.write_text(storage.join(root, "report.html"), build_report(cfg, summary, groups, root),
                       "text/html; charset=utf-8")
    storage.write_json(storage.join(root, "report.json"), summary)
    log.info("Matched %s of %s journal articles (%s). Report: %s", f"{summary['matched']:,}",
             f"{summary['records_in']:,}", pct(summary["matched"], summary["records_in"], 3),
             storage.join(root, "report.html"))
    return summary


def consolidate(cfg: Config, root: str, groups: list[KeywordGroup]) -> None:
    """Merge partition outputs into results.parquet and filtered_records.sqlite."""
    if not storage.list_files(storage.join(root, "parquet"), suffix=".parquet"):
        log.warning("No stage 3 partition outputs yet — nothing to consolidate.")
        return
    tmp = Path(cfg.tmp_dir) / "stage3_consolidate"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    con = duck.connect(cfg.storage_root, cfg.aws_region)
    src = duck.parquet_source(storage.join(root, "parquet"))
    out_parquet = tmp / "results.parquet"
    con.execute(f"COPY (SELECT * FROM {src} ORDER BY publication_year DESC, openalex_id) "
                f"TO '{out_parquet}' (FORMAT parquet, COMPRESSION zstd)")

    # SQLite copy: lists as JSON text, booleans as 0/1, no raw_json — matches what the
    # LLM classification code in llm/ expects (table filtered_records).
    schema = result_schema(groups)
    cols = [f for f in schema if f.name != "raw_json"]
    select = ", ".join(
        f"to_json({f.name})::VARCHAR AS {f.name}" if pa.types.is_list(f.type)
        else f"CAST({f.name} AS INTEGER) AS {f.name}" if pa.types.is_boolean(f.type)
        else f.name for f in cols)
    cur = con.execute(f"SELECT {select} FROM read_parquet('{out_parquet}')")
    sqlite_path = tmp / "filtered_records.sqlite"
    db = sqlite3.connect(sqlite_path)
    sql_type = {pa.int64(): "INTEGER", pa.float64(): "REAL", pa.bool_(): "INTEGER"}
    db.execute("CREATE TABLE filtered_records (" + ", ".join(
        f"{f.name} {sql_type.get(f.type, 'TEXT')}{' PRIMARY KEY' if f.name == 'openalex_id' else ''}"
        for f in cols) + ")")
    placeholders = ",".join("?" * len(cols))
    while chunk := cur.fetchmany(10_000):
        db.executemany(f"INSERT OR REPLACE INTO filtered_records VALUES ({placeholders})", chunk)
    db.commit()
    db.close()
    con.close()
    storage.upload(out_parquet, storage.join(root, "results.parquet"))
    storage.upload(sqlite_path, storage.join(root, "filtered_records.sqlite"))
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Summary + report
# ---------------------------------------------------------------------------

def summarize(stats: list[dict], groups: list[KeywordGroup], kw_hash: str, total: int,
              failures: list[dict], s2: dict, cfg: Config) -> dict:
    def merged(key: str, sub: str | None = None) -> Counter:
        c: Counter = Counter()
        for s in stats:
            c.update((s.get(key) or {}).get(sub, {}) if sub else (s.get(key) or {}))
        return c

    return {
        "stage": "stage3_keywords", "run_id": cfg.run_id, "snapshot_id": s2.get("snapshot_id"),
        "stage2_complete": bool(s2.get("complete")),
        "complete": bool(s2.get("complete")) and len(stats) == total and not failures,
        "partitions_done": len(stats), "partitions_total": total,
        "partitions_failed": [{"name": f["name"], "error": f["error"]} for f in failures],
        "keywords_sha256": kw_hash,
        "groups": [{"key": g.key, "name": g.name, "terms": len(g.terms)} for g in groups],
        "records_in": sum(s["records_in"] for s in stats),
        "matched": sum(s["matched"] for s in stats),
        "patterns": dict(merged("patterns").most_common()),
        "in_title": dict(merged("in_title")),
        "subgroup_pairs": dict(merged("subgroup_pairs").most_common()),
        "term_hits_all": {g.key: dict(merged("term_hits_all", g.key).most_common()) for g in groups},
        "term_hits_matched": {g.key: dict(merged("term_hits_matched", g.key).most_common()) for g in groups},
        "subgroup_hits_matched": {g.key: dict(merged("subgroup_hits_matched", g.key).most_common())
                                  for g in groups},
    }


def _term_regex(terms: list[str]) -> re.Pattern | None:
    parts = []
    for t in sorted(set(terms), key=len, reverse=True):
        p = r"[\s\-]?".join(re.escape(w) for w in re.split(r"[\s\-]+", t.strip()) if w)
        if p:
            parts.append(p + r"(?:e?s)?")
    return re.compile(r"(?<![\w])(" + "|".join(parts) + r")(?![\w])", re.I) if parts else None


def highlight(text: str, terms: list[str], limit: int | None = None) -> str:
    """HTML-escape text and <mark> the matched terms (approximate: ignores stemming)."""
    text = text or ""
    rx = _term_regex(terms)
    if limit and len(text) > limit:
        first = rx.search(text) if rx else None
        start = max(0, first.start() - limit // 3) if first else 0
        text = ("…" if start else "") + text[start:start + limit] + "…"
    if not rx:
        return html.escape(text)
    out, last = [], 0
    for m in rx.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        out.append(f"<mark>{html.escape(m.group(0))}</mark>")
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


def build_report(cfg: Config, s: dict, groups: list[KeywordGroup], root: str) -> str:
    r = Report(f"Stage 3 · Keyword matches · run {s['run_id']}",
               f"snapshot {s['snapshot_id']} · keywords {s['keywords_sha256'][:12]} · output {root}")
    n_in, matched = s["records_in"], s["matched"]
    keys = [g.key for g in groups]
    r.kpis([
        ("Journal articles scanned", n_in, f"{s['partitions_done']:,} of {s['partitions_total']:,} partitions"),
        ("Matched all groups", matched, pct(matched, n_in, 3) + " of scanned"),
        *[(f"Has {g.key} term", sum(v for k, v in s["patterns"].items() if g.key in k.split(" + ")),
           f"{len(g.terms)} terms") for g in groups],
        ("Duration", fmt_duration(s["duration_sec"]), "this invocation"),
    ])
    if not s["complete"]:
        r.section("Status")
        why = [] if s["stage2_complete"] else ["stage 2 has not finished all partitions"]
        if s["partitions_done"] < s["partitions_total"]:
            why.append(f"{s['partitions_total'] - s['partitions_done']:,} partitions pending")
        if s["partitions_failed"]:
            why.append(f"{len(s['partitions_failed'])} partitions failed")
        r.warning("Results are <b>incomplete</b>: " + "; ".join(why) + ".")

    r.section("Matching funnel", "A record is kept only when every keyword group has at least one term "
              "in its title or abstract.")
    steps, remaining = [], n_in
    for i, g in enumerate(groups):
        have = sum(v for k, v in s["patterns"].items()
                   if set(keys[:i + 1]) <= set(k.split(" + ")))
        steps.append((f"Has a term from {g.name}", remaining - have))
        remaining = have
    r.raw(r.funnel("Journal articles from stage 2", n_in, steps))
    r.grid(r.bars(list(s["patterns"].items()), "Which groups matched (near-misses included)", n_in),
           r.bars([(label, s["in_title"].get(k, 0)) for k, label in
                   (("all_groups", "Every group in title"), ("some_groups", "Some groups in title"),
                    ("abstract_only", "Abstract only"))], "Where the matches are", matched))

    if len(groups) >= 2:
        a, b = groups[0], groups[1]
        rows = [sg["name"] for sg in a.subgroups]
        cols = [sg["name"] for sg in b.subgroups]
        matrix = [[s["subgroup_pairs"].get(f"{x}|{y}", 0) for y in cols] for x in rows]
        r.section("Subgroup co-occurrence", f"Matched articles per pair of {a.name} × {b.name} subgroups "
                  "(an article can count in several cells).")
        r.raw(r.heatmap(rows, cols, matrix))

    for g in groups:
        all_hits = s["term_hits_all"].get(g.key, {})
        m_hits = s["term_hits_matched"].get(g.key, {})
        r.section(f"{g.name} — keyword diagnostics",
                  "‘Matched’ counts final matches; ‘any article’ counts every scanned article containing "
                  "the term, which shows how much each term would pull in by itself.")
        r.grid(r.bars(list(s["subgroup_hits_matched"].get(g.key, {}).items()), "Subgroups (matched)", matched),
               r.bars(list(m_hits.items())[:25], "Top terms (matched)", matched))
        rows = [[t, g.term_subgroup.get(t, ""), m_hits.get(t, 0), all_hits.get(t, 0)] for t in g.terms]
        rows.sort(key=lambda x: -x[2])
        r.raw(f"<details><summary>All {len(g.terms)} terms with counts</summary>"
              + r.table(["Term", "Subgroup", "Matched", "Any article"], rows, numeric={2, 3}) + "</details>")
        zero = [t for t in g.terms if not all_hits.get(t)]
        if zero:
            r.warning(f"{len(zero)} terms never occur in any scanned article — check spelling or variants: "
                      + ", ".join(html.escape(z) for z in zero))

    if matched:
        _result_sections(r, cfg, root, groups, matched)

    r.section("Explore the data")
    r.text("Matches: <code>results.parquet</code> (all columns, incl. raw_json) and "
           "<code>filtered_records.sqlite</code> (table filtered_records). Column reference:")
    docs = dict(COLUMN_DOCS) | {n: d for n, _, d in group_fields(groups)}
    r.raw(r.table(["Column", "Description"], [[k, v] for k, v in docs.items()]))
    r.code("python -m pipeline.explore sql \"SELECT title, matched_" + keys[0] + "_terms FROM stage3 LIMIT 20\"\n"
           "python -m pipeline.explore export --out exports/   # chunked CSVs + schema for Claude Desktop")
    if s["partitions_failed"]:
        r.raw(r.table(["Partition", "Error"], [[f["name"], f["error"]] for f in s["partitions_failed"]],
                      title="Failed partitions"))
    return r.to_html()


def _result_sections(r: Report, cfg: Config, root: str, groups: list[KeywordGroup], matched: int) -> None:
    con = duck.connect(cfg.storage_root, cfg.aws_region)
    con.execute(f"CREATE VIEW m AS SELECT * EXCLUDE (raw_json) FROM {duck.parquet_source(storage.join(root, 'parquet'))}")
    r.section("Matched articles")
    years = duck.rows(con, "SELECT publication_year, count(*) FROM m WHERE publication_year BETWEEN 1900 AND 2100 "
                           "GROUP BY 1 ORDER BY 1")
    r.raw(r.columns(years, "Publication year"))
    r.grid(
        r.bars(duck.pairs(con, "SELECT journal_name, count(*) n FROM m GROUP BY 1 ORDER BY n DESC LIMIT 20"),
               "Top journals", matched),
        r.bars(duck.pairs(con, "SELECT field, count(*) n FROM m GROUP BY 1 ORDER BY n DESC LIMIT 15"),
               "Field (primary topic)", matched),
        r.bars(duck.pairs(con, "SELECT c, count(*) n FROM (SELECT unnest(countries) c FROM m) "
                               "GROUP BY 1 ORDER BY n DESC LIMIT 15"), "Author countries (records)", matched),
        r.bars(duck.pairs(con, "SELECT oa_status, count(*) n FROM m GROUP BY 1 ORDER BY n DESC"),
               "Open-access status", matched),
    )
    top = duck.rows(con, "SELECT title, journal_name, publication_year::VARCHAR, cited_by_count, round(fwci, 2) "
                         "FROM m ORDER BY cited_by_count DESC NULLS LAST LIMIT 15")
    r.raw(r.table(["Title", "Journal", "Year", "Cited by", "FWCI"], [list(t) for t in top],
                  numeric={2, 3, 4}, title="Most cited matches"))

    n = cfg.stage3.sample_size
    term_cols = ", ".join(f"matched_{g.key}_terms" for g in groups)
    sample = duck.rows(con, f"SELECT openalex_id, doi, title, abstract_text, journal_name, publication_year, "
                            f"{term_cols} FROM m ORDER BY hash(openalex_id) LIMIT {n}")
    items = []
    for row in sample:
        oid, doi, title, abstract, journal, year, *terms = row
        all_terms = [t for ts in terms for t in (ts or [])]
        items.append({
            "title_html": highlight(title, all_terms),
            "url": f"https://doi.org/{doi}" if doi else None,
            "meta": f"{oid} · {journal or '—'} · {year or '—'}",
            "snippet_html": highlight(abstract or "", all_terms, limit=420),
            "tags": [(t, i % 2 == 1) for i, ts in enumerate(terms) for t in (ts or [])],
        })
    r.section(f"Random sample of {len(items)} matches",
              "Reproducible sample (ordered by a hash of the ID). Highlighting is approximate; "
              "matching itself uses stemmed tokens.")
    r.raw(r.records(items))
    con.close()


if __name__ == "__main__":
    main()
