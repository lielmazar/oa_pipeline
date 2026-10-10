"""Keyword-group matching on titles and abstracts.

A record matches when every keyword group (e.g. "fuel" and "impact") has at
least one term in its title or abstract. Matching is done on normalised,
lightly-stemmed tokens, so "emissions" matches "emission", "bio-oil" matches
"bio oil" and "biooil", and "PM2.5" matches "pm25".

Keyword config format (the ``keywords`` section of research_schema.json, or
a standalone file with the same shape):

    {"groups": [
        {"key": "fuel", "name": "Fuel Terms",
         "subgroups": [{"name": "Diesel & HVO", "terms": ["diesel", ...]}]},
        {"key": "impact", "name": "Impact Terms", "subgroups": [...]}
    ]}
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Normalisation and stemming
# ---------------------------------------------------------------------------

_UNICODE_REMAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉ₓ⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789x0123456789")
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
    return _RE_SEPARATOR.sub(" ", text)


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
    return [stem_token(t) for t in _RE_TOKENS.findall(normalize_text(text))]


# ---------------------------------------------------------------------------
# Keyword groups
# ---------------------------------------------------------------------------

Phrase = tuple[str, ...]


@dataclass
class KeywordGroup:
    key: str
    name: str
    subgroups: list[dict]
    index: dict[str, list[Phrase]] = field(default_factory=dict)   # first token -> phrases
    reverse: dict[Phrase, str] = field(default_factory=dict)       # phrase -> original term
    term_subgroup: dict[str, str] = field(default_factory=dict)    # original term -> subgroup

    @property
    def terms(self) -> list[str]:
        return [t for sg in self.subgroups for t in sg.get("terms", [])]

    def compile(self) -> "KeywordGroup":
        for sg in self.subgroups:
            for term in sg.get("terms", []):
                self.term_subgroup.setdefault(term, sg["name"])
                tokens = normalize_and_stem(term)
                if not tokens:
                    continue
                variants = [tuple(tokens)]
                if len(tokens) > 1:
                    variants.append(("".join(tokens),))   # "bio oil" also matches "biooil"
                for phrase in variants:
                    bucket = self.index.setdefault(phrase[0], [])
                    if phrase not in bucket:
                        bucket.append(phrase)
                    self.reverse.setdefault(phrase, term)
        return self

    def find(self, tokens: list[str]) -> dict[str, str]:
        """Return {original_term: stemmed_phrase} for every term present in tokens."""
        found: dict[str, str] = {}
        n = len(tokens)
        for i, tok in enumerate(tokens):
            for phrase in self.index.get(tok, ()):
                plen = len(phrase)   # phrase[0] == tok is guaranteed by the index
                if plen == 1 or (i + plen <= n and all(
                        tokens[i + j] == phrase[j] for j in range(1, plen))):
                    found.setdefault(self.reverse[phrase], " ".join(phrase))
        return found


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_terms?$", "", s) or "group"


def load_keyword_groups(path: Path) -> tuple[list[KeywordGroup], str]:
    """Load groups from a keywords file. Returns (groups, sha256 of the keyword section)."""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    section = cfg.get("keywords", cfg)
    groups_cfg = section.get("groups") or []
    if len(groups_cfg) < 1:
        raise ValueError(f"No keyword groups found in {path}")
    groups: list[KeywordGroup] = []
    for g in groups_cfg:
        subgroups = g.get("subgroups") or [{"name": g["name"], "terms": g.get("terms", [])}]
        key = g.get("key") or _slug(g["name"])
        if any(x.key == key for x in groups):
            raise ValueError(f"Duplicate keyword group key {key!r}")
        groups.append(KeywordGroup(key=key, name=g["name"], subgroups=subgroups).compile())
    digest = hashlib.sha256(json.dumps(section, sort_keys=True).encode()).hexdigest()
    return groups, digest


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass
class GroupMatch:
    terms: list[str]
    stems: list[str]
    subgroups: list[str]
    in_title: bool

    @property
    def hit(self) -> bool:
        return bool(self.terms)


def match_groups(groups: list[KeywordGroup], title: str, abstract: str) -> list[GroupMatch]:
    """Match every group against title and abstract (matched separately, so a
    phrase never spans the title/abstract boundary)."""
    title_toks = normalize_and_stem(title) if title else []
    abs_toks = normalize_and_stem(abstract) if abstract else []
    vocab = set(title_toks) | set(abs_toks)
    results: list[GroupMatch] = []
    for g in groups:
        if vocab.isdisjoint(g.index.keys()):
            results.append(GroupMatch([], [], [], False))
            continue
        in_title = g.find(title_toks)
        found = dict(in_title)
        for term, stem in g.find(abs_toks).items():
            found.setdefault(term, stem)
        subgroups: list[str] = []
        for term in found:
            sg = g.term_subgroup.get(term)
            if sg and sg not in subgroups:
                subgroups.append(sg)
        results.append(GroupMatch(list(found), list(dict.fromkeys(found.values())),
                                  subgroups, bool(in_title)))
    return results
