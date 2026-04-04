"""
pipeline.py — OpenAlex JSONL filtering and parallel pipeline
=============================================================
Filter OpenAlex snapshot (.gz JSONL) records by fuel and impact keyword
co-occurrence and write matched records to a SQLite database.

Two modes (select via first positional argument):

  filter   Process a single .gz partition file (default: part_0009.gz).
             python pipeline.py filter --input part_0009.gz --output-db out.sqlite

  run      Parallel pipeline over all .gz files in a local directory or S3.
             python pipeline.py run --local-dir ~/data/openalex/works --workers 8

Run mode is the default when no mode argument is given, enabling direct use
from the dashboard without any argument changes:
             python pipeline.py --workers 8 --local-dir ./works ...

Output columns per matched record include provenance lists of the exact
fuel/impact terms and stemmed token tuples that triggered each match, making
every inclusion decision fully auditable.

Keyword terms are loaded from a JSON config file (--keyword-config) when
provided, or fall back to the built-in example term lists below.  The JSON
format is:
  {
    "groups": [
      { "name": "Fuel", "subgroups": [{ "name": "Bio", "terms": [...] }] },
      { "name": "Impact", "subgroups": [{ "name": "Emissions", "terms": [...] }] }
    ]
  }
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import multiprocessing
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
import unicodedata
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# Enable ANSI escape sequences on Windows 10+
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    os.system("")

# ---------------------------------------------------------------------------
# Fast JSON — prefer orjson, fall back to stdlib
# ---------------------------------------------------------------------------
try:
    import orjson as _orjson

    def _loads(raw: str | bytes):
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return _orjson.loads(raw)

    _JSONDecodeError = _orjson.JSONDecodeError
except ImportError:
    _loads = json.loads
    _JSONDecodeError = json.JSONDecodeError

# ---------------------------------------------------------------------------
# Optional boto3 — only needed for S3 mode
# ---------------------------------------------------------------------------
_HAS_BOTO3 = False
try:
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config as BotocoreConfig

    _HAS_BOTO3 = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths and S3 constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent

DEFAULT_STATE_FILE = SCRIPT_DIR / "pipeline_state.json"
DEFAULT_LOG_FILE   = SCRIPT_DIR / "pipeline.log"
DEFAULT_OUTPUT_DB  = SCRIPT_DIR / "openalex_works_filtered.sqlite"

S3_BUCKET = "openalex"
S3_REGION = "us-east-1"
S3_PREFIX = "data/works/"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("pipeline")


def _setup_logging(log_file: Path) -> None:
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


# ============================================================================
# CONFIGURATION DEFAULTS
# These built-in term lists are used when --keyword-config is not provided.
# Supply your own via --keyword-config for domain-specific filtering.
# ============================================================================

FUEL_TERMS: list[str] = [
    # ── Sustainable / Bio / Renewable ──
    "sustainable fuel", "biofuel", "renewable diesel", "synthetic fuel",
    "e-fuel", "sustainable aviation fuel", "biodiesel", "biomass-to-liquid",
    "BtL", "power-to-liquid", "green fuel", "renewable jet fuel",
    "bio-jet fuel", "bio-aviation fuel", "drop-in fuel", "paraffinic fuel",
    "Fischer-Tropsch fuel", "Fischer-Tropsch diesel", "FT fuel", "FT diesel",
    "hydroprocessed fuel", "co-processed fuel", "co-processing",
    "renewable fuel", "alternative fuel", "second generation fuel",
    "second-generation fuel", "advanced biofuel", "cellulosic biofuel",
    "algae fuel", "microalgae fuel", "pyrolysis oil", "bio-oil", "bio-crude",
    "hydrothermal liquefaction", "HTL fuel",
    # ── Conventional hydrocarbon fuels ──
    "jet fuel", "jet a1", "jet a-1", "kerosene", "petrodiesel", "gas oil",
    "gasoline", "marine fuel", "marine diesel", "bunker fuel", "naphtha",
    "gas-to-liquid", "GTL", "jp-8", "jp 8", "diesel",
    # ── Blends ──
    "biodiesel blend", "ethanol blend", "diesel blend", "fuel blend",
    # ── Feedstocks ──
    "waste cooking oil", "used cooking oil", "UCO", "jatropha",
    "palm oil methyl ester", "rapeseed methyl ester", "soybean methyl ester",
    "tallow", "animal fat", "vegetable oil", "fatty acid methyl ester",
    "hydrotreated vegetable oil", "HVO", "FAME",
    "Hydroprocessed Esters and Fatty Acids", "HEFA", "hydroprocessed esters",
    # ── Fuel standards ──
    "def stan 91-091", "ASTM D7566", "ASTM D975", "ASTM D1655",
    "EN 590", "EN 14214", "EN 228",
]

IMPACT_TERMS: list[str] = [
    # ── Emissions ──
    "GHG", "CO2", "greenhouse gas", "emissions", "NOx", "SOx", "soot",
    "particulate matter", "PM2.5", "PM10", "black carbon", "carbon intensity",
    "carbon footprint", "exhaust emissions", "tailpipe emissions",
    "unburned hydrocarbons", "HC emissions", "CO emissions", "carbon monoxide",
    "smoke opacity", "smoke number",
    # ── Performance & efficiency ──
    "thermal efficiency", "brake thermal efficiency", "BTE",
    "specific fuel consumption", "BSFC", "energy density",
    "combustion efficiency", "engine performance", "brake power", "torque",
    "power output",
    # ── Combustion properties ──
    "ignition delay", "cetane number", "cetane index", "octane number",
    "heat release rate", "cylinder pressure", "combustion characteristics",
    "combustion duration", "spray characteristics", "injection pressure",
    "flame temperature", "auto-ignition",
    # ── Fuel physical properties ──
    "flash point", "cloud point", "pour point", "cold filter plugging point",
    "CFPP", "viscosity", "kinematic viscosity", "density", "calorific value",
    "heating value", "lower heating value", "higher heating value",
    "volatility", "distillation curve", "boiling point", "lubricity",
    "oxidation stability", "cold flow", "freezing point", "thermal stability",
    "storage stability", "Chemometrics",
    # ── Lifecycle ──
    "lifecycle", "life cycle", "well-to-wheel", "well-to-wake",
    "well-to-tank", "tank-to-wheel", "LCA", "life cycle assessment",
]


# ============================================================================
# TEXT NORMALISATION AND STEMMING
# ============================================================================

_UNICODE_REMAP = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉ₓ⁰¹²³⁴⁵⁶⁷⁸⁹",
    "0123456789x0123456789",
)
_RE_DIGIT_DOT = re.compile(r"(?<=[0-9])\.(?=[0-9])")
_RE_SEPARATOR  = re.compile(r"[-_/]")
_RE_TOKENS     = re.compile(r"[a-z][a-z0-9]*|[0-9]+")


def normalize_text(text: str) -> str:
    text = text.translate(_UNICODE_REMAP)
    if not text.isascii():
        text = unicodedata.normalize("NFKD", text)
        text = text.encode("ascii", errors="ignore").decode("ascii")
    text = text.lower()
    text = _RE_DIGIT_DOT.sub("", text)
    text = _RE_SEPARATOR.sub(" ", text)
    return text


def tokenize(text: str) -> list[str]:
    return _RE_TOKENS.findall(text)


def stem_token(token: str) -> str:
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def normalize_and_stem(text: str) -> list[str]:
    return [stem_token(t) for t in tokenize(normalize_text(text))]


# ============================================================================
# TERM INDEX WITH REVERSE MAPPING
# ============================================================================

def _index_add(index: dict, token_tuple: tuple) -> None:
    bucket = index.setdefault(token_tuple[0], [])
    if token_tuple not in bucket:
        bucket.append(token_tuple)


def compile_terms(
    terms: list[str],
) -> tuple[dict[str, list[tuple[str, ...]]], dict[tuple[str, ...], str]]:
    """Build a lookup index and a reverse map (stemmed tuple → original term)."""
    index: dict[str, list[tuple[str, ...]]] = {}
    reverse: dict[tuple[str, ...], str] = {}
    for term in terms:
        tokens = normalize_and_stem(term)
        if not tokens:
            continue
        phrase = tuple(tokens)
        _index_add(index, phrase)
        if phrase not in reverse:
            reverse[phrase] = term
        if len(phrase) > 1:
            squished = ("".join(tokens),)
            _index_add(index, squished)
            if squished not in reverse:
                reverse[squished] = term
    return index, reverse


def contains_any_term(
    stemmed_tokens: list[str], index: dict[str, list[tuple[str, ...]]]
) -> bool:
    n = len(stemmed_tokens)
    for i, token in enumerate(stemmed_tokens):
        for phrase in index.get(token, []):
            plen = len(phrase)
            if i + plen <= n and all(
                stemmed_tokens[i + j] == phrase[j] for j in range(plen)
            ):
                return True
    return False


def find_all_matching_terms(
    stemmed_tokens: list[str],
    index: dict[str, list[tuple[str, ...]]],
    reverse: dict[tuple[str, ...], str],
) -> tuple[list[str], list[str]]:
    """Return (original_terms, stem_strings) for all matches in stemmed_tokens."""
    n = len(stemmed_tokens)
    seen_phrases: set[tuple[str, ...]] = set()
    originals: list[str] = []
    stems: list[str] = []
    for i, token in enumerate(stemmed_tokens):
        for phrase in index.get(token, []):
            if phrase in seen_phrases:
                continue
            plen = len(phrase)
            if i + plen <= n and all(
                stemmed_tokens[i + j] == phrase[j] for j in range(plen)
            ):
                seen_phrases.add(phrase)
                orig = reverse.get(phrase, " ".join(phrase))
                if orig not in originals:
                    originals.append(orig)
                stem_str = " ".join(phrase)
                if stem_str not in stems:
                    stems.append(stem_str)
    return originals, stems


# ---------------------------------------------------------------------------
# Lazy term initialisation (supports per-pipeline keyword configs)
# ---------------------------------------------------------------------------
FUEL_INDEX: dict   = {}
FUEL_REVERSE: dict = {}
IMPACT_INDEX: dict   = {}
IMPACT_REVERSE: dict = {}
_FUEL_FIRST_TOKENS: frozenset   = frozenset()
_IMPACT_FIRST_TOKENS: frozenset = frozenset()
_terms_initialized = False

FUEL_SUBGROUP_MAP: dict[str, str]   = {}
IMPACT_SUBGROUP_MAP: dict[str, str] = {}


def _init_terms(
    fuel_terms: list[str] | None = None,
    impact_terms: list[str] | None = None,
    fuel_subgroups: list[dict] | None = None,
    impact_subgroups: list[dict] | None = None,
) -> None:
    """Compile keyword indices.  Call once before processing; can be re-called
    to switch keyword configurations between runs."""
    global FUEL_INDEX, FUEL_REVERSE, IMPACT_INDEX, IMPACT_REVERSE
    global _FUEL_FIRST_TOKENS, _IMPACT_FIRST_TOKENS, _terms_initialized
    global FUEL_SUBGROUP_MAP, IMPACT_SUBGROUP_MAP

    ft = fuel_terms   if fuel_terms   is not None else FUEL_TERMS
    it = impact_terms if impact_terms is not None else IMPACT_TERMS
    FUEL_INDEX,   FUEL_REVERSE   = compile_terms(ft)
    IMPACT_INDEX, IMPACT_REVERSE = compile_terms(it)
    _FUEL_FIRST_TOKENS   = frozenset(FUEL_INDEX.keys())
    _IMPACT_FIRST_TOKENS = frozenset(IMPACT_INDEX.keys())
    _terms_initialized   = True

    FUEL_SUBGROUP_MAP = (
        {term: sg["name"] for sg in fuel_subgroups for term in sg.get("terms", [])}
        if fuel_subgroups else {}
    )
    IMPACT_SUBGROUP_MAP = (
        {term: sg["name"] for sg in impact_subgroups for term in sg.get("terms", [])}
        if impact_subgroups else {}
    )


def _flatten_group_terms(group: dict) -> list[str]:
    """Flatten a keyword group dict to a flat list of terms."""
    if "subgroups" in group:
        return [t for sg in group["subgroups"] for t in sg.get("terms", [])]
    return group.get("terms", [])


def _extract_group_subgroups(group: dict) -> list[dict] | None:
    """Extract [{name, terms}] subgroup list from a keyword group dict."""
    if "subgroups" in group:
        return [{"name": sg["name"], "terms": sg.get("terms", [])}
                for sg in group["subgroups"]]
    return None


def _load_keyword_config(config_path: str | Path | None) -> None:
    """Load and initialise keyword indices from a JSON config file.
    Falls back to built-in defaults when config_path is None."""
    if not config_path:
        _init_terms()
        return
    cfg  = json.loads(Path(config_path).read_text(encoding="utf-8"))
    grps = cfg.get("groups", [])
    ft   = _flatten_group_terms(grps[0]) if grps       else cfg.get("fuel_terms")
    it   = _flatten_group_terms(grps[1]) if len(grps) > 1 else cfg.get("impact_terms")
    fsg  = _extract_group_subgroups(grps[0]) if grps       else None
    isg  = _extract_group_subgroups(grps[1]) if len(grps) > 1 else None
    _init_terms(ft, it, fsg, isg)


def _compute_subgroups(
    matched_terms: list[str], subgroup_map: dict[str, str]
) -> list[str]:
    """Return deduplicated list of subgroup names for the given matched terms."""
    seen: list[str] = []
    for term in matched_terms:
        sg = subgroup_map.get(term)
        if sg and sg not in seen:
            seen.append(sg)
    return seen


# ============================================================================
# KEYWORD MATCHING
# ============================================================================

class MatchResult:
    """Container for keyword match results including term provenance."""

    __slots__ = (
        "matched", "abstract_text",
        "fuel_terms", "fuel_stems",
        "impact_terms", "impact_stems",
        "fuel_subgroups", "impact_subgroups",
    )

    def __init__(
        self,
        matched: bool = False,
        abstract_text: str = "",
        fuel_terms: list[str] | None = None,
        fuel_stems: list[str] | None = None,
        impact_terms: list[str] | None = None,
        impact_stems: list[str] | None = None,
        fuel_subgroups: list[str] | None = None,
        impact_subgroups: list[str] | None = None,
    ):
        self.matched        = matched
        self.abstract_text  = abstract_text
        self.fuel_terms     = fuel_terms     or []
        self.fuel_stems     = fuel_stems     or []
        self.impact_terms   = impact_terms   or []
        self.impact_stems   = impact_stems   or []
        self.fuel_subgroups   = fuel_subgroups   or []
        self.impact_subgroups = impact_subgroups or []

    NO_MATCH: "MatchResult"


MatchResult.NO_MATCH = MatchResult()


def invert_abstract(abstract_inverted_index: Dict[str, List[int]]) -> str:
    """Reconstruct abstract text from OpenAlex inverted index."""
    if not abstract_inverted_index:
        return ""
    max_position = max(
        (max(positions) for positions in abstract_inverted_index.values() if positions),
        default=-1,
    )
    if max_position < 0:
        return ""
    ordered_tokens = [""] * (max_position + 1)
    for token, positions in abstract_inverted_index.items():
        for pos in positions:
            if 0 <= pos <= max_position and not ordered_tokens[pos]:
                ordered_tokens[pos] = token
    return " ".join(token for token in ordered_tokens if token).strip()


def _stem_keys(abstract_inverted_index: dict) -> frozenset[str]:
    stems: set[str] = set()
    for key in abstract_inverted_index:
        for tok in tokenize(normalize_text(key)):
            stems.add(stem_token(tok))
    return frozenset(stems)


def _could_match_index(stem_set: frozenset[str], first_tokens: frozenset[str]) -> bool:
    return not stem_set.isdisjoint(first_tokens)


def keyword_check_smart(title: str, abstract_inverted_index: dict) -> MatchResult:
    """Multi-phase keyword check.  Returns a MatchResult with provenance."""
    title_stems = normalize_and_stem(title)

    title_has_fuel   = contains_any_term(title_stems, FUEL_INDEX)   if title_stems else False
    title_has_impact = contains_any_term(title_stems, IMPACT_INDEX) if title_stems else False

    if title_has_fuel and title_has_impact:
        abstract_text = invert_abstract(abstract_inverted_index)
        combined = normalize_and_stem(f"{title} {abstract_text}")
        ft, fs  = find_all_matching_terms(combined, FUEL_INDEX,   FUEL_REVERSE)
        it, ist = find_all_matching_terms(combined, IMPACT_INDEX, IMPACT_REVERSE)
        return MatchResult(
            True, abstract_text, ft, fs, it, ist,
            _compute_subgroups(ft, FUEL_SUBGROUP_MAP),
            _compute_subgroups(it, IMPACT_SUBGROUP_MAP),
        )

    abs_stem_set   = _stem_keys(abstract_inverted_index)
    combined_stems = abs_stem_set | frozenset(title_stems)

    if not title_has_fuel and not _could_match_index(combined_stems, _FUEL_FIRST_TOKENS):
        return MatchResult.NO_MATCH
    if not title_has_impact and not _could_match_index(combined_stems, _IMPACT_FIRST_TOKENS):
        return MatchResult.NO_MATCH

    abstract_text = invert_abstract(abstract_inverted_index)
    if not abstract_text:
        return MatchResult.NO_MATCH

    combined_text   = f"{title} {abstract_text}"
    stemmed_tokens  = normalize_and_stem(combined_text)
    if not stemmed_tokens:
        return MatchResult.NO_MATCH

    if not title_has_fuel   and not contains_any_term(stemmed_tokens, FUEL_INDEX):
        return MatchResult.NO_MATCH
    if not title_has_impact and not contains_any_term(stemmed_tokens, IMPACT_INDEX):
        return MatchResult.NO_MATCH

    ft, fs  = find_all_matching_terms(stemmed_tokens, FUEL_INDEX,   FUEL_REVERSE)
    it, ist = find_all_matching_terms(stemmed_tokens, IMPACT_INDEX, IMPACT_REVERSE)
    return MatchResult(
        True, abstract_text, ft, fs, it, ist,
        _compute_subgroups(ft, FUEL_SUBGROUP_MAP),
        _compute_subgroups(it, IMPACT_SUBGROUP_MAP),
    )


# ============================================================================
# AUTHOR AND INSTITUTION EXTRACTION
# ============================================================================

def extract_authors_institutions_and_countries(record: dict) -> tuple[str, str]:
    institutions: list[str] = []
    countries: list[str] = []
    for authorship in record.get("authorships") or []:
        for institution in authorship.get("institutions") or []:
            name = (institution.get("display_name") or "").strip()
            if name and name not in institutions:
                institutions.append(name)
        for country in authorship.get("countries") or []:
            c = (country or "").strip()
            if c and c not in countries:
                countries.append(c)
    return (
        json.dumps(institutions, ensure_ascii=False),
        json.dumps(countries,    ensure_ascii=False),
    )


# ============================================================================
# DATABASE SCHEMA
# ============================================================================

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS filtered_records (
        openalex_id                              TEXT PRIMARY KEY,
        doi                                      TEXT NOT NULL,
        title                                    TEXT NOT NULL,
        is_oa                                    INTEGER NOT NULL,
        abstract_text                            TEXT NOT NULL,
        publication_year                         INTEGER,
        publication_date                         TEXT,
        fwci                                     REAL,
        language                                 TEXT,
        type                                     TEXT,
        open_access_is_oa                        INTEGER,
        open_access_any_repository_has_fulltext  INTEGER,
        open_access_oa_status                    TEXT,
        open_access_oa_url                       TEXT,
        primary_location_is_oa                   INTEGER,
        primary_location_landing_page_url        TEXT,
        primary_location_license                 TEXT,
        primary_location_source_display_name     TEXT,
        primary_location_source_host_organization_name TEXT,
        primary_location_source_type             TEXT,
        has_fulltext                             INTEGER,
        fulltext_origin                          TEXT,
        best_oa_location_license                 TEXT,
        best_oa_location_pdf_url                 TEXT,
        best_oa_location_version                 TEXT,
        authors_institutions                     TEXT,
        authors_countries                        TEXT,
        matched_fuel_terms                       TEXT,
        matched_impact_terms                     TEXT,
        matched_fuel_stems                       TEXT,
        matched_impact_stems                     TEXT,
        matched_fuel_subgroups                   TEXT,
        matched_impact_subgroups                 TEXT,
        cited_by_count                           INTEGER,
        primary_location_source_issn_l           TEXT,
        is_retracted                             INTEGER
    )
"""

