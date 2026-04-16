"""
Step 5: Quote Monitor
Simulates distributor quote replies using the LLM, parses them into the quotes
table, and builds a side-by-side comparison with a cheapest-per-ingredient
recommendation.
"""

import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.core.llm import call_llm
from app.models.tables import (
    Distributor, DistributorIngredient, Ingredient, Quote, RFPEmail,
)

logger = logging.getLogger(__name__)

QUOTE_VALIDITY_DAYS = 30


async def simulate_quote_responses(db: Session) -> list[dict]:
    """Generate a simulated quote reply for every RFP email and persist them."""
    emails = db.query(RFPEmail).all()
    logger.info("Starting quote simulation: rfp_email_count=%s", len(emails))

    results = []
    for email in emails:
        distributor = email.distributor
        if distributor is None:
            continue

        ingredients = (
            db.query(Ingredient)
            .join(DistributorIngredient, DistributorIngredient.ingredient_id == Ingredient.id)
            .filter(DistributorIngredient.distributor_id == distributor.id)
            .all()
        )
        if not ingredients:
            logger.info(
                "Skipping quote sim — no linked ingredients: distributor_id=%s",
                distributor.id,
            )
            continue

        # Wipe any prior simulated quotes for this RFP so reruns are idempotent.
        db.query(Quote).filter(Quote.rfp_email_id == email.id).delete()

        logger.info(
            "Generating simulated quote: rfp_email_id=%s distributor_id=%s ingredient_count=%s",
            email.id,
            distributor.id,
            len(ingredients),
        )

        parsed = await _generate_quote(distributor, ingredients)

        ing_by_name = {i.name.lower(): i for i in ingredients}
        valid_until = datetime.utcnow().date() + timedelta(days=QUOTE_VALIDITY_DAYS)
        delivery_terms = _compose_delivery_terms(parsed)

        saved = 0
        for line in parsed.get("line_items", []) or []:
            name = (line.get("ingredient") or "").strip().lower()
            ingredient = ing_by_name.get(name)
            if ingredient is None:
                # Fuzzy fallback: first ingredient whose name contains the quoted token.
                ingredient = next(
                    (i for n, i in ing_by_name.items() if name and (name in n or n in name)),
                    None,
                )
            if ingredient is None:
                continue
            try:
                price = float(line.get("price"))
            except (TypeError, ValueError):
                continue

            db.add(Quote(
                rfp_email_id=email.id,
                distributor_id=distributor.id,
                ingredient_id=ingredient.id,
                quoted_price=price,
                unit=line.get("unit") or "lb",
                delivery_terms=delivery_terms,
                valid_until=valid_until,
                raw_email_body=parsed.get("email_body"),
            ))
            saved += 1

        email.status = "quoted"
        results.append({
            "rfp_email_id": email.id,
            "distributor_id": distributor.id,
            "distributor_name": distributor.name,
            "line_items": saved,
        })

    db.commit()
    logger.info("Completed quote simulation: quotes_persisted_for=%s", len(results))
    return results


async def _generate_quote(distributor: Distributor, ingredients: list[Ingredient]) -> dict:
    """Ask the LLM to produce a realistic distributor quote reply as JSON."""
    system_prompt = """You are simulating a food distributor replying to a restaurant's
RFP. Produce a realistic quote that varies by distributor personality —
some are cheaper, some premium, some have longer lead times, some have
higher minimums. Prices should be plausible wholesale foodservice prices
in USD per pound (or per each for produce sold by count).

Respond ONLY with valid JSON in this exact shape:
{
  "line_items": [
    {"ingredient": "<ingredient name, matching input>", "price": <number>, "unit": "lb"}
  ],
  "minimum_order": "<e.g. $500 minimum>",
  "delivery_frequency": "<e.g. twice weekly>",
  "lead_time_days": <integer>,
  "payment_terms": "<e.g. Net 30>",
  "email_body": "<short professional reply body as plain text>"
}"""

    ingredient_lines = "\n".join(f"- {i.name}" for i in ingredients)
    location = ", ".join([p for p in [distributor.city, distributor.state] if p]) or "local"

    user_prompt = f"""Generate a quote reply from this distributor:

Distributor: {distributor.name}
Location: {location}
Rating: {distributor.rating if distributor.rating is not None else "unrated"}

Ingredients requested (quote one price per ingredient, per pound unless count-based):
{ingredient_lines}

Make the pricing realistic for foodservice wholesale. Vary the pricing
personality based on the distributor name and rating — a highly rated
specialty supplier should price higher than a budget broadline. Keep
ingredient names in the JSON identical to the inputs so they can be
matched back.
"""

    return await call_llm(system_prompt, user_prompt)


