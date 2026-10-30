"""Find numbers with units in a search query (owner: Hyeseong).

Every match is classified by how it constrains the value:

- ``point``  "272 Wh/kg", "around 270 Wh/kg": states the value itself. Tier 0 checks these.
- ``lower``  "above 250 Wh/kg", "at least 730 °C": the value is at or above the bound.
- ``upper``  "below 280 Wh/kg", "up to 700 mAh/g": the value is at or below the bound.
- ``range``  "260-280 Wh/kg", "between 730 and 760 °C": both ends.

Bounds and ranges are *not* exact leaks; they narrow an interval and belong to the Tier 1
ledger. Keeping them out of Tier 0 stops "above 265 Wh/kg" from being read as "265 Wh/kg".

Values are converted to one canonical unit per family, so "0.272 kWh/kg", "1018 K" and
"720 min" compare directly with secrets stored as Wh/kg, degC and h.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["point", "lower", "upper", "range"]

# --- units -----------------------------------------------------------------------------------
# unit id -> (family, scale, offset); canonical = value * scale + offset.
# Canonical units: energy Wh/kg, fraction ppm, temperature degC, time h, capacity mAh/g.
UNITS: dict[str, tuple[str, float, float]] = {
    "Wh/kg": ("energy", 1.0, 0.0),
    "kWh/kg": ("energy", 1000.0, 0.0),
    "Wh/g": ("energy", 1000.0, 0.0),
    "pct": ("fraction", 1e4, 0.0),
    "wt%": ("fraction", 1e4, 0.0),
    "mol%": ("fraction", 1e4, 0.0),
    "at%": ("fraction", 1e4, 0.0),
    "ppm": ("fraction", 1.0, 0.0),
    "degC": ("temperature", 1.0, 0.0),
    "K": ("temperature", 1.0, -273.15),
    "degF": ("temperature", 5 / 9, -32 * 5 / 9),
    "h": ("time", 1.0, 0.0),
    "min": ("time", 1 / 60, 0.0),
    "mAh/g": ("capacity", 1.0, 0.0),
    "Ah/g": ("capacity", 1000.0, 0.0),
    "Ah/kg": ("capacity", 1.0, 0.0),
    "cycles": ("cycles", 1.0, 0.0),
}

# surface spelling -> unit id. Matched case-insensitively except where noted.
_SURFACE: dict[str, str] = {
    "wh/kg": "Wh/kg", "wh kg-1": "Wh/kg", "wh kg^-1": "Wh/kg", "wh·kg-1": "Wh/kg", "wh kg⁻¹": "Wh/kg",
    "kwh/kg": "kWh/kg", "wh/g": "Wh/g",
    "%": "pct", "pct": "pct", "percent": "pct",
    "wt%": "wt%", "wt.%": "wt%", "wt %": "wt%", "mol%": "mol%", "mol %": "mol%", "at%": "at%",
    "ppm": "ppm",
    "°c": "degC", "℃": "degC", "degc": "degC", "deg c": "degC", "celsius": "degC",
    "°f": "degF", "degf": "degF",
    "kelvin": "K",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "mah/g": "mAh/g", "mah g-1": "mAh/g", "mah g^-1": "mAh/g", "mah·g-1": "mAh/g", "mah g⁻¹": "mAh/g",
    "ah/g": "Ah/g", "ah/kg": "Ah/kg",
    "cycles": "cycles", "cycle": "cycles",
}
# Case-sensitive: an uppercase "K" after a number is kelvin; "k" is not. A bare "C" is left out
# on purpose: "1C" is a charge rate, not a temperature.
_SURFACE_CS: dict[str, str] = {"K": "K"}

# Secret `unit` fields may use any spelling above; normalize them the same way.
_UNIT_ALIASES = {k.lower(): k for k in UNITS} | _SURFACE


def unit_id(unit: str) -> str | None:
    """Map a unit spelling (from a query or a SecretSpec) to its unit id, or None."""
    if unit in _SURFACE_CS:
        return _SURFACE_CS[unit]
    return _UNIT_ALIASES.get(unit.strip().lower())


def to_canonical(value: float, uid: str) -> float:
    _, scale, offset = UNITS[uid]
    return value * scale + offset


def family(uid: str) -> str:
    return UNITS[uid][0]


# --- patterns --------------------------------------------------------------------------------
_NUM = r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d])"
_UNIT_RE = "|".join(
    sorted((re.escape(s) for s in _SURFACE), key=len, reverse=True)
)
# unit must not run into another letter/digit ("12 hours" ok, "12 hydrogen" not "h")
_UNIT = rf"(?:(?i:{_UNIT_RE})|K)(?![A-Za-z0-9])"

_LOWER = (r"above|over|exceeding|exceeds?|more than|greater than|higher than|at least|"
          r"minimum(?: of)?|min\.?|from|>=|≥|>")
_UPPER = (r"below|under|less than|lower than|at most|up to|maximum(?: of)?|max\.?|"
          r"no more than|<=|≤|<")

_RANGE = re.compile(
    rf"(?:between\s+)?(?P<a>{_NUM})\s*(?P<ua>{_UNIT})?\s*(?:-|–|—|to|and)\s*(?P<b>{_NUM})\s*(?P<u>{_UNIT})",
)
_SINGLE = re.compile(rf"(?P<n>{_NUM})\s*(?P<u>{_UNIT})")
_BOUND_BEFORE = re.compile(rf"(?i:(?P<lo>{_LOWER})|(?P<hi>{_UPPER}))\s*$")


@dataclass(frozen=True)
class Quantity:
    """One number-with-unit found in the text."""

    kind: Kind
    unit: str                 # unit id as written (e.g. "kWh/kg")
    lo: float                 # canonical value (point: lo == hi)
    hi: float
    text: str                 # the matched span, for display
    start: int
    end: int

    @property
    def family(self) -> str:
        return family(self.unit)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def extract_quantities(text: str) -> list[Quantity]:
    """All numbers-with-units in ``text``, ranges first, each span used once."""
    out: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for m in _RANGE.finditer(text):
        # "between A and B" needs "between"; a bare "A and B" is two separate mentions
        joined = text[m.start():m.end()]
        if re.search(r"\band\b", joined) and not joined.lower().startswith("between"):
            continue
        uid = unit_id(m["u"])
        if uid is None or not free(m.start(), m.end()):
            continue
        ua = unit_id(m["ua"]) if m["ua"] else uid
        if ua is None or family(ua) != family(uid):
            continue
        a, b = to_canonical(_num(m["a"]), ua), to_canonical(_num(m["b"]), uid)
        out.append(Quantity("range", uid, min(a, b), max(a, b), joined, m.start(), m.end()))
        taken.append((m.start(), m.end()))

    for m in _SINGLE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        uid = unit_id(m["u"])
        if uid is None:
            continue
        v = to_canonical(_num(m["n"]), uid)
        before = text[max(0, m.start() - 24):m.start()]
        b = _BOUND_BEFORE.search(before)
        kind: Kind = "point" if b is None else ("lower" if b["lo"] else "upper")
        out.append(Quantity(kind, uid, v, v, text[m.start():m.end()], m.start(), m.end()))
        taken.append((m.start(), m.end()))

    return sorted(out, key=lambda q: q.start)
