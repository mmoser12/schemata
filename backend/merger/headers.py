"""Header parsing and header -> target matching.

Order of attempts (the LLM is only a last resort, per the design rule):
  1. exact synonym match on the normalized name   -> method "rule"
  2. fuzzy string match against all synonyms       -> method "fuzzy"
  3. optional llm_matcher callback for leftovers   -> method "llm"
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Callable, Optional

from .units import token_to_unit

# "Temp (C)", "Mass [mg]", "Pressure {kPa}"
_BRACKET_UNIT = re.compile(r"^(?P<name>.*?)\s*[\(\[\{]\s*(?P<unit>[^\)\]\}]+?)\s*[\)\]\}]\s*$")


def normalize_name(name: str) -> str:
    """'Sample_ID ' -> 'sample id'."""
    name = re.sub(r"([a-z]{2,})([A-Z])", r"\1 \2", str(name))  # SampleID -> Sample ID, but pH stays
    name = re.sub(r"[^0-9a-zA-Z]+", " ", name)
    return " ".join(name.lower().split())


def split_header(raw: str) -> tuple[str, Optional[str]]:
    """Split a raw header into (normalized name, unit token or None).

    The unit token keeps its case, because case matters for units (mM vs mm).
    'Temp (C)' -> ('temp', 'C'); 'temperature_K' -> ('temperature', 'K'); 'Date' -> ('date', None)
    """
    raw = str(raw).strip()
    m = _BRACKET_UNIT.match(raw)
    if m:
        token = m.group("unit").strip().replace(" ", "")
        if token_to_unit(token) is not None:
            return normalize_name(m.group("name")), token

    # Trailing unit after a separator: temperature_K, mass-g, P_kPa
    parts = re.split(r"[_\-\s]+", raw)
    if len(parts) >= 2 and token_to_unit(parts[-1]) is not None:
        return normalize_name(" ".join(parts[:-1])), parts[-1]

    return normalize_name(raw), None


@dataclass
class HeaderMatch:
    target: Optional[str]
    confidence: float
    method: str  # "rule" | "fuzzy" | "llm" | "none"


LLMMatcher = Callable[[str, list[str]], Optional[str]]


def match_header(name: str, targets: dict[str, dict], llm_matcher: Optional[LLMMatcher] = None,
                 fuzzy_cutoff: float = 0.75) -> HeaderMatch:
    """Map a normalized header name to a target column."""
    for target, spec in targets.items():
        if name == target.replace("_", " ") or name in spec.get("synonyms", []):
            return HeaderMatch(target, 1.0, "rule")

    lookup = {}
    for target, spec in targets.items():
        for syn in [target.replace("_", " "), *spec.get("synonyms", [])]:
            lookup[syn] = target
    close = difflib.get_close_matches(name, list(lookup), n=1, cutoff=fuzzy_cutoff)
    if close:
        ratio = difflib.SequenceMatcher(None, name, close[0]).ratio()
        return HeaderMatch(lookup[close[0]], round(ratio, 2), "fuzzy")

    if llm_matcher is not None:
        guess = llm_matcher(name, list(targets))
        if guess in targets:
            return HeaderMatch(guess, 0.6, "llm")

    return HeaderMatch(None, 0.0, "none")
