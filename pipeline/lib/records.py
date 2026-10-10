"""OpenAlex work records: journal detection, screening and flattening to rows."""
from __future__ import annotations

import json

import pyarrow as pa

from pipeline.lib.config import Stage2Config

try:
    import orjson

    def loads(raw: bytes | str):
        return orjson.loads(raw)

    JSONDecodeError = orjson.JSONDecodeError
except ImportError:  # pragma: no cover
    loads = json.loads
    JSONDecodeError = json.JSONDecodeError


# Reasons a record is dropped in stage 2, in the order they are checked.
DROP_REASONS: dict[str, str] = {
    "json_error": "Unparseable JSON line",
    "wrong_type": "Work type not in allowed types",
    "no_source": "Primary location has no source",
    "source_not_journal": "Primary source is not a journal",
    "paratext": "Paratext (front matter, TOC, ...)",
    "retracted": "Retracted",
    "xpac": "Expansion-pack (is_xpac) record",
    "no_doi": "No DOI",
    "language_unknown": "Language unknown (null)",
    "language": "Language not allowed",
    "no_abstract": "No abstract",
}


def short_id(url: str | None) -> str | None:
    """'https://openalex.org/W123' -> 'W123'."""
    return url.rsplit("/", 1)[-1] if url else None


def bare_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            return doi[len(prefix):]
    return doi or None


def invert_abstract(inverted: dict | None) -> str:
    """Rebuild abstract text from OpenAlex's {word: [positions]} index."""
    if not inverted:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inverted.items():
        for pos in positions or ():
            slots.setdefault(pos, word)
    return " ".join(slots[i] for i in sorted(slots)).strip()


def journal_location(rec: dict, allow_fallback: bool) -> tuple[dict | None, bool, str | None]:
    """Find the journal a work was published in.

    Returns (location, from_fallback, drop_reason). The primary location is
    OpenAlex's version of record; with allow_fallback, a work whose primary
    location is a repository (PubMed, arXiv, ...) can still be assigned to a
    journal listed among its other locations, preferring the published version.
    """
    pl = rec.get("primary_location") or {}
    src = pl.get("source") or {}
    if src.get("type") == "journal":
        return pl, False, None
    if allow_fallback:
        candidates = [loc for loc in rec.get("locations") or []
                      if (loc.get("source") or {}).get("type") == "journal"]
        if candidates:
            published = [c for c in candidates if c.get("version") == "publishedVersion"]
            return (published or candidates)[0], True, None
    return None, False, ("source_not_journal" if src else "no_source")


def screen(rec: dict, crit: Stage2Config) -> tuple[str | None, dict | None, bool]:
    """Apply the stage 2 rules. Returns (drop_reason or None, journal location, from_fallback)."""
    if (rec.get("type") or "") not in crit.work_types:
        return "wrong_type", None, False
    if crit.require_journal_source:
        loc, fallback, reason = journal_location(rec, crit.allow_location_fallback)
        if reason:
            return reason, None, False
    else:
        loc, fallback = rec.get("primary_location") or {}, False
    if crit.exclude_paratext and rec.get("is_paratext"):
        return "paratext", None, False
    if crit.exclude_retracted and rec.get("is_retracted"):
        return "retracted", None, False
    if crit.exclude_xpac and rec.get("is_xpac"):
        return "xpac", None, False
    if crit.require_doi and not rec.get("doi"):
        return "no_doi", None, False
    if crit.languages:
        lang = rec.get("language")
        if not lang:
            if not crit.allow_unknown_language:
                return "language_unknown", None, False
        elif lang not in crit.languages:
            return "language", None, False
    if crit.require_abstract and not rec.get("abstract_inverted_index"):
        return "no_abstract", None, False
    return None, loc, fallback


# ---------------------------------------------------------------------------
# Flat row schema (stage 2 output; stage 3 adds keyword-match columns)
# ---------------------------------------------------------------------------

_STR, _INT, _F, _B = pa.string(), pa.int64(), pa.float64(), pa.bool_()
_LIST = pa.list_(pa.string())

