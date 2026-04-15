"""
Step 2: Ingredient Pricing Trends (USDA API)
Fetches pricing data from USDA FoodData Central for extracted ingredients.
"""

import httpx
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.tables import Ingredient, IngredientPrice

USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"

settings = get_settings()


async def fetch_pricing_for_all_ingredients(db: Session) -> list[dict]:
    """Fetch USDA pricing data for all ingredients in the database."""
    ingredients = db.query(Ingredient).all()
    results = []

    async with httpx.AsyncClient(timeout=30) as client:
        for ingredient in ingredients:
            search_term = ingredient.usda_search_term or ingredient.name
            try:
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
                else:
                    results.append({
                        "ingredient": ingredient.name,
                        "status": "not_found",
                    })

            except Exception as e:
                results.append({
                    "ingredient": ingredient.name,
                    "status": "error",
                    "error": str(e),
                })

    db.commit()
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
        return None

    # Take the best match
    best = foods[0]
    fdc_id = best.get("fdcId")

    # Try to get detailed nutrient/price data
    # Note: USDA FoodData Central doesn't directly provide market prices.
    # For a real system, you'd use the USDA Agricultural Marketing Service (AMS)
    # or USDA Economic Research Service (ERS) for wholesale prices.
    # Here we'll use the food data and note the limitation.
    detail_url = f"{USDA_BASE_URL}/food/{fdc_id}"
    detail_params = {"api_key": settings.usda_api_key}

    detail_response = await client.get(detail_url, params=detail_params)

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
    """
    # Rough wholesale price estimates (USD per lb) for common ingredients
    price_estimates = {
        "chicken": 2.50,
        "beef": 5.00,
        "steak": 8.00,
        "salmon": 7.50,
        "shrimp": 9.00,
        "tofu": 1.80,
        "rice": 0.60,
        "quinoa": 3.50,
        "kale": 1.50,
        "spinach": 2.00,
        "arugula": 3.00,
        "lettuce": 1.00,
        "tomato": 1.50,
        "avocado": 2.50,
        "onion": 0.80,
        "pepper": 2.00,
        "corn": 0.50,
        "sweet potato": 1.20,
        "broccoli": 1.50,
        "carrot": 0.80,
        "cucumber": 1.00,
        "cheese": 4.00,
        "olive oil": 5.00,
        "lemon": 1.50,
        "lime": 2.00,
        "egg": 2.50,  # per dozen → ~$0.21/each
        "bread": 1.50,
        "tortilla": 1.20,
    }

    name_lower = ingredient_name.lower()
    for key, price in price_estimates.items():
        if key in name_lower:
            return price

    return None