_INSERT_SQL = """
    INSERT OR REPLACE INTO filtered_records
    (
        openalex_id, doi, title, is_oa, abstract_text,
        publication_year, publication_date, fwci, language, type,
        open_access_is_oa, open_access_any_repository_has_fulltext,
        open_access_oa_status, open_access_oa_url,
        primary_location_is_oa, primary_location_landing_page_url,
        primary_location_license, primary_location_source_display_name,
        primary_location_source_host_organization_name,
        primary_location_source_type,
        has_fulltext, fulltext_origin,
        best_oa_location_license, best_oa_location_pdf_url,
        best_oa_location_version,
        authors_institutions, authors_countries,
        matched_fuel_terms, matched_impact_terms,
        matched_fuel_stems, matched_impact_stems,
        matched_fuel_subgroups, matched_impact_subgroups,
        cited_by_count, primary_location_source_issn_l, is_retracted
    )
    VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?, ?,?, ?,?,?, ?,?, ?,?,?,?, ?,?, ?,?,?)
"""


# ============================================================================
# DATABASE HELPERS
# ============================================================================

def ensure_db(conn: sqlite3.Connection) -> None:
    """Create table and add any missing columns (safe for schema upgrades)."""
    conn.execute(_CREATE_TABLE_SQL)
    for col in (
        "matched_fuel_terms", "matched_impact_terms",
        "matched_fuel_stems", "matched_impact_stems",
        "has_fulltext", "fulltext_origin",
        "best_oa_location_license", "best_oa_location_pdf_url",
        "best_oa_location_version",
        "matched_fuel_subgroups", "matched_impact_subgroups",
        "cited_by_count", "primary_location_source_issn_l", "is_retracted",
    ):
        try:
            conn.execute(f"ALTER TABLE filtered_records ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Drop and recreate the filtered_records table."""
    conn.execute("DROP TABLE IF EXISTS filtered_records")
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()


def load_subgroup_maps_from_config(
    config: dict,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build {term: subgroup_name} maps from a pipeline config dict."""
    groups = config.get("groups", [])

    def _build(g: dict) -> dict[str, str]:
        return {t: sg["name"] for sg in g.get("subgroups", []) for t in sg.get("terms", [])}

    fuel_map   = _build(groups[0]) if len(groups) > 0 else {}
    impact_map = _build(groups[1]) if len(groups) > 1 else {}
    return fuel_map, impact_map


def backfill_subgroups(
    conn: sqlite3.Connection,
    fuel_subgroup_map: dict[str, str],
    impact_subgroup_map: dict[str, str],
) -> int:
    """Fill matched_*_subgroups for records that have matched terms but empty
    subgroup annotations — occurs when records predate the subgroup config."""
    if not fuel_subgroup_map and not impact_subgroup_map:
        return 0
    cur = conn.execute(
        "SELECT openalex_id, matched_fuel_terms, matched_impact_terms "
        "FROM filtered_records "
        "WHERE matched_fuel_subgroups IS NULL "
        "   OR matched_fuel_subgroups = '' "
        "   OR matched_fuel_subgroups = '[]'"
    )
    to_fix = cur.fetchall()
    if not to_fix:
        return 0
    updates: list[tuple] = []
    for openalex_id, fuel_json, impact_json in to_fix:
        try:
            fuel_t = json.loads(fuel_json or "[]")
        except Exception:
            fuel_t = []
        try:
            impact_t = json.loads(impact_json or "[]")
        except Exception:
            impact_t = []
        updates.append((
            json.dumps(_compute_subgroups(fuel_t,   fuel_subgroup_map),   ensure_ascii=False),
            json.dumps(_compute_subgroups(impact_t, impact_subgroup_map), ensure_ascii=False),
            openalex_id,
        ))
    conn.executemany(
        "UPDATE filtered_records "
        "SET matched_fuel_subgroups = ?, matched_impact_subgroups = ? "
        "WHERE openalex_id = ?",
        updates,
    )
    conn.commit()
    return len(updates)


_SUBGROUP_RENAMES = {"Feedstocks and Processed": "Feedstocks and Processes"}


def apply_subgroup_renames(conn: sqlite3.Connection) -> int:
    """Apply subgroup name corrections across matched_*_subgroups columns."""
    updated = 0
    for old, new in _SUBGROUP_RENAMES.items():
        for col in ("matched_fuel_subgroups", "matched_impact_subgroups"):
            cur = conn.execute(
                f"UPDATE filtered_records "
                f"SET {col} = REPLACE({col}, ?, ?) "
                f"WHERE {col} LIKE ?",
                (f'"{old}"', f'"{new}"', f'%"{old}"%'),
            )
            updated += cur.rowcount
    if updated:
        conn.commit()
    return updated


# ============================================================================
# CORE RECORD EXTRACTION
# ============================================================================

def _extract_row(record: dict, title: str, abstract_text: str, match: MatchResult) -> tuple | None:
    openalex_id = (record.get("id") or "").strip()
    if not openalex_id:
        return None

    open_access = record.get("open_access") or {}
    oa_any_repo = bool(open_access.get("any_repository_has_fulltext"))
    is_oa_val   = int(bool(open_access.get("is_oa")))

    pl      = record.get("primary_location") or {}
    pl_src  = pl.get("source") or {}
    boa     = record.get("best_oa_location") or {}

    ai, ac = extract_authors_institutions_and_countries(record)

    return (
        openalex_id,
        (record.get("doi") or "").strip(),
        title,
        is_oa_val,
        abstract_text,
        record.get("publication_year"),
        record.get("publication_date"),
        record.get("fwci"),
        (record.get("language") or "").strip().lower(),
        record.get("type"),
        is_oa_val,
        1 if oa_any_repo else 0,
        open_access.get("oa_status"),
        open_access.get("oa_url"),
        1 if bool(pl.get("is_oa")) else 0,
        pl.get("landing_page_url"),
        pl.get("license"),
        pl_src.get("display_name"),
        pl_src.get("host_organization_name"),
        pl_src.get("type"),
        1 if bool(record.get("has_fulltext")) else 0,
        record.get("fulltext_origin"),
        boa.get("license"),
        boa.get("pdf_url"),
        boa.get("version"),
        ai,
        ac,
        json.dumps(match.fuel_terms,       ensure_ascii=False),
        json.dumps(match.impact_terms,     ensure_ascii=False),
        json.dumps(match.fuel_stems,       ensure_ascii=False),
        json.dumps(match.impact_stems,     ensure_ascii=False),
        json.dumps(match.fuel_subgroups,   ensure_ascii=False),
        json.dumps(match.impact_subgroups, ensure_ascii=False),
        record.get("cited_by_count"),
        pl_src.get("issn_l"),
        1 if bool(record.get("is_retracted")) else 0,
    )


# ============================================================================
# SINGLE-FILE PROCESSING
# ============================================================================

def process_records_to_rows(
    input_gz_path: Path,
    max_records: int | None = None,
) -> tuple[int, int, list[tuple], dict[str, int], list[str]]:
    """Process one .gz JSONL file and return matched rows in memory.

    Returns (total_seen, total_matched, rows, skip_stats, errors).
    """
    if not _terms_initialized:
        _init_terms()

    matched = 0
    total   = 0
    rows:   list[tuple]        = []
    skips:  dict[str, int]     = {}
    errors: list[str]          = []

    def _s(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    try:
        with gzip.open(input_gz_path, "rt", encoding="utf-8") as infile:
            for line in infile:
                if max_records is not None and total >= max_records:
                    break
                total += 1
                line = line.strip()
                if not line:
                    _s("empty_line"); continue

                # Fast language pre-screen on raw bytes
                if '"language": "en"' not in line and '"language":"en"' not in line:
                    _s("not_english"); continue

                try:
                    record = _loads(line)
                except _JSONDecodeError as e:
                    _s("json_error")
                    errors.append(f"line {total}: JSON parse error: {e}")
                    continue

                if not (record.get("doi") or "").strip():
                    _s("no_doi"); continue
                if (record.get("language") or "").strip().lower() != "en":
                    _s("not_english"); continue

                abstract_ii = record.get("abstract_inverted_index") or {}
                if not abstract_ii:
                    _s("no_abstract"); continue

                title = (record.get("title") or "").strip()
                try:
                    mr = keyword_check_smart(title, abstract_ii)
                except Exception as e:
                    _s("processing_error")
                    errors.append(f"line {total}: keyword check error: {e}")
                    continue

                if not mr.matched:
                    _s("no_keywords"); continue
                if not mr.abstract_text:
                    _s("empty_abstract"); continue

                try:
                    row = _extract_row(record, title, mr.abstract_text, mr)
                except Exception as e:
                    _s("processing_error")
                    errors.append(f"line {total}: row extraction error: {e}")
                    continue

                if row is None:
                    _s("no_id"); continue

                rows.append(row)
                matched += 1

    except Exception as e:
        errors.append(
            f"FATAL reading {input_gz_path.name}: "
            f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        )

    return total, matched, rows, skips, errors


def process_file(
    input_gz_path: Path,
    output_db_path: Path,
    max_records: int | None = None,
    append_mode: bool = False,
) -> tuple[int, int]:
    """Filter a single .gz file and write matched rows to SQLite.
    Returns (total_seen, total_inserted)."""
    conn = sqlite3.connect(output_db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")
    if append_mode:
        ensure_db(conn)
    else:
        init_db(conn)

    total, inserted, rows, _skips, errs = process_records_to_rows(input_gz_path, max_records)

    if rows:
        conn.executemany(_INSERT_SQL, rows)
        conn.commit()

    if FUEL_SUBGROUP_MAP or IMPACT_SUBGROUP_MAP:
        n = backfill_subgroups(conn, FUEL_SUBGROUP_MAP, IMPACT_SUBGROUP_MAP)
        if n:
            print(f"Backfilled subgroups for {n:,} records.")

    n_renamed = apply_subgroup_renames(conn)
    if n_renamed:
        print(f"Renamed subgroups in {n_renamed:,} cells.")

    conn.close()

    print(f"Processed:  {total:,} records")
    print(f"Matched:    {inserted:,} records")
    print(f"Skipped:    {total - inserted:,} records")
    print(f"Database:   {output_db_path}")
    if errs:
        print(f"Errors ({len(errs)}):")
        for e in errs[:10]:
            print(f"  {e}")

    return total, inserted


# ============================================================================
# TERMINAL DISPLAY (parallel run mode)
# ============================================================================

class _C:
    """ANSI colour codes — disabled when --no-color or non-TTY."""
    RST = BOLD = DIM = GREEN = YELLOW = CYAN = RED = MAGENTA = BLUE = ""

    @classmethod
    def enable(cls) -> None:
        cls.RST     = "\033[0m"
        cls.BOLD    = "\033[1m"
        cls.DIM     = "\033[2m"
        cls.GREEN   = "\033[32m"
        cls.YELLOW  = "\033[33m"
        cls.CYAN    = "\033[36m"
        cls.RED     = "\033[31m"
        cls.MAGENTA = "\033[35m"
        cls.BLUE    = "\033[34m"


_CLEAR_LINE  = "\033[K"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"

_SKIP_LABELS: dict[str, str] = {
    "not_oa": "not open access", "not_english": "not English",
    "no_doi": "no DOI", "no_abstract": "no abstract",
    "no_keywords": "no keywords", "empty_abstract": "empty abstract",
    "json_error": "JSON error", "empty_line": "empty line",
    "no_id": "no ID", "processing_error": "error",
}


def _bar(fraction: float, width: int = 36) -> str:
    filled = int(width * min(fraction, 1.0))
    return f"{_C.GREEN}{'█' * filled}{_C.DIM}{'░' * (width - filled)}{_C.RST}"


def _mini_bar(fraction: float, width: int = 8) -> str:
    filled = int(width * min(fraction, 1.0))
    return f"{_C.GREEN}{'█' * filled}{_C.DIM}{'░' * (width - filled)}{_C.RST}"


def _short_key(s3_key: str) -> str:
    return s3_key.replace("data/works/updated_date=", "").replace(".gz", "")


def _fmt_dur(seconds: float) -> str:
    h, m, s = int(seconds // 3600), int((seconds % 3600) // 60), int(seconds % 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def _fmt_size(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _fmt_count(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else (f"{n / 1_000:.1f}K" if n >= 1_000 else str(n))


class Display:
    """Live terminal display that overwrites itself in-place."""

    def __init__(self, total_files: int, num_workers: int, worker_status: dict, is_tty: bool, mode: str) -> None:
        self.total_files  = total_files
        self.num_workers  = num_workers
        self.worker_status = worker_status
        self.is_tty       = is_tty
        self.mode         = mode
        self.files_done: int = 0
        self.total_seen: int = 0
        self.total_inserted: int = 0
        self.total_data_mb: float = 0.0
        self.file_errors: int = 0
        self.record_errors: int = 0
        self.recent_errors: list[str] = []
        self.skip_stats: dict[str, int] = {}
        self.last_match_title: str = ""
        self.free_gb: float = 0.0
        self.db_size_mb: float = 0.0
        self._start = time.monotonic()
        self._lines_printed = 0
        self._last_render = 0.0
        self._speed_samples: list[tuple[float, int]] = []

    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    def _files_per_min(self) -> float:
        now = time.monotonic()
        self._speed_samples.append((now, self.files_done))
        cutoff = now - 120.0
        self._speed_samples = [(t, n) for t, n in self._speed_samples if t >= cutoff]
        if len(self._speed_samples) < 2:
            elapsed = self._elapsed()
            return self.files_done / elapsed * 60 if elapsed >= 5 and self.files_done else 0.0
        oldest_t, oldest_n = self._speed_samples[0]
        dt = now - oldest_t
        return (self.files_done - oldest_n) / dt * 60 if dt >= 1 else 0.0

    def _eta(self) -> str:
        fpm = self._files_per_min()
        remaining = self.total_files - self.files_done
        if remaining <= 0: return "done"
        if fpm <= 0:       return "calculating…"
        return _fmt_dur(remaining / fpm * 60)

    def _records_per_sec(self) -> float:
        elapsed = self._elapsed()
        return self.total_seen / elapsed if elapsed >= 2 else 0.0

    def add_skip_stats(self, skips: dict[str, int]) -> None:
        for reason, count in skips.items():
            self.skip_stats[reason] = self.skip_stats.get(reason, 0) + count

    def add_errors(self, file_key: str, errors: list[str]) -> None:
        self.record_errors += len(errors)
        for e in errors:
            self.recent_errors.append(f"{_short_key(file_key)}: {e}")
        if len(self.recent_errors) > 20:
            self.recent_errors = self.recent_errors[-20:]

    def render(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_render) < 0.30:
            return
        self._last_render = now
        if self.is_tty:
            lines = self._build_lines()
            if self._lines_printed > 0:
                sys.stdout.write(f"\033[{self._lines_printed}A")
            for line in lines:
                sys.stdout.write(f"{_CLEAR_LINE}{line}\n")
            sys.stdout.flush()
            self._lines_printed = len(lines)
        else:
            pct = (self.files_done / self.total_files * 100) if self.total_files else 0
            print(
                f"[{self.files_done:,}/{self.total_files:,} ({pct:.1f}%)] "
                f"seen={self.total_seen:,} ins={self.total_inserted:,} "
                f"err={self.file_errors}+{self.record_errors}",
                flush=True,
            )

    def _build_lines(self) -> list[str]:
        C = _C
        elapsed  = self._elapsed()
        pct      = (self.files_done / self.total_files * 100) if self.total_files else 0
        fpm      = self._files_per_min()
        hit_rate = (self.total_inserted / self.total_seen * 100) if self.total_seen else 0
        rps      = self._records_per_sec()
        src      = "local" if self.mode == "local" else "S3"
        lines: list[str] = []

        lines.append(
            f" {C.BOLD}OpenAlex ({src}) → SQLite{C.RST}"
            f"                    elapsed {C.CYAN}{_fmt_dur(elapsed)}{C.RST}"
        )
        lines.append(
            f" {_bar(pct / 100)}  {self.files_done:,} / {self.total_files:,}  ({C.BOLD}{pct:.1f}%{C.RST})"
        )
        if fpm > 0:
            speed_str = (
                f"{_fmt_count(int(rps))} rec/s" if self.mode == "local"
                else f"{_fmt_size(self.total_data_mb / (elapsed / 60) if elapsed > 5 else 0)}/min"
            )
            lines.append(
                f" {speed_str}  ·  {fpm:.1f} files/min  ·  {C.CYAN}ETA ~{self._eta()}{C.RST}"
            )
        else:
            lines.append(f" {C.DIM}warming up…{C.RST}  ·  {C.CYAN}ETA ~{self._eta()}{C.RST}")
        lines.append("")

        for slot in range(self.num_workers):
            ws = (self.worker_status.get(slot) if self.worker_status else None)
            if ws is None or ws.get("state") == "idle":
                lines.append(f" {C.DIM} ·  w{slot}  idle{C.RST}"); continue
            state = ws.get("state", "?")
            key   = _short_key(ws.get("key", "?"))
            if len(key) > 28: key = "…" + key[-27:]
            if state == "downloading":
                db, dt, sp = ws.get("dl_bytes", 0), ws.get("dl_total", 1), ws.get("dl_speed", 0)
                frac = db / dt if dt else 0
                lines.append(
                    f" {C.BLUE} ↓{C.RST}  w{slot}  {key}  {_mini_bar(frac)}"
                    f"  {db/(1024**2):.0f}/{dt/(1024**2):.0f} MB  {C.DIM}{sp:.1f} MB/s{C.RST}"
                )
            elif state == "processing":
                smb = ws.get("size_mb", 0)
                lines.append(
                    f" {C.YELLOW} ⚙{C.RST}  w{slot}  {key}  {C.YELLOW}filtering…{C.RST}"
                    + (f"  {C.DIM}{smb:.0f} MB{C.RST}" if smb else "")
                )
            elif state == "done":
                lines.append(f" {C.GREEN} ✓{C.RST}  w{slot}  {key}  {C.DIM}done{C.RST}")
            elif state == "error":
                lines.append(f" {C.RED} ✗{C.RST}  w{slot}  {key}  {C.RED}error{C.RST}")
            else:
                lines.append(f" {C.DIM} ?  w{slot}  {key}  {state}{C.RST}")
        lines.append("")

        ins_color = C.GREEN if self.total_inserted > 0 else C.DIM
        lines.append(
            f" {C.BOLD}Records{C.RST}   {_fmt_count(self.total_seen)} seen"
            f"  →  {ins_color}{self.total_inserted:,} matched{C.RST}  ({hit_rate:.4f}%)"
        )

        total_skipped = sum(self.skip_stats.values())
        if total_skipped > 0:
            parts = [
                f"{_SKIP_LABELS.get(r, r)} {c / total_skipped * 100:.0f}%"
                for r, c in sorted(self.skip_stats.items(), key=lambda kv: kv[1], reverse=True)[:6]
            ]
            lines.append(f" {C.BOLD}Skipped{C.RST}   " + f"  {C.DIM}·{C.RST}  ".join(parts))
        else:
            lines.append(f" {C.BOLD}Skipped{C.RST}   {C.DIM}—{C.RST}")

        total_errs = self.file_errors + self.record_errors
        if total_errs:
            parts = (
                ([f"{self.file_errors} file"] if self.file_errors else []) +
                ([f"{self.record_errors} record"] if self.record_errors else [])
            )
            err_part = f"{C.RED}{' + '.join(parts)} errors{C.RST}"
        else:
            err_part = f"{C.DIM}0 errors{C.RST}"
        lines.append(
            f" {C.BOLD}System{C.RST}    {self.free_gb:.1f} GB free"
            f"  ·  DB {self.db_size_mb:.1f} MB  ·  {err_part}"
        )

        if self.last_match_title:
            t = self.last_match_title[:57] + "…" if len(self.last_match_title) > 60 else self.last_match_title
            lines.append(f" {C.BOLD}Last hit{C.RST}  {C.MAGENTA}{t}{C.RST}")
        else:
            lines.append(f" {C.BOLD}Last hit{C.RST}  {C.DIM}—{C.RST}")

        if self.recent_errors:
            lines.append("")
            lines.append(f" {C.RED}{C.BOLD}Errors{C.RST}")
            for e in self.recent_errors[-3:]:
                if len(e) > 72: e = e[:69] + "…"
                lines.append(f" {C.RED} ·{C.RST}  {C.DIM}{e}{C.RST}")
        lines.append("")
        return lines

    def print_final_summary(self, state: dict) -> None:
        C = _C
        elapsed  = self._elapsed()
        pct      = (self.files_done / self.total_files * 100) if self.total_files else 0
        hit_rate = (self.total_inserted / self.total_seen * 100) if self.total_seen else 0
        print()
        print(f" {C.BOLD}{'═' * 64}{C.RST}")
        print(f" {C.BOLD}Pipeline complete{C.RST}  —  {_fmt_dur(elapsed)}")
        print(f" {'═' * 64}")
        print()
        print(f"   Files      {self.files_done:,} / {self.total_files:,} ({pct:.1f}%)")
        print(
            f"   Records    {self.total_seen:,} seen  →  "
            f"{C.GREEN}{self.total_inserted:,} matched{C.RST}  ({hit_rate:.4f}%)"
        )
        if self.total_data_mb > 0:
            print(f"   Data       {_fmt_size(self.total_data_mb)}")
        if self.file_errors or self.record_errors:
            parts = (
                ([f"{self.file_errors} file(s) failed"] if self.file_errors else []) +
                ([f"{self.record_errors} record-level errors"] if self.record_errors else [])
            )
            print(f"   Errors     {C.RED}{', '.join(parts)}{C.RST}")
        print()

        total_skipped = sum(self.skip_stats.values())
        if total_skipped > 0:
            print(f"   {C.BOLD}Skip breakdown{C.RST} ({total_skipped:,} total)")
            sorted_reasons = sorted(self.skip_stats.items(), key=lambda kv: kv[1], reverse=True)
            max_count = sorted_reasons[0][1] if sorted_reasons else 1
            for reason, count in sorted_reasons:
                pct_r = count / total_skipped * 100
                label = _SKIP_LABELS.get(reason, reason)
                bar_str = f"{C.DIM}{'▪' * int(30 * count / max_count)}{C.RST}"
                print(f"   {bar_str} {label:>16s}  {count:>12,}  ({pct_r:5.1f}%)")
            print()

        if self.recent_errors:
            print(f"   {C.BOLD}Recent errors{C.RST} ({self.file_errors + self.record_errors} total — full details in pipeline.log)")
            for e in self.recent_errors[-10:]:
                if len(e) > 68: e = e[:65] + "…"
                print(f"   {C.RED}·{C.RST} {C.DIM}{e}{C.RST}")
            print()

        print(f" {C.DIM}{'─' * 64}{C.RST}")
        print(f" {C.DIM}State saved to pipeline_state.json  ·  Full log in pipeline.log{C.RST}")
        print()


# ============================================================================
# FILE LISTING — LOCAL OR S3
# ============================================================================

def list_local_files(local_dir: Path) -> list[str]:
    """Discover .gz files under local_dir and return S3-style keys."""
    print(f" Scanning {local_dir} …", end="", flush=True)
    keys: list[str] = []
    for gz in sorted(local_dir.rglob("*.gz")):
        try:
            rel = gz.relative_to(local_dir)
        except ValueError:
            continue
        keys.append("data/works/" + str(rel).replace("\\", "/"))
    print(f"  found {len(keys):,} files.")
    logger.info(f"Local scan: found {len(keys):,} .gz files in {local_dir}")
    return keys


def _s3_client():
    return boto3.client(
        "s3", region_name=S3_REGION,
        config=BotocoreConfig(signature_version=UNSIGNED),
    )


def list_s3_files() -> list[str]:
    print(" Listing partition files from S3…", end="", flush=True)
    client    = _s3_client()
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            if key.endswith(".gz"):
                keys.append(key)
    keys.sort()
    print(f"  found {len(keys):,} files.")
    logger.info(f"S3 listing: found {len(keys):,} partition files.")
    return keys


def _key_to_local_path(key: str, local_dir: Path) -> Path:
    return local_dir / key.removeprefix("data/works/")


# ============================================================================
# DISK HELPERS
# ============================================================================

def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def _db_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 ** 2) if path.exists() else 0.0
    except OSError:
        return 0.0


# ============================================================================
# STATE PERSISTENCE  (Windows / OneDrive safe)
# ============================================================================

def load_state(state_file: Path) -> dict:
    if state_file.exists():
        with state_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "all_files": [], "processed_files": [], "failed_files": [],
        "total_records_seen": 0, "total_records_inserted": 0,
        "started_at": None, "last_updated": None,
        "skip_stats": {}, "file_errors": 0, "record_errors": 0,
    }


def save_state(state: dict, state_file: Path) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    for attempt in range(5):
        try:
            tmp.replace(state_file)
            return
        except PermissionError:
            if attempt < 4:
                time.sleep(0.2 * (attempt + 1))
            else:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                with state_file.open("w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)


# ============================================================================
# PARALLEL WORKERS  (run in child processes via ProcessPoolExecutor)
# ============================================================================

def _worker_local(
    key: str,
    gz_path: str,
    worker_status: dict,
    slot_id: int,
    keyword_config_path: str | None = None,
) -> dict:
    """Filter a local .gz file and return matched rows."""
    _load_keyword_config(keyword_config_path)

    local    = Path(gz_path)
    size_mb  = local.stat().st_size / (1024 ** 2) if local.exists() else 0.0
    if worker_status is not None:
        worker_status[slot_id] = {"state": "processing", "key": key, "size_mb": size_mb}
    try:
        seen, matched, rows, skips, record_errors = process_records_to_rows(local)
        if worker_status is not None:
            worker_status[slot_id] = {"state": "done", "key": key}
        return {
            "s3_key": key, "seen": seen, "inserted": matched, "data_mb": size_mb,
            "rows": rows, "skips": skips,
            "last_title": rows[-1][2] if rows else "",
            "record_errors": record_errors, "error": None,
        }
    except Exception as e:
        if worker_status is not None:
            worker_status[slot_id] = {"state": "error", "key": key}
        return {
            "s3_key": key, "seen": 0, "inserted": 0, "data_mb": 0.0,
            "rows": [], "skips": {}, "last_title": "",
            "record_errors": [f"FATAL: {type(e).__name__}: {e}\n{traceback.format_exc()}"],
            "error": str(e),
        }


def _worker_s3(
    s3_key: str,
    temp_dir: str,
    worker_status: dict,
    slot_id: int,
    keyword_config_path: str | None = None,
) -> dict:
    """Download one S3 partition, filter it, and return matched rows."""
    _load_keyword_config(keyword_config_path)

    local_path = Path(temp_dir) / f"w{slot_id}_{os.getpid()}.gz"
    dl_mb      = 0.0
    try:
        client      = _s3_client()
        head        = client.head_object(Bucket=S3_BUCKET, Key=s3_key)
        total_bytes = head.get("ContentLength", 0)
        if worker_status is not None:
            worker_status[slot_id] = {
                "state": "downloading", "key": s3_key,
                "dl_bytes": 0, "dl_total": total_bytes, "dl_speed": 0.0,
            }
        downloaded = 0
        chunk_size = 8 * 1024 * 1024
        dl_start   = time.time()
        body = client.get_object(Bucket=S3_BUCKET, Key=s3_key)["Body"]
        with local_path.open("wb") as fout:
            for chunk in iter(lambda: body.read(chunk_size), b""):
                fout.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - dl_start
                speed   = (downloaded / (1024 ** 2)) / elapsed if elapsed > 0 else 0
                if worker_status is not None:
                    worker_status[slot_id] = {
                        "state": "downloading", "key": s3_key,
                        "dl_bytes": downloaded, "dl_total": total_bytes, "dl_speed": speed,
                    }
        dl_mb = downloaded / (1024 ** 2)
        if worker_status is not None:
            worker_status[slot_id] = {"state": "processing", "key": s3_key}
        seen, matched, rows, skips, record_errors = process_records_to_rows(local_path)
        if worker_status is not None:
            worker_status[slot_id] = {"state": "done", "key": s3_key}
        return {
            "s3_key": s3_key, "seen": seen, "inserted": matched, "data_mb": dl_mb,
            "rows": rows, "skips": skips,
            "last_title": rows[-1][2] if rows else "",
            "record_errors": record_errors, "error": None,
        }
    except Exception as e:
        if worker_status is not None:
            worker_status[slot_id] = {"state": "error", "key": s3_key}
        return {
            "s3_key": s3_key, "seen": 0, "inserted": 0, "data_mb": 0.0,
            "rows": [], "skips": {}, "last_title": "",
            "record_errors": [f"FATAL: {type(e).__name__}: {e}\n{traceback.format_exc()}"],
            "error": str(e),
        }
    finally:
        if local_path.exists():
            try:
                local_path.unlink()
            except OSError:
                pass


# ============================================================================
# PARALLEL PIPELINE
# ============================================================================

def run_parallel(
    output_db: Path,
    state_file: Path,
    max_files: int | None,
    min_free_gb: float,
    workers: int,
    use_color: bool,
    local_dir: Path | None,
    keyword_config: Path | None = None,
) -> None:
    """Run the full parallel pipeline over all partition files."""
    is_tty = sys.stdout.isatty()
    if use_color and is_tty:
        _C.enable()

    mode = "local" if local_dir is not None else "s3"
    if mode == "s3" and not _HAS_BOTO3:
        print(
            "ERROR: boto3 is not installed (needed for S3 mode).\n"
            "Install it with:  pip install boto3\n"
            "Or use --local-dir to process files from disk."
        )
        sys.exit(1)

    state = load_state(state_file)

    if not state["all_files"]:
        state["all_files"] = list_local_files(local_dir) if mode == "local" else list_s3_files()
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state, state_file)

    all_files     = state["all_files"]
    processed_set = set(state["processed_files"])
    failed_set    = set(state["failed_files"])
    pending       = [k for k in all_files if k not in processed_set]
    if max_files is not None:
        pending = pending[:max_files]

    total_files = len(all_files)
    already_done = len(processed_set)
    cpu_cores    = os.cpu_count() or "?"

    print(f" CPU cores: {cpu_cores}  ·  workers: {workers}  ·  mode: {mode}", flush=True)
    logger.info(
        f"Pipeline start — {total_files:,} total, {already_done:,} done, "
        f"{len(pending):,} to process, {workers} worker(s), mode={mode}, cpu_cores={cpu_cores}."
    )
    if not pending:
        print(" Nothing left to process. All done!")
        return

    if is_tty:
        manager = multiprocessing.Manager()
        worker_status: dict = manager.dict()
        for slot in range(workers):
            worker_status[slot] = {"state": "idle"}
    else:
        manager = None
        worker_status = None

    display = Display(total_files, workers, worker_status, is_tty, mode)
    display.files_done      = already_done
    display.total_seen      = state["total_records_seen"]
    display.total_inserted  = state["total_records_inserted"]
    if state.get("skip_stats"):
        display.skip_stats.update(state["skip_stats"])
    display.file_errors   += state.get("file_errors", 0)
    display.record_errors += state.get("record_errors", 0)

    conn = sqlite3.connect(output_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    ensure_db(conn)

    temp_dir: str | None = None
    if mode == "s3":
        temp_dir = tempfile.mkdtemp(prefix="openalex_", dir=SCRIPT_DIR)

    if is_tty:
        sys.stdout.write(_HIDE_CURSOR)
        sys.stdout.flush()
        first_frame = display._build_lines()
        for _ in first_frame: print()
        display._lines_printed = len(first_frame)

    submitted: dict[Future, tuple[str, int]] = {}
    free_slots = list(range(workers))
    idx        = 0
    kw_cfg_str = str(keyword_config) if keyword_config else None

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            while idx < len(pending) or submitted:
                db_dir = output_db.parent
                fg = _free_gb(db_dir)
                display.free_gb    = fg
                display.db_size_mb = _db_size_mb(output_db)

                if fg < min_free_gb:
                    logger.error(f"Disk space low: {fg:.2f} GB < {min_free_gb} GB")
                    break

                while free_slots and idx < len(pending):
                    key  = pending[idx]; idx += 1
                    slot = free_slots.pop(0)
                    if mode == "local":
                        fut = pool.submit(
                            _worker_local, key,
                            str(_key_to_local_path(key, local_dir)),
                            worker_status, slot, kw_cfg_str,
                        )
                    else:
                        if worker_status is not None:
                            worker_status[slot] = {
                                "state": "downloading", "key": key,
                                "dl_bytes": 0, "dl_total": 0, "dl_speed": 0.0,
                            }
                        fut = pool.submit(_worker_s3, key, temp_dir, worker_status, slot, kw_cfg_str)
                    submitted[fut] = (key, slot)

                if not submitted:
                    break

                done, _ = wait(submitted.keys(), timeout=0.05, return_when=FIRST_COMPLETED)
                for fut in done:
                    key, slot = submitted.pop(fut)
                    free_slots.append(slot)
                    if worker_status is not None:
                        worker_status[slot] = {"state": "idle"}

                    result   = fut.result()
                    rec_errs = result.get("record_errors", [])
                    if rec_errs:
                        display.add_errors(key, rec_errs)
                        for e in rec_errs:
                            logger.error(f"  {_short_key(key)}: {e}")

                    if result["error"]:
                        logger.error(f"FAILED {key}: {result['error']}")
                        display.file_errors += 1
                        display.add_errors(key, [f"FILE FAILED: {result['error']}"])
                        if key not in failed_set:
                            state["failed_files"].append(key); failed_set.add(key)
                        state["skip_stats"]    = dict(display.skip_stats)
                        state["file_errors"]   = display.file_errors
                        state["record_errors"] = display.record_errors
                        save_state(state, state_file)
                        display.files_done = len(state["processed_files"]) + len(state["failed_files"])
                        continue

                    if result["rows"]:
                        conn.executemany(_INSERT_SQL, result["rows"])
                        conn.commit()

                    state["processed_files"].append(key);  processed_set.add(key)
                    state["total_records_seen"]     += result["seen"]
                    state["total_records_inserted"] += result["inserted"]
                    state["skip_stats"]    = dict(display.skip_stats)
                    state["file_errors"]   = display.file_errors
                    state["record_errors"] = display.record_errors
                    save_state(state, state_file)

                    display.files_done      = len(state["processed_files"])
                    display.total_seen      = state["total_records_seen"]
                    display.total_inserted  = state["total_records_inserted"]
                    display.total_data_mb  += result["data_mb"]
                    display.add_skip_stats(result["skips"])
                    if result["last_title"]:
                        display.last_match_title = result["last_title"]

                    err_note = f"  ({len(rec_errs)} record errors)" if rec_errs else ""
                    logger.info(
                        f"[{display.files_done:,}/{total_files:,}] {key} — "
                        f"ins: {result['inserted']}  seen: {result['seen']:,}{err_note}"
                    )

                display.render()

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        if is_tty:
            sys.stdout.write(_SHOW_CURSOR); sys.stdout.flush()
        # Backfill subgroup annotations for records from prior runs
        if keyword_config and keyword_config.exists():
            try:
                _cfg_bf  = json.loads(keyword_config.read_text(encoding="utf-8"))
                fm, im   = load_subgroup_maps_from_config(_cfg_bf)
                if fm or im:
                    n_bf = backfill_subgroups(conn, fm, im)
                    if n_bf:
                        print(f" Backfilled subgroups for {n_bf:,} records.")
                        logger.info(f"Backfilled subgroups for {n_bf:,} records.")
            except Exception as e_bf:
                logger.warning(f"Subgroup backfill failed: {e_bf}")
        conn.close()
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        save_state(state, state_file)

    display.render(force=True)
    display.print_final_summary(state)


# ============================================================================
# CLI
# ============================================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Optional mode selector — defaults to 'run' for backward compatibility
    parser.add_argument(
        "mode", nargs="?", choices=["run", "filter"], default="run",
        help="'run'    — parallel pipeline over all partition files (default)\n"
             "'filter' — process a single .gz file",
    )

    # ── run mode ──
    run = parser.add_argument_group("run mode")
    run.add_argument("--local-dir", type=Path, default=None, metavar="DIR",
                     help="Local OpenAlex works directory (omit to stream from S3).")
    run.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE,
                     help=f"Pipeline state JSON (default: {DEFAULT_STATE_FILE.name})")
    run.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE,
                     help=f"Log file path (default: {DEFAULT_LOG_FILE.name})")
    run.add_argument("--max-files", type=int, default=None, metavar="N",
                     help="Stop after processing N files (smoke test).")
    run.add_argument("--min-free-gb", type=float, default=2.0,
                     help="Abort if free disk space falls below this GB (default: 2.0).")
    run.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 4),
                     metavar="N", help="Number of parallel worker processes.")
    run.add_argument("--no-color", action="store_true", help="Disable coloured output.")

    # ── filter mode ──
    flt = parser.add_argument_group("filter mode")
    flt.add_argument("--input", type=Path, default=Path("part_0009.gz"),
                     help="Input .gz JSONL file (default: part_0009.gz)")
    flt.add_argument("--max-records", type=int, default=None,
                     help="Stop after N records (smoke test).")
    flt.add_argument("--append", action="store_true",
                     help="Append to an existing database instead of replacing it.")

    # ── shared ──
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB,
                        help=f"Output SQLite database (default: {DEFAULT_OUTPUT_DB.name})")
    parser.add_argument("--keyword-config", type=Path, default=None, metavar="JSON",
                        help="JSON keyword config.  Falls back to built-in defaults when omitted.")

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.mode == "filter":
        _load_keyword_config(args.keyword_config)
        process_file(
            input_gz_path=args.input,
            output_db_path=args.output_db,
            max_records=args.max_records,
            append_mode=args.append,
        )
    else:
        # run mode
        log_file = getattr(args, "log_file", DEFAULT_LOG_FILE)
        _setup_logging(log_file)
        run_parallel(
            output_db=args.output_db,
            state_file=args.state_file,
            max_files=args.max_files,
            min_free_gb=args.min_free_gb,
            workers=args.workers,
            use_color=not args.no_color,
            local_dir=args.local_dir,
            keyword_config=args.keyword_config,
        )


if __name__ == "__main__":
    main()
