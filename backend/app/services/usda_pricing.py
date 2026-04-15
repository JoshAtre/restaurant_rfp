"""
Step 2: Ingredient Pricing Trends (USDA API)
Fetches pricing data from USDA FoodData Central for extracted ingredients.
"""

import logging
import httpx
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.tables import Ingredient, IngredientPrice

USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"

settings = get_settings()
logger = logging.getLogger(__name__)


async def fetch_pricing_for_all_ingredients(db: Session) -> list[dict]:
    """Fetch USDA pricing data for all ingredients in the database."""
    ingredients = db.query(Ingredient).all()
    results = []
    logger.info("Starting USDA pricing fetch for %s ingredients", len(ingredients))

    async with httpx.AsyncClient(timeout=30) as client:
        for ingredient in ingredients:
            search_term = ingredient.usda_search_term or ingredient.name
            try:
                logger.info(
                    "Searching USDA pricing data for ingredient_id=%s term=%r",
                    ingredient.id,
                    search_term,
                )
                price_data = await _search_usda(client, search_term)
                if price_data:
                    # Store the USDA FDC ID on the ingredient
                    ingredient.usda_fdc_id = price_data.get("fdc_id")

                    # Create price record
                    price = IngredientPrice(
                        ingredient_id=ingredient.id,
                        price=price_data.get("price"),
                        unit=price_data.get("unit", "per lb"),
                        source="USDA FoodData Central",
                        report_date=price_data.get("report_date"),
                    )
                    db.add(price)

                    results.append({
                        "ingredient": ingredient.name,
                        "fdc_id": price_data.get("fdc_id"),
                        "price": price_data.get("price"),
                        "unit": price_data.get("unit"),
                        "status": "found",
                    })
                    logger.info(
                        "USDA match found for ingredient_id=%s fdc_id=%s estimated_price=%s",
                        ingredient.id,
                        price_data.get("fdc_id"),
                        price_data.get("price"),
                    )
                else:
                    results.append({
                        "ingredient": ingredient.name,
                        "status": "not_found",
                    })
                    logger.warning(
                        "No USDA match found for ingredient_id=%s term=%r",
                        ingredient.id,
                        search_term,
                    )

            except Exception as e:
                results.append({
                    "ingredient": ingredient.name,
                    "status": "error",
                    "error": str(e),
                })
                logger.exception(
                    "USDA pricing lookup failed for ingredient_id=%s term=%r",
                    ingredient.id,
                    search_term,
                )

    db.commit()
    found_count = sum(1 for result in results if result["status"] == "found")
    error_count = sum(1 for result in results if result["status"] == "error")
    logger.info(
        "Completed USDA pricing fetch: total=%s found=%s errors=%s",
        len(results),
        found_count,
        error_count,
    )
    return results


async def _search_usda(client: httpx.AsyncClient, query: str) -> dict | None:
    """Search USDA FoodData Central for an ingredient and extract pricing info."""

    # Search for the food item
    search_url = f"{USDA_BASE_URL}/foods/search"
    params = {
        "api_key": settings.usda_api_key,
        "query": query,
        # "dataType": ["Survey (FNDDS)", "SR Legacy"],
        "pageSize": 5,
    }

    response = await client.get(search_url, params=params)
    response.raise_for_status()
    data = response.json()

    foods = data.get("foods", [])
    if not foods:
        logger.warning("USDA search returned no foods for query=%r", query)
        return None

    # Take the best match
    best = foods[0]
    fdc_id = best.get("fdcId")
    logger.info(
        "Selected USDA food match for query=%r fdc_id=%s description=%r",
        query,
        fdc_id,
        best.get("description", ""),
    )

    # Try to get detailed nutrient/price data
    # Note: USDA FoodData Central doesn't directly provide market prices.
    # For a real system, you'd use the USDA Agricultural Marketing Service (AMS)
    # or USDA Economic Research Service (ERS) for wholesale prices.
    # Here we'll use the food data and note the limitation.
    detail_url = f"{USDA_BASE_URL}/food/{fdc_id}"
    detail_params = {"api_key": settings.usda_api_key}

    detail_response = await client.get(detail_url, params=detail_params)
    if detail_response.is_error:
        logger.warning(
            "USDA detail lookup returned status=%s for fdc_id=%s",
            detail_response.status_code,
            fdc_id,
        )

    price_info = {
        "fdc_id": fdc_id,
        "description": best.get("description", ""),
        "price": _estimate_price(query),  # Fallback estimation
        "unit": "per lb",
        "report_date": datetime.utcnow().date(),
    }

    return price_info


