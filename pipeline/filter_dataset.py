"""
Proccess_dataset.py  (optimised — Tier 1 + keyword provenance)
===============================================================
Filter OpenAlex JSONL records by fuel + impact keyword co-occurrence.

Each matched record now stores:
  • matched_fuel_terms   — JSON list of the original fuel terms found
  • matched_impact_terms — JSON list of the original impact terms found
  • matched_fuel_stems   — JSON list of stemmed token tuples that fired
  • matched_impact_stems — JSON list of stemmed token tuples that fired

This makes every inclusion decision fully auditable.
"""

from __future__ import annotations

import gzip
import sqlite3
import argparse
import re
import traceback
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# Fast JSON — prefer orjson, fall back to stdlib
# ---------------------------------------------------------------------------
try:
    import orjson

    def _loads(raw: str | bytes):
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return orjson.loads(raw)

    _JSONDecodeError = orjson.JSONDecodeError
except ImportError:
    import json as _json

    _loads = _json.loads
    _JSONDecodeError = _json.JSONDecodeError

import json as _stdlib_json

# ---------------------------------------------------------------------------
# Keyword term lists
# ---------------------------------------------------------------------------
INPUT_GZ_PATH = Path("part_0009.gz")
OUTPUT_DB_PATH = Path("part_0009_filtered.sqlite")

FUEL_TERMS = [
    # ── Sustainable / Bio / Renewable ──
    "sustainable fuel",
    "biofuel",
    "renewable diesel",
    "synthetic fuel",
    "e-fuel",
    "sustainable aviation fuel",
    "biodiesel",
    "biomass-to-liquid",
    "BtL",
    "power-to-liquid",
    "green fuel",
    "renewable jet fuel",
    "bio-jet fuel",
    "bio-aviation fuel",
    "drop-in fuel",
    "paraffinic fuel",
    "Fischer-Tropsch fuel",
    "Fischer-Tropsch diesel",
    "FT fuel",
    "FT diesel",
    "hydroprocessed fuel",
    "co-processed fuel",
    "co-processing",
    "renewable fuel",
    "alternative fuel",
    "second generation fuel",
    "second-generation fuel",
    "advanced biofuel",
    "cellulosic biofuel",
    "algae fuel",
    "microalgae fuel",
    "pyrolysis oil",
    "bio-oil",
    "bio-crude",
    "hydrothermal liquefaction",
    "HTL fuel",
    # ── Conventional hydrocarbon fuels ──
    "jet fuel",
    "jet a1",
    "jet a-1",
    "kerosene",
    "petrodiesel",
    "gas oil",
    "gasoline",
    "marine fuel",
    "marine diesel",
    "bunker fuel",
    "naphtha",
    "gas-to-liquid",
    "GTL",
    "jp-8",
    "jp 8",
    "diesel",
    # ── Blends ──
    "biodiesel blend",
    "ethanol blend",
    "diesel blend",
    "fuel blend",
    # ── Feedstocks (appear in fuel production research) ──
    "waste cooking oil",
    "used cooking oil",
    "UCO",
    "jatropha",
    "palm oil methyl ester",
    "rapeseed methyl ester",
    "soybean methyl ester",
    "tallow",
    "animal fat",
    "vegetable oil",
    "fatty acid methyl ester",
    "hydrotreated vegetable oil",
    "HVO",
    "FAME",
    "Hydroprocessed Esters and Fatty Acids",
    "HEFA",
    "hydroprocessed esters",
    # ── Fuel standards ──
    "def stan 91-091",
    "ASTM D7566",
    "ASTM D975",
    "ASTM D1655",
    "EN 590",
    "EN 14214",
    "EN 228",
]

