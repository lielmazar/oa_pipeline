"""End-to-end run of all three stages against a local storage root."""
from __future__ import annotations

import json
import sqlite3

import duckdb
import pytest

from pipeline import explore, stage1_mirror, stage2_journals, stage3_keywords


def args(ws, *extra):
    return ["--config", str(ws["config"]), *extra]


def test_full_pipeline(workspace, capsys):
    ws = workspace
    s1 = stage1_mirror.main(args(ws))
    assert s1["snapshot_id"] == "2025-03-15"                     # OpenAlex release date
    assert s1["copied"] == 3 and s1["failed"] == 0 and s1["complete"]   # deleted_ids.csv.gz ignored
    snap = ws["store"] / "snapshots" / "2025-03-15"
    manifest = json.loads((snap / "snapshot_manifest.json").read_text())
    assert manifest["entities"]["works"]["total_records"] == 15
    assert manifest["openalex_release"] == "2025-03-15"
    assert (snap / "data" / "manifest.json").exists() and (snap / "data" / "works" / "manifest.json").exists()
    assert (snap / "stage1_report.html").read_text().count("Stage 1") >= 1

    # Re-running skips everything already copied.
    assert stage1_mirror.main(args(ws))["skipped_existing"] == 3

    s2 = stage2_journals.main(args(ws))
    assert s2["records_in"] == 15 and s2["kept"] == 5 and s2["complete"]
    assert s2["drops"] == {"json_error": 1, "source_not_journal": 1, "no_source": 1, "wrong_type": 1,
                           "retracted": 1, "paratext": 1, "xpac": 1, "no_doi": 1, "language": 1,
                           "no_abstract": 1}
    run = ws["store"] / "runs" / "test"
    html2 = (run / "stage2" / "report.html").read_text()
    assert "Selection funnel" in html2 and "Fuel" in html2 and "6.50" in html2   # journal metric joined
    kept = duckdb.sql(f"SELECT openalex_id, raw_json IS NOT NULL FROM read_parquet('{run}/stage2/parquet/*.parquet') "
                      "ORDER BY 1").fetchall()
    assert [k[0] for k in kept] == ["W1", "W2", "W3", "W4", "W5"] and all(k[1] for k in kept)

    s3 = stage3_keywords.main(args(ws))
    assert s3["records_in"] == 5 and s3["matched"] == 3 and s3["complete"]
    assert s3["patterns"] == {"fuel + impact": 3, "fuel": 1, "none": 1}
    assert "zzzunusedterm" not in s3["term_hits_all"]["impact"]
    html3 = (run / "stage3" / "report.html").read_text()
    assert "never occur" in html3 and "zzzunusedterm" in html3 and "<mark>" in html3

    db = sqlite3.connect(run / "stage3" / "filtered_records.sqlite")
    rows = db.execute("SELECT openalex_id, matched_fuel_terms, fuel_in_title, is_oa FROM filtered_records "
                      "ORDER BY openalex_id").fetchall()
    assert [r[0] for r in rows] == ["W1", "W2", "W3"]
    assert json.loads(rows[0][1]) == ["biodiesel", "biodiesel blend", "diesel"]
    assert rows[0][2] == 1 and rows[2][2] == 0 and rows[0][3] == 0

    # Resume: nothing left to do, same totals.
    assert stage2_journals.main(args(ws))["kept"] == 5
    assert stage3_keywords.main(args(ws))["matched"] == 3

    # Explore
    explore.main(args(ws, "sql", "SELECT count(*) AS n FROM stage3"))
    assert "3" in capsys.readouterr().out
    out_csv = ws["tmp"] / "m.csv"
    explore.main(args(ws, "sql", "SELECT openalex_id, matched_impact_terms FROM stage3", "--out", str(out_csv)))
    assert "NOx" in out_csv.read_text()
    explore.main(args(ws, "tables"))
    explore.main(args(ws, "export", "--out", str(ws["tmp"] / "exp")))
    assert (ws["tmp"] / "exp" / "records_part1.csv").exists()
    assert (ws["tmp"] / "exp" / "test_export.zip").exists()
    explore.main(args(ws, "report", "3", "--out", str(ws["tmp"] / "rep")))
    assert (ws["tmp"] / "rep" / "test" / "stage3_report.html").exists()


def test_changed_stage2_rules_require_new_run(workspace):
    ws = workspace
    stage1_mirror.main(args(ws))
    stage2_journals.main(args(ws, "--max-partitions", "1"))
    cfg = ws["config"]
    cfg.write_text(cfg.read_text() + "stage2:\n  languages: []\n")
    with pytest.raises(SystemExit, match="different snapshot or stage2 settings"):
        stage2_journals.main(args(ws))
    s2 = stage2_journals.main(args(ws, "--restart"))
    assert s2["drops"].get("language", 0) == 0 and s2["kept"] == 6


def test_missing_release_manifest_marks_snapshot_partial(workspace, source_dir):
    (source_dir / "manifest.json").unlink()
    s1 = stage1_mirror.main(args(workspace))
    assert not s1["complete"] and s1["snapshot_id"] == "2025-03-15"   # date still from entity manifest


def test_partial_run_is_flagged(workspace):
    ws = workspace
    stage1_mirror.main(args(ws, "--max-files", "1"))
    s2 = stage2_journals.main(args(ws))
    assert not s2["complete"] and s2["partitions_total"] == 1
    assert "incomplete" in (ws["store"] / "runs" / "test" / "stage2" / "report.html").read_text()


def test_keyword_change_requires_restart(workspace):
    ws = workspace
    stage1_mirror.main(args(ws))
    stage2_journals.main(args(ws))
    stage3_keywords.main(args(ws))
    kw = json.loads(ws["keywords"].read_text())
    kw["keywords"]["groups"][0]["subgroups"][0]["terms"].append("kerosene")
    ws["keywords"].write_text(json.dumps(kw))
    with pytest.raises(SystemExit, match="Keywords changed"):
        stage3_keywords.main(args(ws))
    assert stage3_keywords.main(args(ws, "--restart"))["matched"] == 3
