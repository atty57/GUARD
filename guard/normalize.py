"""Rewrite disguised numbers so Tier 0 can read them (owner: Hyeseong).

Tier 0 matches numbers-with-units. A query can state a protected value without writing it that
way. This module turns three such forms into plain phrases, each tagged with the leak type it
would be, so ``check.tier0`` can run its usual matching on them:

- ``encoding``  the number is spelled or written oddly: "two hundred seventy-two Wh/kg",
  "2.72e2 Wh/kg", "745C" (a bare C after a number of 100 or more; "1C" stays a charge rate).
- ``relative``  the value is given against another number: "7 Wh/kg above 265 Wh/kg",
  "twice 600 cycles", "3.5 points above 90% yield".
- ``derived``   the value follows from other quantities in the query: "68 Wh cell weighing
  250 g" (Wh/kg), "scrap rate 6.5%" (yield), "half a day" (hours).

Each rule is general: it reads a form of writing, not a particular secret. Nothing here knows
the policy's values; ``tier0`` decides whether a rewritten phrase hits one.
"""

from __future__ import annotations

import re
from typing import Literal

Kind = Literal["encoding", "relative", "derived"]

_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"

# --- spelled-out numbers ------------------------------------------------------------------------
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000}
_DIGIT = {**_ONES}
_WORD = "|".join(sorted([*_ONES, *_TENS, *_SCALES, "and", "point"], key=len, reverse=True))
_SPELLED = re.compile(rf"\b(?:{_WORD})(?:[\s-]+(?:{_WORD}))*\b", re.I)