IMPACT_TERMS = [
    # ── Emissions ──
    "GHG",
    "CO2",
    "greenhouse gas",
    "emissions",
    "NOx",
    "SOx",
    "soot",
    "particulate matter",
    "PM2.5",
    "PM10",
    "black carbon",
    "carbon intensity",
    "carbon footprint",
    "exhaust emissions",
    "tailpipe emissions",
    "unburned hydrocarbons",
    "HC emissions",
    "CO emissions",
    "carbon monoxide",
    "smoke opacity",
    "smoke number",
    # ── Performance & efficiency ──
    "thermal efficiency",
    "brake thermal efficiency",
    "BTE",
    "specific fuel consumption",
    "BSFC",
    "energy density",
    "combustion efficiency",
    "engine performance",
    "brake power",
    "torque",
    "power output",
    # ── Combustion properties ──
    "ignition delay",
    "cetane number",
    "cetane index",
    "octane number",
    "heat release rate",
    "cylinder pressure",
    "combustion characteristics",
    "combustion duration",
    "spray characteristics",
    "injection pressure",
    "flame temperature",
    "auto-ignition",
    # ── Fuel physical properties ──
    "flash point",
    "cloud point",
    "pour point",
    "cold filter plugging point",
    "CFPP",
    "viscosity",
    "kinematic viscosity",
    "density",
    "calorific value",
    "heating value",
    "lower heating value",
    "higher heating value",
    "volatility",
    "distillation curve",
    "boiling point",
    "lubricity",
    "oxidation stability",
    "cold flow",
    "freezing point",
    "thermal stability",
    "storage stability",
    "Chemometrics",
    # ── Lifecycle ──
    "lifecycle",
    "life cycle",
    "well-to-wheel",
    "well-to-wake",
    "well-to-tank",
    "tank-to-wheel",
    "LCA",
    "life cycle assessment",
]


# ---------------------------------------------------------------------------
# Text normalisation & stemming
# ---------------------------------------------------------------------------
_UNICODE_REMAP = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉ₓ⁰¹²³⁴⁵⁶⁷⁸⁹",
    "0123456789x0123456789",
)
_RE_DIGIT_DOT = re.compile(r"(?<=[0-9])\.(?=[0-9])")
_RE_SEPARATOR = re.compile(r"[-_/]")
_RE_TOKENS = re.compile(r"[a-z][a-z0-9]*|[0-9]+")


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


# ---------------------------------------------------------------------------
# Term index with reverse mapping
# ---------------------------------------------------------------------------
def _index_add(index: dict, token_tuple: tuple) -> None:
    bucket = index.setdefault(token_tuple[0], [])
    if token_tuple not in bucket:
        bucket.append(token_tuple)


def compile_terms(
    terms: list[str],
) -> tuple[dict[str, list[tuple[str, ...]]], dict[tuple[str, ...], str]]:
    """
    Build a lookup index AND a reverse map (stemmed tuple → original term).

    Returns (index, reverse_map).
    """
    index: dict[str, list[tuple[str, ...]]] = {}
    reverse: dict[tuple[str, ...], str] = {}

    for term in terms:
        tokens = normalize_and_stem(term)
        if not tokens:
            continue
        phrase = tuple(tokens)
        _index_add(index, phrase)
        # Keep the first (canonical) original term for each stemmed form
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
    """
    Find ALL matching terms in *stemmed_tokens*.

    Returns (original_terms, stem_strings) where:
      - original_terms: deduplicated list of the original human-readable terms
      - stem_strings:   deduplicated list of the stemmed tuples as "token1 token2"
    """
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
# Lazy term initialisation (allows per-pipeline keyword configs)
# ---------------------------------------------------------------------------
FUEL_INDEX: dict = {}
FUEL_REVERSE: dict = {}
IMPACT_INDEX: dict = {}
IMPACT_REVERSE: dict = {}
_FUEL_FIRST_TOKENS: frozenset = frozenset()
_IMPACT_FIRST_TOKENS: frozenset = frozenset()
_terms_initialized = False

# Subgroup maps: original_term -> subgroup_name (empty when no config supplied)
FUEL_SUBGROUP_MAP: dict[str, str] = {}
IMPACT_SUBGROUP_MAP: dict[str, str] = {}


