from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

JOURNAL_SOURCE = {
    "id": "https://openalex.org/S100", "display_name": "Fuel", "issn_l": "0016-2361",
    "issn": ["0016-2361"], "is_oa": False, "is_in_doaj": False, "is_core": True,
    "listed_in": ["cwts-core", "norway-1"], "host_organization": "https://openalex.org/P1",
    "host_organization_name": "Elsevier BV", "type": "journal",
}
PUBMED_SOURCE = {
    "id": "https://openalex.org/S4306525036", "display_name": "PubMed", "issn_l": None, "issn": None,
    "is_oa": False, "is_in_doaj": False, "is_core": False, "listed_in": [],
    "host_organization_name": "National Institutes of Health", "type": "repository",
}


def inverted(text: str) -> dict:
    idx: dict = {}
    for i, w in enumerate(text.split()):
        idx.setdefault(w, []).append(i)
    return idx


def make_work(n: int, title: str = "A study", abstract: str | None = "Some abstract text here.",
              **overrides) -> dict:
    loc = {"id": f"10.1/x{n}", "source": copy.deepcopy(JOURNAL_SOURCE), "is_oa": False,
           "landing_page_url": f"https://doi.org/10.1/x{n}", "pdf_url": None, "license": None,
           "version": "publishedVersion", "is_accepted": True, "provenance": "crossref"}
    rec = {
        "id": f"https://openalex.org/W{n}", "doi": f"https://doi.org/10.1/X{n}",
        "title": title, "display_name": title, "publication_year": 2020, "publication_date": "2020-05-01",
        "language": "en", "type": "article", "is_paratext": False, "is_retracted": False, "is_xpac": False,
        "authorships": [{"author": {"display_name": "Ada Lovelace", "id": "https://openalex.org/A1"},
                         "countries": ["GB"], "institutions": [{"display_name": "Uni A", "country_code": "GB"}]}],
        "authors_count": 1,
        "primary_location": loc, "locations": [copy.deepcopy(loc)], "best_oa_location": {"id": None},
        "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None,
                        "any_repository_has_fulltext": False},
        "primary_topic": {"id": "https://openalex.org/T10001", "display_name": "Combustion",
                          "score": 0.99, "subfield": {"display_name": "Fuel Technology"},
                          "field": {"display_name": "Energy"}, "domain": {"display_name": "Physical Sciences"}},
        "biblio": {"volume": "1", "issue": "2", "first_page": "301", "last_page": "2"},
        "cited_by_count": 3, "fwci": 1.2, "referenced_works_count": 10,
        "citation_normalized_percentile": {"value": 0.8, "is_in_top_1_percent": False,
                                           "is_in_top_10_percent": False},
        "abstract_inverted_index": inverted(abstract) if abstract else None,
    }
    rec.update(overrides)
    return rec


def corpus() -> list[dict]:
    """Records covering every stage 2 rule and both keyword groups."""
    repo_loc = {"id": "1", "source": PUBMED_SOURCE, "version": None, "landing_page_url": "https://pubmed/1"}
    return [
        # 1-3 journal articles that match fuel AND impact
        make_work(1, "Biodiesel blends reduce NOx emissions", "We test biodiesel in a diesel engine."),
        make_work(2, "Sustainable aviation fuel", "Life cycle assessment of SAF shows lower GHG emissions."),
        make_work(3, "Engine study", "Bio-oil and PM2.5 particulate matter were measured.", type="review"),
        # 4 journal article, fuel only (near miss)
        make_work(4, "Kerosene markets", "Prices of kerosene in Asia."),
        # 5 journal article, no keywords
        make_work(5, "Hermeneutics of narrative", "A philosophical essay."),
        # 6 PubMed-only record (source is a repository)
        make_work(6, "Diesel exhaust and emissions", "diesel emissions",
                  primary_location=repo_loc, locations=[repo_loc]),
        # 7 no source at all (DataCite thesis)
        make_work(7, "Thesis on gasoline emissions", "gasoline emissions",
                  primary_location={"id": "x", "source": None}, locations=[]),
        # 8 book
        make_work(8, "Geomagnetic data", "data", type="book"),
        # 9 retracted, 10 paratext, 11 xpac, 12 no DOI, 13 not English, 14 no abstract
        make_work(9, "Diesel NOx", "diesel nox", is_retracted=True),
        make_work(10, "Front matter", "x", is_paratext=True),
        make_work(11, "Diesel GHG", "diesel ghg", is_xpac=True),
        make_work(12, "Diesel GHG", "diesel ghg", doi=None),
        make_work(13, "Diesel GHG", "diesel ghg", language="fi"),
        make_work(14, "Diesel GHG emissions", None),
    ]


