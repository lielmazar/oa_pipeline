"""Explore a run's outputs with SQL (DuckDB), locally or straight from S3.

Views available in every query:
    stage2            journal articles kept by stage 2 (all partitions)
    journals          journal metrics from the OpenAlex `sources` entity
    stage3            keyword matches (alias: filtered_records)

    python -m pipeline.explore tables
    python -m pipeline.explore sql "SELECT journal_name, count(*) n FROM stage3 GROUP BY 1 ORDER BY n DESC"
    python -m pipeline.explore sql "SELECT * FROM stage3 WHERE fuel_in_title" --out matches.csv
    python -m pipeline.explore sample --stage 3 -n 5
    python -m pipeline.explore shell
    python -m pipeline.explore report 3                # download report.html to ./reports/
    python -m pipeline.explore export --out exports/   # chunked CSVs + schema for Claude Desktop
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

from pipeline.lib import duck, layout, storage
from pipeline.lib.config import Config, add_common_args, config_from_args
from pipeline.lib.records import COLUMN_DOCS

CSV_CHUNK_BYTES = 25 * 1024 * 1024   # stays under Claude Desktop's 30 MB upload limit


def open_views(cfg: Config):
    con = duck.connect(cfg.storage_root, cfg.aws_region)
    s2 = layout.stage_root(cfg, "stage2")
    s3 = layout.stage_root(cfg, "stage3")
    created = []
    if storage.list_files(storage.join(s2, "parquet"), suffix=".parquet"):
        con.execute(f"CREATE VIEW stage2 AS SELECT * FROM {duck.parquet_source(storage.join(s2, 'parquet'))}")
        created.append("stage2")
    if storage.exists(storage.join(s2, "journals.parquet")):
        con.execute(f"CREATE VIEW journals AS SELECT * FROM read_parquet('{storage.join(s2, 'journals.parquet')}')")
        created.append("journals")
    results = storage.join(s3, "results.parquet")
    if storage.exists(results):
        con.execute(f"CREATE VIEW stage3 AS SELECT * FROM read_parquet('{results}')")
    elif storage.list_files(storage.join(s3, "parquet"), suffix=".parquet"):
        con.execute(f"CREATE VIEW stage3 AS SELECT * FROM {duck.parquet_source(storage.join(s3, 'parquet'))}")
    if "stage3" not in created and _has_view(con, "stage3"):
        con.execute("CREATE VIEW filtered_records AS SELECT * FROM stage3")
        created.append("stage3")
    if not created:
        raise SystemExit(f"No outputs found for run {cfg.run_id!r} under {layout.run_root(cfg)}")
    return con, created


def _has_view(con, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM duckdb_views() WHERE view_name = ?", [name]).fetchall())


def cmd_tables(con, views: list[str]) -> None:
    for v in views:
        n = con.execute(f"SELECT count(*) FROM {v}").fetchone()[0]
        cols = con.execute(f"DESCRIBE {v}").fetchall()
        print(f"\n{v}  ({n:,} rows)")
        for name, typ, *_ in cols:
            print(f"  {name:<34} {typ:<16} {COLUMN_DOCS.get(name, '')}")


def cmd_sql(con, query: str, out: Path | None, limit: int) -> None:
    rel = con.sql(query)
    if out is None:
        rel.show(max_rows=limit, max_width=200)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = "parquet" if out.suffix == ".parquet" else "json" if out.suffix in (".json", ".jsonl") else "csv"
    if fmt == "csv":
        # CSV can't hold lists; serialise them as JSON text.
        cols = [f"to_json({c})::VARCHAR AS {c}" if "[]" in t else c for c, t in zip(rel.columns, map(str, rel.types))]
        rel = con.sql(f"SELECT {', '.join(cols)} FROM ({query})")
    rel.write_csv(str(out)) if fmt == "csv" else rel.write_parquet(str(out)) if fmt == "parquet" \
        else con.execute(f"COPY ({query}) TO '{out}' (FORMAT json)")
    print(f"Wrote {out}")


def cmd_shell(con, views: list[str]) -> None:
    print(f"DuckDB shell · views: {', '.join(views)} · end statements with ';' · Ctrl-D to quit")
    buf = ""
    while True:
        try:
            line = input("... " if buf else "sql> ")
        except EOFError:
            print()
            return
        buf += line + "\n"
        if buf.strip().endswith(";"):
            try:
                con.sql(buf).show(max_rows=50, max_width=200)
            except Exception as e:  # noqa: BLE001 — interactive: report and continue
                print(f"error: {e}")
            buf = ""


def cmd_sample(con, stage: int, n: int) -> None:
    view = f"stage{stage}"
    cols = [c for c, in con.execute(f"SELECT column_name FROM (DESCRIBE {view})").fetchall()]
    term_cols = [c for c in cols if c.startswith("matched_") and c.endswith("_terms")]
    rows = con.execute(f"SELECT openalex_id, doi, title, journal_name, publication_year, abstract_text"
                       f"{''.join(', ' + c for c in term_cols)} FROM {view} USING SAMPLE {n} ROWS").fetchall()
    for oid, doi, title, journal, year, abstract, *terms in rows:
        print(f"\n{oid}  https://doi.org/{doi}\n  {title}\n  {journal} ({year})")
        for c, t in zip(term_cols, terms):
            print(f"  {c}: {', '.join(t or [])}")
        print("  " + (abstract or "")[:400].replace("\n", " ") + ("…" if abstract and len(abstract) > 400 else ""))


def cmd_report(cfg: Config, stage: int, out_dir: Path) -> None:
    if stage == 1:
        sid = layout.resolve_snapshot(cfg)
        uri = storage.join(layout.snapshot_root(cfg, sid), "stage1_report.html")
    else:
        uri = storage.join(layout.stage_root(cfg, f"stage{stage}"), "report.html")
    dest = out_dir / cfg.run_id / f"stage{stage}_report.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(storage.read_bytes(uri))
    print(f"Saved {dest.resolve()}")


def cmd_export(cfg: Config, con, out_dir: Path) -> None:
    """Chunked CSVs (≤25 MB each) + research_schema.json + schema.csv, also zipped."""
    if not _has_view(con, "stage3"):
        raise SystemExit("No stage 3 results to export.")
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = con.sql("SELECT * EXCLUDE (raw_json) FROM stage3")
    cols = [f"to_json({c})::VARCHAR AS {c}" if "[]" in str(t) else c for c, t in zip(rel.columns, rel.types)]
    cur = con.execute(f"SELECT {', '.join(cols)} FROM stage3")
    header = [d[0] for d in cur.description]

    files: list[Path] = []
    part, buf, writer = 0, None, None

    def flush() -> None:
        if buf is not None and buf.tell():
            path = out_dir / f"records_part{part}.csv"
            path.write_text(buf.getvalue(), encoding="utf-8")
            files.append(path)

    while chunk := cur.fetchmany(5_000):
        for row in chunk:
            if buf is None or buf.tell() > CSV_CHUNK_BYTES:
                flush()
                part += 1
                buf = io.StringIO()
                writer = csv.writer(buf, lineterminator="\n")
                writer.writerow(header)
            writer.writerow(row)
    flush()

    schema_src = cfg.resolve_path(cfg.stage3.keywords_file)
    schema = json.loads(schema_src.read_text(encoding="utf-8")) if schema_src.exists() else {}
    kw_uri = storage.join(layout.stage_root(cfg, "stage3"), "keywords.json")
    if storage.exists(kw_uri):
        schema["keywords"] = storage.read_json(kw_uri)["keywords"]   # keywords actually used
    (out_dir / "research_schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    docs = schema.get("columns", {})
    with (out_dir / "schema.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["column", "description"])
        for c in header:
            w.writerow([c, (docs.get(c) or {}).get("description") or COLUMN_DOCS.get(c, "")])
    zip_path = out_dir / f"{cfg.run_id}_export.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in [*files, out_dir / "research_schema.json", out_dir / "schema.csv"]:
            zf.write(p, p.name)
    print(f"Wrote {len(files)} CSV part(s), schema files and {zip_path}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tables", help="List views, row counts and columns")
    q = sub.add_parser("sql", help="Run a query")
    q.add_argument("query")
    q.add_argument("--out", type=Path, help="Write result to .csv, .parquet or .json instead of printing")
    q.add_argument("--limit", type=int, default=40, help="Rows to print (default 40)")
    sub.add_parser("shell", help="Interactive SQL prompt")
    s = sub.add_parser("sample", help="Print random records")
    s.add_argument("--stage", type=int, choices=[2, 3], default=3)
    s.add_argument("-n", type=int, default=5)
    r = sub.add_parser("report", help="Download a stage report (1, 2 or 3)")
    r.add_argument("stage", type=int, choices=[1, 2, 3])
    r.add_argument("--out", type=Path, default=Path("reports"))
    e = sub.add_parser("export", help="Chunked CSV export of stage 3 for Claude Desktop")
    e.add_argument("--out", type=Path, default=Path("exports"))
    args = p.parse_args(argv)
    cfg = config_from_args(args)

    if args.cmd == "report":
        return cmd_report(cfg, args.stage, args.out)
    con, views = open_views(cfg)
    if args.cmd == "tables":
        cmd_tables(con, views)
    elif args.cmd == "sql":
        cmd_sql(con, args.query, args.out, args.limit)
    elif args.cmd == "shell":
        cmd_shell(con, views)
    elif args.cmd == "sample":
        cmd_sample(con, args.stage, args.n)
    elif args.cmd == "export":
        cmd_export(cfg, con, args.out)


if __name__ == "__main__":
    sys.exit(main())