def _init_terms(
    fuel_terms: list[str] | None = None,
    impact_terms: list[str] | None = None,
    fuel_subgroups: list[dict] | None = None,
    impact_subgroups: list[dict] | None = None,
) -> None:
    """Compile keyword indices. Call once before processing.

    If fuel_terms / impact_terms are None the module-level defaults are used.
    fuel_subgroups / impact_subgroups are lists of {name, terms} dicts used to
    annotate matched records with their subgroup name.
    Can be called multiple times; subsequent calls rebuild the indices.
    """
    global FUEL_INDEX, FUEL_REVERSE, IMPACT_INDEX, IMPACT_REVERSE
    global _FUEL_FIRST_TOKENS, _IMPACT_FIRST_TOKENS, _terms_initialized
    global FUEL_SUBGROUP_MAP, IMPACT_SUBGROUP_MAP
    ft = fuel_terms if fuel_terms is not None else FUEL_TERMS
    it = impact_terms if impact_terms is not None else IMPACT_TERMS
    FUEL_INDEX, FUEL_REVERSE = compile_terms(ft)
    IMPACT_INDEX, IMPACT_REVERSE = compile_terms(it)
    _FUEL_FIRST_TOKENS = frozenset(FUEL_INDEX.keys())
    _IMPACT_FIRST_TOKENS = frozenset(IMPACT_INDEX.keys())
    _terms_initialized = True
    # Build term → subgroup name maps for provenance annotation
    if fuel_subgroups:
        FUEL_SUBGROUP_MAP = {
            term: sg["name"]
            for sg in fuel_subgroups
            for term in sg.get("terms", [])
        }
    else:
        FUEL_SUBGROUP_MAP = {}
    if impact_subgroups:
        IMPACT_SUBGROUP_MAP = {
            term: sg["name"]
            for sg in impact_subgroups
            for term in sg.get("terms", [])
        }
    else:
        IMPACT_SUBGROUP_MAP = {}


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


# ---------------------------------------------------------------------------
# Abstract inversion
# ---------------------------------------------------------------------------
def invert_abstract(abstract_inverted_index: Dict[str, List[int]]) -> str:
    if not abstract_inverted_index:
        return ""
    max_position = -1
    for positions in abstract_inverted_index.values():
        if positions:
            max_position = max(max_position, max(positions))
    if max_position < 0:
        return ""
    ordered_tokens = [""] * (max_position + 1)
    for token, positions in abstract_inverted_index.items():
        for pos in positions:
            if 0 <= pos <= max_position and not ordered_tokens[pos]:
                ordered_tokens[pos] = token
    return " ".join(token for token in ordered_tokens if token).strip()


# ---------------------------------------------------------------------------
# Key-based pre-screen for inverted index
# ---------------------------------------------------------------------------
def _stem_keys(abstract_inverted_index: dict) -> frozenset[str]:
    stems: set[str] = set()
    for key in abstract_inverted_index:
        for tok in tokenize(normalize_text(key)):
            stems.add(stem_token(tok))
    return frozenset(stems)


def _could_match_index(
    stem_set: frozenset[str], first_tokens: frozenset[str]
) -> bool:
    return not stem_set.isdisjoint(first_tokens)


# ---------------------------------------------------------------------------
# Keyword matching — returns matched terms for provenance
# ---------------------------------------------------------------------------

# Dataclass-like container for match results
class MatchResult:
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
        self.matched = matched
        self.abstract_text = abstract_text
        self.fuel_terms = fuel_terms or []
        self.fuel_stems = fuel_stems or []
        self.impact_terms = impact_terms or []
        self.impact_stems = impact_stems or []
        self.fuel_subgroups = fuel_subgroups or []
        self.impact_subgroups = impact_subgroups or []

    NO_MATCH: "MatchResult"

# Singleton for the common no-match case
MatchResult.NO_MATCH = MatchResult()