WORK_FIELDS: list[tuple[str, pa.DataType, str]] = [
    ("openalex_id", _STR, "OpenAlex work ID, short form (W1234567890)"),
    ("doi", _STR, "DOI without the https://doi.org/ prefix, lower-case"),
    ("title", _STR, "Work title"),
    ("abstract_text", _STR, "Abstract rebuilt from OpenAlex's inverted index (punctuation tokens may be missing)"),
    ("publication_year", _INT, "Publication year"),
    ("publication_date", _STR, "Publication date (ISO)"),
    ("type", _STR, "OpenAlex work type (article, review)"),
    ("language", _STR, "Detected language code"),
    ("journal_id", _STR, "OpenAlex source ID of the journal (S...) — join key to the journals table"),
    ("journal_name", _STR, "Journal name (OpenAlex display name)"),
    ("journal_issn_l", _STR, "Linking ISSN — join key for external rankings (SJR, JCR, ...)"),
    ("journal_issns", _LIST, "All ISSNs of the journal"),
    ("publisher", _STR, "Journal host organization (publisher)"),
    ("journal_is_core", _B, "Journal is in the CWTS core list"),
    ("journal_is_in_doaj", _B, "Journal is listed in DOAJ"),
    ("journal_listed_in", _LIST, "Journal lists (e.g. jufo-2, norway-1, erih-plus, cwts-core)"),
    ("journal_from_fallback", _B, "Journal taken from a non-primary location (stage2.allow_location_fallback)"),
    ("version", _STR, "Version at the journal location (publishedVersion, acceptedVersion, ...)"),
    ("landing_page_url", _STR, "Landing page at the journal"),
    ("license", _STR, "License at the journal location"),
    ("volume", _STR, "Volume"),
    ("issue", _STR, "Issue"),
    ("first_page", _STR, "First page"),
    ("last_page", _STR, "Last page (PubMed-style abbreviations are not expanded)"),
    ("is_oa", _B, "Open access (any location)"),
    ("oa_status", _STR, "OA status: gold, diamond, hybrid, bronze, green, closed"),
    ("oa_url", _STR, "Best open-access URL"),
    ("any_repository_has_fulltext", _B, "A repository holds the full text"),
    ("best_oa_pdf_url", _STR, "PDF URL at the best OA location"),
    ("best_oa_version", _STR, "Version at the best OA location"),
    ("best_oa_license", _STR, "License at the best OA location"),
    ("authors_count", _INT, "Number of authors"),
    ("author_names", _LIST, "Author display names in order"),
    ("institutions", _LIST, "Distinct institution names across authors"),
    ("countries", _LIST, "Distinct author country codes"),
    ("cited_by_count", _INT, "Citation count"),
    ("fwci", _F, "Field-weighted citation impact (0 often means no data for old works)"),
    ("citation_percentile", _F, "Citation percentile vs works of same field and year (0-1)"),
    ("is_top_1_percent", _B, "In top 1% by normalized citations"),
    ("is_top_10_percent", _B, "In top 10% by normalized citations"),
    ("referenced_works_count", _INT, "Number of references"),
    ("primary_topic_id", _STR, "OpenAlex topic ID (T...)"),
    ("primary_topic", _STR, "Primary topic name"),
    ("primary_topic_score", _F, "Topic classifier confidence"),
    ("subfield", _STR, "Topic subfield"),
    ("field", _STR, "Topic field"),
    ("domain", _STR, "Topic domain"),
    ("is_retracted", _B, "Retracted"),
    ("raw_json", _STR, "Full original OpenAlex record (JSON); null when stage2.store_raw_json is false"),
]

WORK_SCHEMA = pa.schema([(name, typ) for name, typ, _ in WORK_FIELDS])
COLUMN_DOCS = {name: doc for name, _, doc in WORK_FIELDS}


def _name(obj: dict | None) -> str | None:
    return (obj or {}).get("display_name")