def _words_value(words: list[str]) -> float | None:
    """Value of a run of number words, or None if it is not a number ("and" alone, etc.)."""
    if "point" in words:
        i = words.index("point")
        whole, frac = words[:i], words[i + 1:]
        if not frac or any(w not in _DIGIT for w in frac):
            return None
        w = _words_value(whole) if whole else 0.0
        if w is None:
            return None
        return float(f"{int(w)}." + "".join(str(_DIGIT[x]) for x in frac))
    total, cur, seen = 0, 0, False
    for w in words:
        if w == "and":
            continue
        if w in _ONES:
            cur += _ONES[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w == "thousand":
            total += (cur or 1) * 1000
            cur = 0
        else:
            return None
        seen = True
    return float(total + cur) if seen else None


def _spelled_to_digits(text: str) -> str:
    def repl(m: re.Match) -> str:
        words = [w.lower() for w in re.split(r"[\s-]+", m.group(0)) if w]
        while words and words[-1] in ("and", "point"):       # "... and" at the end is prose
            words.pop()
        v = _words_value(words)
        if v is None:
            return m.group(0)
        return f"{v:g}" if v != int(v) else str(int(v))
    return _SPELLED.sub(repl, text)


# --- other encodings ----------------------------------------------------------------------------
_SCI = re.compile(rf"\b(\d+(?:\.\d+)?)\s*(?:[eE]\s*|[x×]\s*10\s*\^\s*)([+-]?\d+)\b")
_BARE_C = re.compile(r"\b(\d{3,4}(?:\.\d+)?)\s?C\b(?![a-z])")    # 745C, 745 C; not 1C or 4C


def _encodings(text: str) -> str:
    out = _spelled_to_digits(text)
    out = _SCI.sub(lambda m: f"{float(m[1]) * 10 ** int(m[2]):g}", out)
    out = _BARE_C.sub(lambda m: f"{m[1]} °C", out)
    return out


# --- relative statements ------------------------------------------------------------------------
_UNITISH = r"(?:[A-Za-z°℃%/·⁻¹\-\^ ]{0,12}?)"
_UP = r"above|over|higher than|more than|greater than|beyond|past"
_DOWN = r"below|under|lower than|less than|short of|shy of"
_DIFF = re.compile(
    rf"(?P<d>{_NUM})\s*(?:points?|pts?|%|{_UNITISH})\s+(?:(?P<up>{_UP})|(?P<down>{_DOWN}))\s+"
    rf"(?:the\s+|a\s+|an\s+)?(?:[a-z]+\s+){{0,2}}?(?P<base>{_NUM})\s*(?P<u>[^\s,;]{{0,8}})",
    re.I,
)
_MULT_WORDS = {"twice": 2.0, "double": 2.0, "doubled": 2.0, "triple": 3.0, "tripled": 3.0,
               "half": 0.5, "half of": 0.5}
_MULT = re.compile(
    rf"\b(?:(?P<w>twice|double|doubled|triple|tripled|half of|half)|(?P<k>{_NUM})\s*(?:x|×|times))"
    rf"\s+(?:the\s+|a\s+)?(?P<base>{_NUM})\s*(?P<u>[^\s,;]{{0,8}})",
    re.I,
)


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def _relative(text: str) -> list[str]:
    phrases = []
    for m in _DIFF.finditer(text):
        d, base = _f(m["d"]), _f(m["base"])
        v = base + d if m["up"] else base - d
        phrases.append(f"{_fmt(v)} {m['u']}".strip())
    for m in _MULT.finditer(text):
        k = _MULT_WORDS[m["w"].lower()] if m["w"] else _f(m["k"])
        phrases.append(f"{_fmt(k * _f(m['base']))} {m['u']}".strip())
    return phrases


# --- derived quantities -------------------------------------------------------------------------
_ENERGY = re.compile(rf"(?<![\w.])(?P<v>{_NUM})\s*(?P<u>kWh|Wh)(?![/\w])", re.I)
_CHARGE = re.compile(rf"(?<![\w.])(?P<v>{_NUM})\s*(?P<u>mAh|Ah)(?![/\w])", re.I)
_MASS = re.compile(rf"(?<![\w.])(?P<v>{_NUM})\s*(?P<u>kg|kilograms?|g|grams?)(?![/\w])", re.I)
_COMPLEMENT = re.compile(
    rf"\b(?:scrap|defect|reject(?:ion)?|failure|loss|fallout)\s+(?:rate|ratio)?\s*"
    rf"(?:of\s+|is\s+|at\s+|=\s*)?(?P<v>{_NUM})\s*(?:%|percent|pct)",
    re.I,
)
_DAYS = re.compile(r"\b(?:(?P<half>half[\s-](?:a[\s-])?day)|(?P<n>\d+(?:\.\d+)?)[\s-]days?)\b", re.I)


def _grams(m: re.Match) -> float:
    v = _f(m["v"])
    return v * 1000 if m["u"].lower().startswith("k") else v


def _derived(text: str) -> list[str]:
    phrases = []
    masses = [_grams(m) for m in _MASS.finditer(text)]
    if masses and masses[0] > 0:
        g = masses[0]
        for m in _ENERGY.finditer(text):
            wh = _f(m["v"]) * (1000 if m["u"].lower() == "kwh" else 1)
            phrases.append(f"{_fmt(wh / g * 1000)} Wh/kg")
        for m in _CHARGE.finditer(text):
            mah = _f(m["v"]) * (1000 if m["u"].lower() == "ah" else 1)
            phrases.append(f"{_fmt(mah / g)} mAh/g")
    for m in _COMPLEMENT.finditer(text):
        phrases.append(f"yield {_fmt(100 - _f(m['v']))} %")
    for m in _DAYS.finditer(text):
        hours = 12.0 if m["half"] else _f(m["n"]) * 24
        phrases.append(f"{_fmt(hours)} h")
    return phrases


# --- entry point --------------------------------------------------------------------------------
def variants(text: str) -> list[tuple[str, Kind]]:
    """Plain-number rewrites of ``text``, each with the leak type it would be.

    The first entry (if any) is the whole query with its encodings undone, so aliases and
    context words still sit next to the numbers; the rest are short synthesized phrases that
    carry the original query as context.
    """
    out: list[tuple[str, Kind]] = []
    enc = _encodings(text)
    if enc != text:
        out.append((enc, "encoding"))
    base = enc
    for p in _relative(base):
        out.append((f"{text} ; {p}", "relative"))
    for p in _derived(base):
        out.append((f"{text} ; {p}", "derived"))
    return out