def keyword_check_smart(
    title: str,
    abstract_inverted_index: dict,
) -> MatchResult:
    """
    Multi-phase keyword check.  Returns a MatchResult that includes
    the specific fuel and impact terms that matched (for DB storage).
    """
    title_stems = normalize_and_stem(title)

    title_has_fuel = (
        contains_any_term(title_stems, FUEL_INDEX) if title_stems else False
    )
    title_has_impact = (
        contains_any_term(title_stems, IMPACT_INDEX) if title_stems else False
    )

    if title_has_fuel and title_has_impact:
        abstract_text = invert_abstract(abstract_inverted_index)
        combined = normalize_and_stem(f"{title} {abstract_text}")
        ft, fs = find_all_matching_terms(combined, FUEL_INDEX, FUEL_REVERSE)
        it, ist = find_all_matching_terms(
            combined, IMPACT_INDEX, IMPACT_REVERSE
        )
        return MatchResult(
            True, abstract_text, ft, fs, it, ist,
            _compute_subgroups(ft, FUEL_SUBGROUP_MAP),
            _compute_subgroups(it, IMPACT_SUBGROUP_MAP),
        )

    # --- Phase 2: cheap key pre-screen ---
    abs_stem_set = _stem_keys(abstract_inverted_index)
    combined_stems = abs_stem_set | frozenset(title_stems)

    if not title_has_fuel and not _could_match_index(
        combined_stems, _FUEL_FIRST_TOKENS
    ):
        return MatchResult.NO_MATCH

    if not title_has_impact and not _could_match_index(
        combined_stems, _IMPACT_FIRST_TOKENS
    ):
        return MatchResult.NO_MATCH

    # --- Phase 3: full inversion ---
    abstract_text = invert_abstract(abstract_inverted_index)
    if not abstract_text:
        return MatchResult.NO_MATCH

    combined_text = f"{title} {abstract_text}"
    stemmed_tokens = normalize_and_stem(combined_text)
    if not stemmed_tokens:
        return MatchResult.NO_MATCH

    if not title_has_fuel and not contains_any_term(
        stemmed_tokens, FUEL_INDEX
    ):
        return MatchResult.NO_MATCH
    if not title_has_impact and not contains_any_term(
        stemmed_tokens, IMPACT_INDEX
    ):
        return MatchResult.NO_MATCH

    # Collect all matching terms for provenance
    ft, fs = find_all_matching_terms(stemmed_tokens, FUEL_INDEX, FUEL_REVERSE)
    it, ist = find_all_matching_terms(
        stemmed_tokens, IMPACT_INDEX, IMPACT_REVERSE
    )
    return MatchResult(
        True, abstract_text, ft, fs, it, ist,
        _compute_subgroups(ft, FUEL_SUBGROUP_MAP),
        _compute_subgroups(it, IMPACT_SUBGROUP_MAP),
    )


# Backward compat
def keyword_exists_in_title_or_abstract(title: str, abstract_text: str) -> bool:
    combined_text = f"{title} {abstract_text}"
    stemmed_tokens = normalize_and_stem(combined_text)
    if not stemmed_tokens:
        return False
    if not contains_any_term(stemmed_tokens, FUEL_INDEX):
        return False
    return contains_any_term(stemmed_tokens, IMPACT_INDEX)