KEYWORDS = {"keywords": {"groups": [
    {"key": "fuel", "name": "Fuel Terms", "subgroups": [
        {"name": "Diesel", "terms": ["diesel", "biodiesel", "biodiesel blend"]},
        {"name": "Jet", "terms": ["sustainable aviation fuel", "kerosene"]},
        {"name": "Bio", "terms": ["bio-oil", "pyrolysis oil"]},
        {"name": "Gasoline", "terms": ["gasoline"]}]},
    {"key": "impact", "name": "Impact Terms", "subgroups": [
        {"name": "Emissions", "terms": ["emissions", "NOx", "GHG", "PM2.5", "particulate matter"]},
        {"name": "Life Cycle", "terms": ["life cycle assessment", "LCA"]},
        {"name": "Never", "terms": ["zzzunusedterm"]}]},
]}, "columns": {}}


def write_partition(path: Path, records: list, raw_lines: list[bytes] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        for r in records:
            fh.write(json.dumps(r).encode() + b"\n")
        for line in raw_lines:
            fh.write(line + b"\n")


RELEASE = "2025-03-15"


def manifest(entity: str, files: list[tuple[str, int]]) -> str:
    """Per-entity manifest.json in the current OpenAlex layout."""
    return json.dumps({"date": RELEASE, "format": "jsonl", "entity": entity, "files": [
        {"url": f"s3://openalex/data/jsonl/{entity}/{rel}", "meta": {"content_length": 1, "record_count": n}}
        for rel, n in files]})


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """A local stand-in for s3://openalex/data/jsonl (works, sources, manifests)."""
    src = tmp_path / "openalex" / "data" / "jsonl"
    recs = corpus()
    write_partition(src / "works" / "updated_date=2025-01-01" / "part_0000.gz", recs[:7], raw_lines=[b"{not json"])
    write_partition(src / "works" / "updated_date=2025-02-01" / "part_0000.gz", recs[7:])
    write_partition(src / "works" / "deleted_ids.csv.gz", [])          # side file: must be ignored
    (src / "works" / "manifest.json").write_text(manifest("works", [
        ("updated_date=2025-01-01/part_0000.gz", 8), ("updated_date=2025-02-01/part_0000.gz", len(recs) - 7)]))
    write_partition(src / "sources" / "updated_date=2025-01-01" / "part_0000.gz", [
        {**JOURNAL_SOURCE, "works_count": 1000, "cited_by_count": 50000,
         "summary_stats": {"2yr_mean_citedness": 6.5, "h_index": 200, "i10_index": 900},
         "country_code": "GB", "apc_usd": 3000, "topics": [{"field": {"display_name": "Energy"}}]},
        PUBMED_SOURCE,
    ])
    (src / "sources" / "manifest.json").write_text(manifest("sources", [("updated_date=2025-01-01/part_0000.gz", 2)]))
    (src / "manifest.json").write_text(json.dumps({"date": RELEASE, "format": "jsonl", "entities": []}))
    return src


@pytest.fixture
def workspace(tmp_path: Path, source_dir: Path) -> dict:
    kw = tmp_path / "keywords.json"
    kw.write_text(json.dumps(KEYWORDS))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"storage_root: {tmp_path / 'store'}\nrun_id: test\nworkers: 1\ntmp_dir: {tmp_path / 'tmp'}\n"
        f"stage1:\n  source: {source_dir}\n  copy_concurrency: 2\n"
        f"stage3:\n  keywords_file: {kw}\n  sample_size: 5\n")
    return {"config": cfg, "store": tmp_path / "store", "keywords": kw, "tmp": tmp_path}