def _estimate_price(ingredient_name: str) -> float | None:
    """
    Fallback price estimation based on common commodity prices.
    In production, this would pull from USDA AMS reports or a pricing database.

    ORDER MATTERS: this is substring matching over an ordered dict — the
    first key contained in the ingredient name wins. Put specific
    multi-word entries ABOVE the generic single-word fallback they'd
    otherwise collide with (e.g. "sun-dried tomato" before "tomato",
    "wild rice" before "rice", "sweet potato" before "potato").
    """
    price_estimates = {
        # ─── specific dressings/sauces (must beat shorter keys
        #     like "cashew", "basil", "ginger" that would otherwise
        #     match first via substring) ───────────────────────
        "spicy cashew dressing": 5.50,
        "basil pesto vinaigrette": 6.00,
        "miso sesame ginger dressing": 5.00,
        "green goddess ranch": 4.50,
        "balsamic vinaigrette": 4.50,
        "caesar dressing": 4.00,
        "sweet chili sauce": 3.50,
        "hummus tahini": 3.50,

        # ─── cheese ──────────────────────────────────────────
        "goat cheese": 4.00,
        "blue cheese": 4.50,
        "cream cheese": 4.00,
        "parmesan": 6.50,
        "mozzarella": 4.50,
        "cheddar": 5.00,
        "feta": 5.00,
        "ricotta": 4.00,
        "cheese": 4.00,

        # ─── grains / breads ────────────────────────────────
        "wild rice": 3.50,
        "crispy rice": 3.00,
        "tortilla chip": 1.20,
        "tortilla": 1.20,
        "focaccia": 2.50,
        "crouton": 3.00,
        "breadcrumb": 2.00,
        "pita": 2.00,
        "bread": 1.50,
        "quinoa": 3.50,
        "couscous": 2.50,
        "barley": 1.00,
        "oat": 1.00,
        "rice": 0.60,

        # ─── proteins ───────────────────────────────────────
        "blackened chicken": 2.50,
        "roasted chicken": 2.50,
        "chicken": 2.50,
        "turkey": 3.50,
        "beef": 5.00,
        "steak": 8.00,
        "lamb": 9.00,
        "pork": 4.00,
        "bacon": 6.00,
        "salmon": 7.50,
        "tuna": 6.00,
        "shrimp": 9.00,
        "tofu": 1.80,
        "tempeh": 3.00,
        "falafel": 3.50,
        "egg": 2.50,

        # ─── legumes ────────────────────────────────────────
        "chickpea": 1.20,
        "garbanzo": 1.20,
        "lentil": 1.50,
        "black bean": 1.20,
        "kidney bean": 1.20,
        "bean": 1.20,

        # ─── greens / veg ───────────────────────────────────
        "baby spinach": 2.00,
        "spinach": 2.00,
        "kale": 1.50,
        "arugula": 3.00,
        "romaine": 1.20,
        "lettuce": 1.00,
        "cabbage": 0.60,
        "broccoli": 1.50,
        "cauliflower": 1.40,
        "carrot": 0.80,
        "cucumber": 1.00,
        "sun-dried tomato": 5.00,
        "cherry tomato": 2.50,
        "tomato": 1.50,
        "avocado": 2.50,
        "beet": 1.00,
        "sprout": 3.00,
        "portobello": 3.50,
        "shiitake": 6.00,
        "mushroom": 3.00,
        "sweet potato": 1.20,
        "potato": 0.80,
        "corn": 0.50,
        "pepper": 2.00,
        "jalapeno": 2.50,
        "jalapeño": 2.50,
        "red onion": 0.80,
        "green onion": 2.00,
        "scallion": 2.00,
        "onion": 0.80,
        "garlic": 3.00,
        "ginger": 3.00,

        # ─── fruit ──────────────────────────────────────────
        "apple": 1.20,
        "pear": 1.50,
        "berry": 4.00,
        "strawberry": 3.50,
        "blueberry": 5.00,
        "orange": 1.00,
        "lemon": 1.50,
        "lime": 2.00,
        "mango": 2.00,
        "pineapple": 1.50,

        # ─── herbs ──────────────────────────────────────────
        "cilantro": 4.00,
        "basil": 8.00,
        "mint": 10.00,
        "parsley": 3.00,
        "dill": 6.00,
        "thyme": 10.00,
        "rosemary": 9.00,

        # ─── nuts & seeds ───────────────────────────────────
        "almond": 8.00,
        "cashew": 9.00,
        "walnut": 10.00,
        "pecan": 11.00,
        "pistachio": 12.00,
        "peanut": 3.50,
        "pine nut": 22.00,
        "pumpkin seed": 7.00,
        "sunflower seed": 5.00,
        "sesame seed": 4.00,
        "chia seed": 8.00,
        "flax": 6.00,
        "seed": 5.00,

        # ─── oils & vinegars ────────────────────────────────
        "olive oil": 5.00,
        "sesame oil": 8.00,
        "avocado oil": 9.00,
        "balsamic vinegar": 4.00,
        "rice vinegar": 3.00,
        "vinegar": 3.00,

        # ─── dressings, sauces, spreads ─────────────────────
        "balsamic vinaigrette": 4.50,
        "basil pesto vinaigrette": 6.00,
        "caesar dressing": 4.00,
        "green goddess": 4.50,
        "ranch": 3.50,
        "miso sesame ginger dressing": 5.00,
        "miso": 5.00,
        "spicy cashew dressing": 5.50,
        "vinaigrette": 4.00,
        "dressing": 3.50,
        "pesto": 6.00,
        "hummus tahini": 3.50,
        "hummus": 3.00,
        "tahini": 4.00,
        "sweet chili sauce": 3.50,
        "sriracha": 4.00,
        "soy sauce": 3.00,
        "salsa": 3.00,
        "guacamole": 4.00,
        "sauce": 3.50,

        # ─── dairy ──────────────────────────────────────────
        "greek yogurt": 3.00,
        "yogurt": 2.50,
        "sour cream": 3.00,
        "butter": 4.00,
        "milk": 0.80,

        # ─── spice blends ───────────────────────────────────
        "za'atar": 12.00,
        "zaatar": 12.00,
    }

    name_lower = ingredient_name.lower()
    for key, price in price_estimates.items():
        if key in name_lower:
            return price

    return None