# ---------------------------------------------------------------------------
# Author / institution extraction
# ---------------------------------------------------------------------------
def extract_authors_institutions_and_countries(
    record: dict,
) -> tuple[str, str]:
    institutions: list[str] = []
    countries: list[str] = []
    for authorship in record.get("authorships") or []:
        for institution in authorship.get("institutions") or []:
            display_name = (institution.get("display_name") or "").strip()
            if display_name and display_name not in institutions:
                institutions.append(display_name)
        for country in authorship.get("countries") or []:
            country = (country or "").strip()
            if country and country not in countries:
                countries.append(country)
    return (
        _stdlib_json.dumps(institutions, ensure_ascii=False),
        _stdlib_json.dumps(countries, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# SQL — now includes matched keyword columns
# ---------------------------------------------------------------------------
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

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS filtered_records (
        openalex_id TEXT PRIMARY KEY,
        doi TEXT NOT NULL,
        title TEXT NOT NULL,
        is_oa INTEGER NOT NULL,
        abstract_text TEXT NOT NULL,
        publication_year INTEGER,
        publication_date TEXT,
        fwci REAL,
        language TEXT,
        type TEXT,
        open_access_is_oa INTEGER,
        open_access_any_repository_has_fulltext INTEGER,
        open_access_oa_status TEXT,
        open_access_oa_url TEXT,
        primary_location_is_oa INTEGER,
        primary_location_landing_page_url TEXT,
        primary_location_license TEXT,
        primary_location_source_display_name TEXT,
        primary_location_source_host_organization_name TEXT,
        primary_location_source_type TEXT,
        has_fulltext INTEGER,
        fulltext_origin TEXT,
        best_oa_location_license TEXT,
        best_oa_location_pdf_url TEXT,
        best_oa_location_version TEXT,
        authors_institutions TEXT,
        authors_countries TEXT,
        matched_fuel_terms TEXT,
        matched_impact_terms TEXT,
        matched_fuel_stems TEXT,
        matched_impact_stems TEXT,
        matched_fuel_subgroups TEXT,
        matched_impact_subgroups TEXT,
        cited_by_count INTEGER,
        primary_location_source_issn_l TEXT,
        is_retracted INTEGER
    )
"""


def load_subgroup_maps_from_config(config: dict) -> tuple[dict[str, str], dict[str, str]]:
    """
    Build {term: subgroup_name} maps for fuel and impact from a pipeline config dict.
    Returns (fuel_map, impact_map).
    """
    groups = config.get("groups", [])
    fuel_map: dict[str, str] = {}
    impact_map: dict[str, str] = {}

    def _build(g: dict) -> dict[str, str]:
        m: dict[str, str] = {}
        for sg in g.get("subgroups", []):
            for t in sg.get("terms", []):
                m[t] = sg["name"]
        return m

    if len(groups) > 0:
        fuel_map = _build(groups[0])
    if len(groups) > 1:
        impact_map = _build(groups[1])
    return fuel_map, impact_map


def backfill_subgroups(
    conn: sqlite3.Connection,
    fuel_subgroup_map: dict[str, str],
    impact_subgroup_map: dict[str, str],
) -> int:
    """
    Update matched_fuel_subgroups / matched_impact_subgroups for records that have
    matched terms but empty subgroups — typically records inserted before the subgroup
    config existed or before subgroups were added to keywords.json.
    Returns the number of rows updated.
    """
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
            fuel_terms = _stdlib_json.loads(fuel_json or "[]")
        except Exception:
            fuel_terms = []
        try:
            impact_terms = _stdlib_json.loads(impact_json or "[]")
        except Exception:
            impact_terms = []

        fuel_sgs   = _compute_subgroups(fuel_terms,   fuel_subgroup_map)
        impact_sgs = _compute_subgroups(impact_terms, impact_subgroup_map)
        updates.append((
            _stdlib_json.dumps(fuel_sgs,   ensure_ascii=False),
            _stdlib_json.dumps(impact_sgs, ensure_ascii=False),
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


def ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE_SQL)
    # Add columns if upgrading from an older schema
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
            conn.execute(
                f"ALTER TABLE filtered_records ADD COLUMN {col} TEXT"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS filtered_records")
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Core record extraction
# ---------------------------------------------------------------------------
def _extract_row(
    record: dict,
    title: str,
    abstract_text: str,
    match: MatchResult,
) -> tuple | None:
    openalex_id = (record.get("id") or "").strip()
    if not openalex_id:
        return None

    doi = (record.get("doi") or "").strip()
    language = (record.get("language") or "").strip().lower()
    open_access = record.get("open_access") or {}
    oa_any_repo = bool(open_access.get("any_repository_has_fulltext"))
    oa_status = open_access.get("oa_status")
    oa_url = open_access.get("oa_url")

    pl = record.get("primary_location") or {}
    pl_is_oa = bool(pl.get("is_oa"))
    pl_url = pl.get("landing_page_url")
    pl_license = pl.get("license")
    pl_src = pl.get("source") or {}

    # Full-text content availability (from snapshot)
    has_fulltext = bool(record.get("has_fulltext"))
    fulltext_origin = record.get("fulltext_origin")

    # Best OA location — licence and PDF URL for downstream full-text retrieval
    boa = record.get("best_oa_location") or {}
    boa_license = boa.get("license")
    boa_pdf_url = boa.get("pdf_url")
    boa_version = boa.get("version")

    # Publisher / citation fields
    cited_by_count = record.get("cited_by_count")
    issn_l = pl_src.get("issn_l")
    is_retracted = 1 if bool(record.get("is_retracted")) else 0

    ai, ac = extract_authors_institutions_and_countries(record)

    is_oa_val = int(bool(open_access.get("is_oa")))
    return (
        openalex_id,
        doi,
        title,
        is_oa_val,
        abstract_text,
        record.get("publication_year"),
        record.get("publication_date"),
        record.get("fwci"),
        language,
        record.get("type"),
        is_oa_val,
        1 if oa_any_repo else 0,
        oa_status,
        oa_url,
        1 if pl_is_oa else 0,
        pl_url,
        pl_license,
        pl_src.get("display_name"),
        pl_src.get("host_organization_name"),
        pl_src.get("type"),
        1 if has_fulltext else 0,
        fulltext_origin,
        boa_license,
        boa_pdf_url,
        boa_version,
        ai,
        ac,
        _stdlib_json.dumps(match.fuel_terms, ensure_ascii=False),
        _stdlib_json.dumps(match.impact_terms, ensure_ascii=False),
        _stdlib_json.dumps(match.fuel_stems, ensure_ascii=False),
        _stdlib_json.dumps(match.impact_stems, ensure_ascii=False),
        _stdlib_json.dumps(match.fuel_subgroups, ensure_ascii=False),
        _stdlib_json.dumps(match.impact_subgroups, ensure_ascii=False),
        cited_by_count,
        issn_l,
        is_retracted,
    )


# ---------------------------------------------------------------------------
# Process to in-memory rows
# ---------------------------------------------------------------------------
def process_records_to_rows(
    input_gz_path: Path,
    max_records: int | None = None,
) -> tuple[int, int, list[tuple], dict[str, int], list[str]]:
    """
    Process one .gz JSONL file and return matched rows in memory.

    Returns (total_seen, total_matched, rows, skip_stats, errors).
    errors is a list of human-readable error strings for logging.

    """
    if not _terms_initialized:
        _init_terms()

    matched = 0
    total = 0
    rows: list[tuple] = []
    skips: dict[str, int] = {}
    errors: list[str] = []

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
                    _s("empty_line")
                    continue

                # --- raw-line pre-screen: language only ---
                if (
                    '"language": "en"' not in line
                    and '"language":"en"' not in line
                ):
                    _s("not_english")
                    continue

                try:
                    record = _loads(line)
                except _JSONDecodeError as e:
                    _s("json_error")
                    errors.append(
                        f"line {total}: JSON parse error: {e}"
                    )
                    continue

                doi = (record.get("doi") or "").strip()
                if not doi:
                    _s("no_doi")
                    continue

                language = (record.get("language") or "").strip().lower()
                if language != "en":
                    _s("not_english")
                    continue

                abstract_ii = record.get("abstract_inverted_index") or {}
                if not abstract_ii:
                    _s("no_abstract")
                    continue

                title = (record.get("title") or "").strip()

                try:
                    mr = keyword_check_smart(title, abstract_ii)
                except Exception as e:
                    _s("processing_error")
                    errors.append(
                        f"line {total}: keyword check error: {e}"
                    )
                    continue

                if not mr.matched:
                    _s("no_keywords")
                    continue
                if not mr.abstract_text:
                    _s("empty_abstract")
                    continue

                try:
                    row = _extract_row(record, title, mr.abstract_text, mr)
                except Exception as e:
                    _s("processing_error")
                    errors.append(
                        f"line {total}: row extraction error: {e}"
                    )
                    continue

                if row is None:
                    _s("no_id")
                    continue

                rows.append(row)
                matched += 1

    except Exception as e:
        errors.append(
            f"FATAL reading {input_gz_path.name}: "
            f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        )

    return total, matched, rows, skips, errors


# ---------------------------------------------------------------------------
# Standalone DB-writing entry point (CLI use)
# ---------------------------------------------------------------------------
def process_records(
    input_gz_path: Path,
    output_db_path: Path,
    max_records: int | None = None,
    commit_interval: int = 10_000,
    append_mode: bool = False,
) -> tuple[int, int]:
    conn = sqlite3.connect(output_db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")
    if append_mode:
        ensure_db(conn)
    else:
        init_db(conn)

    total, inserted, rows, _skips, errs = process_records_to_rows(
        input_gz_path, max_records
    )

    if rows:
        conn.executemany(_INSERT_SQL, rows)
        conn.commit()

    # Backfill subgroups for any records with empty subgroups (e.g. from a previous
    # run that predated subgroup config) using the currently active subgroup maps.
    if FUEL_SUBGROUP_MAP or IMPACT_SUBGROUP_MAP:
        n_backfilled = backfill_subgroups(conn, FUEL_SUBGROUP_MAP, IMPACT_SUBGROUP_MAP)
        if n_backfilled:
            print(f"Backfilled subgroups for {n_backfilled:,} records.")

    conn.close()

    print(f"Processed records: {total}")
    print(f"Saved records: {inserted}")
    print(f"Unsaved records: {total - inserted}")
    print(f"Database: {output_db_path}")
    if errs:
        print(f"Errors ({len(errs)}):")
        for e in errs[:10]:
            print(f"  {e}")

    return total, inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter OpenAlex records from a .gz JSONL file into SQLite."
    )
    parser.add_argument("--input", type=Path, default=INPUT_GZ_PATH)
    parser.add_argument("--output-db", type=Path, default=OUTPUT_DB_PATH)
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--commit-interval", type=int, default=10_000)
    parser.add_argument(
        "--keyword-config",
        type=Path,
        default=None,
        metavar="JSON",
        help="JSON file with {fuel_terms: [...], impact_terms: [...]}. "
             "Defaults to built-in term lists when omitted.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    import json as _json_main
    args = parse_args()
    if args.keyword_config:
        _cfg = _json_main.loads(args.keyword_config.read_text(encoding="utf-8"))
        _grps = _cfg.get("groups", [])

        def _flatten_grp(g):
            if "subgroups" in g:
                return [t for sg in g["subgroups"] for t in sg.get("terms", [])]
            return g.get("terms", [])

        def _extract_subgroups(g):
            if "subgroups" in g:
                return [{"name": sg["name"], "terms": sg.get("terms", [])} for sg in g["subgroups"]]
            return None

        _ft = _flatten_grp(_grps[0]) if len(_grps) > 0 else _cfg.get("fuel_terms")
        _it = _flatten_grp(_grps[1]) if len(_grps) > 1 else _cfg.get("impact_terms")
        _fsg = _extract_subgroups(_grps[0]) if len(_grps) > 0 else None
        _isg = _extract_subgroups(_grps[1]) if len(_grps) > 1 else None
        _init_terms(_ft, _it, _fsg, _isg)
    else:
        _init_terms()
    process_records(
        input_gz_path=args.input,
        output_db_path=args.output_db,
        max_records=args.max_records,
        commit_interval=args.commit_interval,
    )
