"""
Unit normalization for recipe ingredient quantities.

Recipes are parsed into `recipe_ingredients` rows where (quantity, unit) can
drift across dishes — one recipe may say "2 oz", another "1 tbsp", another
"0.5 cup". When we aggregate for RFP emails or pricing totals we need a
single canonical unit per category so the sums are meaningful.

Categories:
  - weight → canonical "oz"
  - volume → canonical "fl oz"
  - count  → canonical "each"

Anything we can't classify is silently dropped from aggregation (rather
than polluting the sum with a bogus value).
"""

from __future__ import annotations

from dataclasses import dataclass

# qty × factor = qty in canonical unit for that category
_WEIGHT_TO_OZ = {
    "oz": 1.0, "ounce": 1.0, "ounces": 1.0,
    "lb": 16.0, "lbs": 16.0, "pound": 16.0, "pounds": 16.0,
    "g": 0.035274, "gram": 0.035274, "grams": 0.035274,
    "kg": 35.274, "kilogram": 35.274, "kilograms": 35.274,
}

_VOLUME_TO_FL_OZ = {
    "fl oz": 1.0, "floz": 1.0, "fluid ounce": 1.0, "fluid ounces": 1.0,
    "tsp": 1.0 / 6.0, "teaspoon": 1.0 / 6.0, "teaspoons": 1.0 / 6.0,
    "tbsp": 0.5, "tablespoon": 0.5, "tablespoons": 0.5,
    "cup": 8.0, "cups": 8.0,
    "pint": 16.0, "pints": 16.0,
    "quart": 32.0, "quarts": 32.0,
    "gallon": 128.0, "gallons": 128.0, "gal": 128.0,
    "ml": 0.033814, "milliliter": 0.033814, "milliliters": 0.033814,
    "l": 33.814, "liter": 33.814, "liters": 33.814,
}

_COUNT_UNITS = {
    "each", "ea", "unit", "units", "piece", "pieces",
    "bunch", "bunches", "head", "heads", "clove", "cloves",
    "sprig", "sprigs", "slice", "slices",
}

WEIGHT = "weight"
VOLUME = "volume"
COUNT = "count"
UNKNOWN = "unknown"


@dataclass
class Canonical:
    category: str   # "weight" | "volume" | "count" | "unknown"
    quantity: float  # in the canonical unit for the category
    unit: str        # "oz" | "fl oz" | "each" | ""


def _normalize_token(u: str | None) -> str:
    return (u or "").strip().lower().rstrip(".")


def to_canonical(quantity: float | None, unit: str | None) -> Canonical | None:
    """Convert (quantity, unit) to its canonical form, or None if unknown."""
    if quantity is None:
        return None
    try:
        q = float(quantity)
    except (TypeError, ValueError):
        return None

    u = _normalize_token(unit)
    if not u:
        return None

    if u in _WEIGHT_TO_OZ:
        return Canonical(WEIGHT, q * _WEIGHT_TO_OZ[u], "oz")
    if u in _VOLUME_TO_FL_OZ:
        return Canonical(VOLUME, q * _VOLUME_TO_FL_OZ[u], "fl oz")
    if u in _COUNT_UNITS:
        return Canonical(COUNT, q, "each")
    return None


@dataclass
class Aggregation:
    canonical: Canonical | None
    dropped_categories: list[str]  # non-dominant categories that had rows
    dropped_unknown: int           # rows we couldn't classify at all


def sum_canonical(rows: list[tuple[float | None, str | None]]) -> Aggregation:
    """Aggregate a list of (quantity, unit) tuples for one ingredient.

    Picks the dominant category (by row count) and sums only rows in that
    category. Rows in other categories are discarded and returned in
    `dropped_categories` so the caller can log a warning — this is the
    "enforcement" step that prevents mixed-unit double-counting.
    """
    buckets: dict[str, list[Canonical]] = {WEIGHT: [], VOLUME: [], COUNT: []}
    dropped_unknown = 0
    for qty, unit in rows:
        c = to_canonical(qty, unit)
        if c is None:
            dropped_unknown += 1
            continue
        buckets[c.category].append(c)

    chosen = max(buckets, key=lambda k: len(buckets[k]))
    if not buckets[chosen]:
        return Aggregation(None, [], dropped_unknown)

    total = sum(c.quantity for c in buckets[chosen])
    unit = buckets[chosen][0].unit
    dropped = [k for k, v in buckets.items() if k != chosen and v]
    return Aggregation(Canonical(chosen, total, unit), dropped, dropped_unknown)


def prettify(canonical: Canonical) -> tuple[float, str]:
    """Return a human-friendly (quantity, unit) for display in emails.

    Weights ≥ 16 oz → lbs. Volumes ≥ 128 fl oz → gallons. Counts stay
    as-is.
    """
    q, u = canonical.quantity, canonical.unit
    if canonical.category == WEIGHT and q >= 16:
        return round(q / 16.0, 1), "lbs"
    if canonical.category == VOLUME and q >= 128:
        return round(q / 128.0, 1), "gallons"
    return round(q, 1), u
