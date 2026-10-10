from __future__ import annotations

import json
from pathlib import Path

from pipeline.lib.config import Stage2Config
from pipeline.lib.keywords import load_keyword_groups, match_groups, normalize_and_stem
from pipeline.lib.layout import partition_name, updated_date
from pipeline.lib.records import invert_abstract, journal_location, screen, work_row
from tests.conftest import KEYWORDS, PUBMED_SOURCE, corpus, make_work


def groups(tmp_path: Path):
    p = tmp_path / "kw.json"
    p.write_text(json.dumps(KEYWORDS))
    return load_keyword_groups(p)[0]


def test_normalisation_matches_legacy_rules():
    assert normalize_and_stem("Emissions of NOx") == ["emission", "of", "nox"]
    assert normalize_and_stem("PM2.5") == ["pm25"]
    assert normalize_and_stem("bio-oil") == ["bio", "oil"]
    assert normalize_and_stem("CO₂ studies") == ["co2", "study"]


def test_match_requires_every_group_and_tracks_provenance(tmp_path):
    g = groups(tmp_path)
    fuel, impact = match_groups(g, "Biodiesel blends reduce NOx emissions", "We test diesel engines.")
    assert fuel.hit and impact.hit
    assert set(fuel.terms) == {"biodiesel", "biodiesel blend", "diesel"}
    assert fuel.in_title and impact.in_title
    assert impact.subgroups == ["Emissions"]

    fuel, impact = match_groups(g, "Kerosene markets", "Prices of kerosene.")
    assert fuel.hit and not impact.hit

    # squished multi-word variant and digit-dot handling
    fuel, impact = match_groups(g, "Engine", "Biooil and PM2.5 were measured")
    assert fuel.terms == ["bio-oil"] and impact.terms == ["PM2.5"]
    assert not fuel.in_title


def test_phrase_does_not_span_title_and_abstract(tmp_path):
    g = groups(tmp_path)
    fuel, _ = match_groups(g, "pyrolysis", "oil yields")
    assert not fuel.hit


def test_invert_abstract_orders_positions():
    assert invert_abstract({"world": [1], "hello": [0, 2]}) == "hello world hello"
    assert invert_abstract(None) == ""


def test_screen_rules():
    crit = Stage2Config()
    reasons = [screen(r, crit)[0] for r in corpus()]
    assert reasons == [None, None, None, None, None, "source_not_journal", "no_source", "wrong_type",
                       "retracted", "paratext", "xpac", "no_doi", "language", "no_abstract"]
    unknown = make_work(99, language=None)
    assert screen(unknown, crit)[0] == "language_unknown"
    assert screen(unknown, Stage2Config(allow_unknown_language=True))[0] is None


def test_location_fallback_prefers_published_journal_version():
    journal = make_work(1)["primary_location"]
    rec = make_work(1, primary_location={"source": PUBMED_SOURCE},
                    locations=[{"source": PUBMED_SOURCE}, {**journal, "version": "acceptedVersion"}, journal])
    assert journal_location(rec, allow_fallback=False)[2] == "source_not_journal"
    loc, fallback, reason = journal_location(rec, allow_fallback=True)
    assert reason is None and fallback and loc["version"] == "publishedVersion"


def test_work_row_flattens_fields():
    rec = make_work(42, "T", "a b")
    loc = rec["primary_location"]
    row = work_row(rec, loc, False, b"{}", store_raw=False)
    assert row["openalex_id"] == "W42" and row["doi"] == "10.1/x42"
    assert row["journal_id"] == "S100" and row["journal_issn_l"] == "0016-2361"
    assert row["countries"] == ["GB"] and row["institutions"] == ["Uni A"]
    assert row["abstract_text"] == "a b" and row["raw_json"] is None
    assert row["field"] == "Energy"


def test_partition_naming():
    key = "works/updated_date=2025-01-01/part_003.gz"
    assert partition_name(key) == "updated_date=2025-01-01__part_003"
    assert updated_date(key) == "2025-01-01"