def work_row(rec: dict, loc: dict | None, from_fallback: bool,
             raw_line: bytes | None, store_raw: bool) -> dict:
    loc = loc or {}
    src = loc.get("source") or {}
    oa = rec.get("open_access") or {}
    boa = rec.get("best_oa_location") or {}
    biblio = rec.get("biblio") or {}
    cnp = rec.get("citation_normalized_percentile") or {}
    topic = rec.get("primary_topic") or {}

    authors: list[str] = []
    institutions: list[str] = []
    countries: list[str] = []
    for a in rec.get("authorships") or []:
        if name := _name(a.get("author")):
            authors.append(name)
        for inst in a.get("institutions") or []:
            if (n := inst.get("display_name")) and n not in institutions:
                institutions.append(n)
        for c in a.get("countries") or []:
            if c and c not in countries:
                countries.append(c)

    return {
        "openalex_id": short_id(rec.get("id")),
        "doi": bare_doi(rec.get("doi")),
        "title": (rec.get("title") or rec.get("display_name") or "").strip(),
        "abstract_text": invert_abstract(rec.get("abstract_inverted_index")),
        "publication_year": rec.get("publication_year"),
        "publication_date": rec.get("publication_date"),
        "type": rec.get("type"),
        "language": rec.get("language"),
        "journal_id": short_id(src.get("id")),
        "journal_name": src.get("display_name"),
        "journal_issn_l": src.get("issn_l"),
        "journal_issns": src.get("issn") or [],
        "publisher": src.get("host_organization_name"),
        "journal_is_core": src.get("is_core"),
        "journal_is_in_doaj": src.get("is_in_doaj"),
        "journal_listed_in": src.get("listed_in") or [],
        "journal_from_fallback": from_fallback,
        "version": loc.get("version"),
        "landing_page_url": loc.get("landing_page_url"),
        "license": loc.get("license"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "first_page": biblio.get("first_page"),
        "last_page": biblio.get("last_page"),
        "is_oa": oa.get("is_oa"),
        "oa_status": oa.get("oa_status"),
        "oa_url": oa.get("oa_url"),
        "any_repository_has_fulltext": oa.get("any_repository_has_fulltext"),
        "best_oa_pdf_url": boa.get("pdf_url"),
        "best_oa_version": boa.get("version"),
        "best_oa_license": boa.get("license"),
        "authors_count": rec.get("authors_count", len(authors)),
        "author_names": authors,
        "institutions": institutions,
        "countries": countries,
        "cited_by_count": rec.get("cited_by_count"),
        "fwci": rec.get("fwci"),
        "citation_percentile": cnp.get("value"),
        "is_top_1_percent": cnp.get("is_in_top_1_percent"),
        "is_top_10_percent": cnp.get("is_in_top_10_percent"),
        "referenced_works_count": rec.get("referenced_works_count"),
        "primary_topic_id": short_id(topic.get("id")),
        "primary_topic": topic.get("display_name"),
        "primary_topic_score": topic.get("score"),
        "subfield": _name(topic.get("subfield")),
        "field": _name(topic.get("field")),
        "domain": _name(topic.get("domain")),
        "is_retracted": rec.get("is_retracted"),
        "raw_json": raw_line.decode("utf-8") if (store_raw and raw_line is not None) else None,
    }


# ---------------------------------------------------------------------------
# Journals table (from the OpenAlex `sources` entity)
# ---------------------------------------------------------------------------

JOURNAL_FIELDS: list[tuple[str, pa.DataType, str]] = [
    ("journal_id", _STR, "OpenAlex source ID (S...)"),
    ("journal_name", _STR, "Journal name"),
    ("issn_l", _STR, "Linking ISSN"),
    ("issns", _LIST, "All ISSNs"),
    ("publisher", _STR, "Host organization name"),
    ("country_code", _STR, "Country of the journal"),
    ("works_count", _INT, "Works published (all years)"),
    ("cited_by_count", _INT, "Total citations"),
    ("two_yr_mean_citedness", _F, "Impact-factor-like: mean citations in year N to works from N-1, N-2"),
    ("h_index", _INT, "h-index"),
    ("i10_index", _INT, "Works with >=10 citations"),
    ("is_oa", _B, "Fully open-access journal"),
    ("is_in_doaj", _B, "Listed in DOAJ"),
    ("is_core", _B, "In CWTS core list"),
    ("apc_usd", _F, "Article processing charge in USD"),
    ("top_field", _STR, "Most frequent field among the journal's topics"),
]
JOURNAL_SCHEMA = pa.schema([(n, t) for n, t, _ in JOURNAL_FIELDS])


def journal_row(src: dict) -> dict | None:
    if src.get("type") != "journal":
        return None
    stats = src.get("summary_stats") or {}
    topics = src.get("topics") or []
    top_field = _name((topics[0] or {}).get("field")) if topics else None
    return {
        "journal_id": short_id(src.get("id")),
        "journal_name": src.get("display_name"),
        "issn_l": src.get("issn_l"),
        "issns": src.get("issn") or [],
        "publisher": src.get("host_organization_name"),
        "country_code": src.get("country_code"),
        "works_count": src.get("works_count"),
        "cited_by_count": src.get("cited_by_count"),
        "two_yr_mean_citedness": stats.get("2yr_mean_citedness"),
        "h_index": stats.get("h_index"),
        "i10_index": stats.get("i10_index"),
        "is_oa": src.get("is_oa"),
        "is_in_doaj": src.get("is_in_doaj"),
        "is_core": src.get("is_core"),
        "apc_usd": src.get("apc_usd"),
        "top_field": top_field,
    }