def _compose_delivery_terms(parsed: dict) -> str:
    parts = []
    if parsed.get("minimum_order"):
        parts.append(f"Min order: {parsed['minimum_order']}")
    if parsed.get("delivery_frequency"):
        parts.append(f"Delivery: {parsed['delivery_frequency']}")
    if parsed.get("lead_time_days") is not None:
        parts.append(f"Lead time: {parsed['lead_time_days']} days")
    if parsed.get("payment_terms"):
        parts.append(f"Terms: {parsed['payment_terms']}")
    return " · ".join(parts)


def build_comparison(db: Session) -> dict:
    """Assemble a side-by-side comparison of all simulated quotes."""
    quotes = db.query(Quote).all()

    distributors_map: dict[int, dict] = {}
    ingredients_map: dict[int, dict] = {}
    # matrix[ingredient_id][distributor_id] = {price, unit}
    matrix: dict[int, dict[int, dict]] = {}

    for q in quotes:
        dist = q.rfp_email.distributor if q.rfp_email else None
        if dist is None:
            dist = db.query(Distributor).filter(Distributor.id == q.distributor_id).first()
        if dist is None:
            continue
        ingredient = db.query(Ingredient).filter(Ingredient.id == q.ingredient_id).first()
        if ingredient is None:
            continue

        distributors_map.setdefault(dist.id, {
            "id": dist.id,
            "name": dist.name,
            "delivery_terms": q.delivery_terms,
            "rating": dist.rating,
        })
        ingredients_map.setdefault(ingredient.id, {
            "id": ingredient.id,
            "name": ingredient.name,
        })
        matrix.setdefault(ingredient.id, {})[dist.id] = {
            "price": q.quoted_price,
            "unit": q.unit,
        }

    ingredients = list(ingredients_map.values())
    distributors = list(distributors_map.values())

    # Rows: one per ingredient, with a cheapest-distributor recommendation.
    rows = []
    dist_win_counts: dict[int, int] = {d["id"]: 0 for d in distributors}
    dist_totals: dict[int, float] = {d["id"]: 0.0 for d in distributors}
    dist_counts: dict[int, int] = {d["id"]: 0 for d in distributors}

    for ing in ingredients:
        prices = matrix.get(ing["id"], {})
        cheapest_id = None
        cheapest_price = None
        for dist_id, cell in prices.items():
            if cell["price"] is None:
                continue
            dist_totals[dist_id] += cell["price"]
            dist_counts[dist_id] += 1
            if cheapest_price is None or cell["price"] < cheapest_price:
                cheapest_price = cell["price"]
                cheapest_id = dist_id
        if cheapest_id is not None:
            dist_win_counts[cheapest_id] += 1
        rows.append({
            "ingredient_id": ing["id"],
            "ingredient_name": ing["name"],
            "prices": prices,
            "cheapest_distributor_id": cheapest_id,
            "cheapest_price": cheapest_price,
        })

    # Overall recommendation: distributor that wins the most ingredient lines,
    # tiebroken by lowest average price. Falls back to any distributor with data.
    recommendation = None
    if distributors:
        def score(d):
            wins = dist_win_counts.get(d["id"], 0)
            count = dist_counts.get(d["id"], 0)
            avg = dist_totals[d["id"]] / count if count else float("inf")
            return (-wins, avg)

        best = sorted(distributors, key=score)[0]
        wins = dist_win_counts.get(best["id"], 0)
        count = dist_counts.get(best["id"], 0)
        avg = dist_totals[best["id"]] / count if count else None
        recommendation = {
            "distributor_id": best["id"],
            "distributor_name": best["name"],
            "win_count": wins,
            "line_count": len(ingredients),
            "delivery_terms": best.get("delivery_terms"),
            "rationale": (
                f"{best['name']} offered the lowest price on {wins} of "
                f"{len(ingredients)} ingredients."
            ),
        }

    return {
        "distributors": distributors,
        "ingredients": ingredients,
        "rows": rows,
        "recommendation": recommendation,
    }
