
"""Script (voice-over + on-screen captions) generation and fact checking.

Template writer:
  * deterministic, uses only supplied facts. No LLM or network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple

from .models import MAX_FEATURES, PropertyDetails

KIND_TITLE, KIND_FEATURE, KIND_GALLERY, KIND_PRICE, KIND_CTA = (
    "title", "feature", "gallery", "price", "cta"
)


@dataclass
class Scene:
    kind: str
    headline: str
    caption: str
    narration: str
    image_index: int = 0


DEFAULT_CTA = "Enquire now to book a visit."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def speech_normalize(text: str) -> str:
    """Make text TTS-friendly (currency, abbreviations). Display text is untouched."""
    t = text
    cur = {"₹": "rupees", "$": "dollars", "€": "euros", "£": "pounds"}

    m = re.match(r"^\s*([₹$€£])\s*([\d.,]+)\s*([A-Za-z]+)?\s*(.*)$", t)

    if m and not re.search(r"[₹$€£]", m.group(4) or ""):
        sym, num, unit, rest = m.groups()
        unit = {
            "cr": "crore",
            "l": "lakh",
            "m": "million",
            "k": "thousand",
            "mn": "million",
        }.get((unit or "").lower(), unit or "")

        t = " ".join(x for x in [num, unit, cur[sym], rest] if x)

    for sym, word in cur.items():
        t = t.replace(sym, f" {word} ")

    t = re.sub(r"\bBHK\b", "B H K", t, flags=re.I)
    t = re.sub(r"\b(\d+)\s*[x×/]\s*(\d+)\b", r"\1 by \2", t)
    t = re.sub(r"\bsq\.?\s*ft\b", "square feet", t, flags=re.I)
    t = re.sub(r"\bsq\.?\s*m\b", "square metres", t, flags=re.I)
    t = re.sub(r"\bCCTV\b", "C C T V", t)
    t = re.sub(r"\bEV\b", "E V", t)
    t = re.sub(r"[|•·]", ", ", t)

    return re.sub(r"\s+", " ", t).strip()


def _article(word: str) -> str:
    return (
        "an"
        if word[:1].lower() in "aeiou"
        or re.match(r"^\d*\s*(8|11|18)", word)
        else "a"
    )


# ---------------------------------------------------------------------------
# Template writer
# ---------------------------------------------------------------------------

def template_script(
    d: PropertyDetails,
    features: Optional[List[str]] = None
) -> List[Scene]:

    feats = features if features is not None else [
        f.text for f in d.features
    ][:MAX_FEATURES]

    scenes: List[Scene] = []

    # Opening title
    sub_parts = [p for p in (d.property_type, d.location) if p]
    caption = "  •  ".join(sub_parts)

    type_in_name = (
        bool(d.property_type)
        and d.property_type.lower() in d.name.lower()
    )

    if d.property_type and d.location and not type_in_name:
        narr = (
            f"Introducing {d.name}, "
            f"{_article(d.property_type)} {d.property_type} "
            f"in {d.location}."
        )
    elif d.property_type and not type_in_name:
        narr = (
            f"Introducing {d.name}, "
            f"{_article(d.property_type)} {d.property_type}."
        )
    elif d.location:
        narr = f"Introducing {d.name}, located in {d.location}."
    else:
        narr = f"Introducing {d.name}."

    scenes.append(Scene(KIND_TITLE, d.name, caption, narr))

    # Feature scenes
    leads = [
        "Key feature: {f}.",
        "Also included: {f}.",
        "Plus: {f}.",
        "And: {f}.",
        "Finally: {f}.",
    ]

    for i, f in enumerate(feats):
        lead = (
            leads[min(i, len(leads) - 1)]
            if len(feats) > 1
            else "Key feature: {f}."
        )

        scenes.append(
            Scene(
                KIND_FEATURE,
                f,
                "Feature highlight" if i == 0 else "",
                lead.format(f=f),
            )
        )

    # Price
    if d.price:
        scenes.append(
            Scene(
                KIND_PRICE,
                d.price,
                "Priced at",
                f"Priced at {d.price}.",
            )
        )

    # Call to action
    cta = d.cta_text or DEFAULT_CTA

    scenes.append(
        Scene(
            KIND_CTA,
            cta,
            d.contact,
            cta if cta.endswith((".", "!", "?")) else cta + ".",
        )
    )

    return scenes


# ---------------------------------------------------------------------------
# Fact checking
# ---------------------------------------------------------------------------

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")

RISKY_CLAIMS = [
    "metro", "airport", "school", "hospital", "mall", "railway",
    "highway", "rera", "discount", "offer", "guarantee",
    "guaranteed", "emi", "loan", "possession", "ready to move",
    "ready-to-move", "sea view", "freehold", "lakh", "crore",
    "million", "per month", "rental", "roi", "return",
    "investment", "award", "best", "number one", "no. 1",
    "limited", "hurry", "last unit", "sold out",
]

_GENERIC_OK = {
    "introducing", "key", "feature", "also", "included", "plus",
    "and", "finally", "priced", "at", "enquire", "now", "book",
    "visit", "call", "contact", "today", "discover", "your", "new",
    "home", "in", "a", "an", "the", "with", "for", "to", "of",
    "is", "are", "this", "take", "look", "closer", "see", "more",
    "live", "welcome", "tour", "explore", "from", "get", "touch",
    "us", "our", "schedule", "message", "on", "it", "highlight",
    "highlights", "features", "details", "property", "listing",
    "available", "lets", "let's", "start", "by", "you", "we",
    "can", "be", "will", "here", "or", "as", "that", "its",
    "them", "all", "enquiry", "inquiry", "interested", "ready",
    "when", "where", "feel", "come",
}


def _norm_words(text: str) -> set:
    return {w.lower().strip("'-") for w in _WORD.findall(text)}


def check_facts(
    d: PropertyDetails,
    scenes: List[Scene]
) -> List[str]:
    """Return problems; empty list means claims trace back to input."""

    facts = d.facts_text()

    fact_words = (
        _norm_words(facts)
        | _norm_words(speech_normalize(facts))
    )

    fact_text_l = facts.lower()
    fact_nums = {
        n.replace(",", "") for n in _NUM.findall(facts)
    }

    problems: List[str] = []

    for i, s in enumerate(scenes, 1):
        text = " ".join([
            s.headline,
            s.caption,
            s.narration,
        ])

        for n in _NUM.findall(text):
            if n.replace(",", "") not in fact_nums:
                problems.append(
                    f"scene {i}: number '{n}' "
                    "is not in the supplied details"
                )

        low = text.lower()

        for claim in RISKY_CLAIMS:
            if (
                re.search(rf"\b{re.escape(claim)}\b", low)
                and not re.search(
                    rf"\b{re.escape(claim)}\b",
                    fact_text_l,
                )
            ):
                problems.append(
                    f"scene {i}: unsupported claim '{claim}'"
                )

        for field_text in (
            s.headline,
            s.caption,
            s.narration,
        ):
            for w in _norm_words(field_text):
                if (
                    w in fact_words
                    or w in _GENERIC_OK
                    or len(w) <= 2
                ):
                    continue

                if (
                    re.search(
                        rf"\b{re.escape(w.capitalize())}\b",
                        field_text,
                    )
                    and not re.search(
                        rf"\b{re.escape(w)}\b",
                        fact_text_l,
                    )
                ):
                    if not re.search(
                        rf"(^|[.!?]\s+)"
                        f"{re.escape(w.capitalize())}\b",
                        field_text,
                    ):
                        problems.append(
                            f"scene {i}: unknown proper noun "
                            f"'{w.capitalize()}'"
                        )

    return sorted(set(problems))


# ---------------------------------------------------------------------------
# Script generation (template only)
# ---------------------------------------------------------------------------

def generate_script(
    d: PropertyDetails,
) -> Tuple[List[Scene], List[str], str]:
    """Return (scenes, warnings, writer_used)."""

    warnings: List[str] = []

    feats = [
        f.text for f in d.features
    ][:MAX_FEATURES]

    if len(d.features) > MAX_FEATURES:
        warnings.append(
            f"{len(d.features)} features supplied; "
            f"the first {MAX_FEATURES} are shown "
            "to keep the video under ~40 s."
        )

    scenes = template_script(d, feats)

    problems = check_facts(d, scenes)

    if problems:
        warnings.append(
            "Template script flagged by fact-check: "
            + "; ".join(problems[:3])
        )

    return scenes, warnings, "template"


# ---------------------------------------------------------------------------
# Scene serialization
# ---------------------------------------------------------------------------

def scenes_to_json(scenes: List[Scene]) -> str:
    return json.dumps(
        [asdict(s) for s in scenes],
        indent=2,
        ensure_ascii=False,
    )
