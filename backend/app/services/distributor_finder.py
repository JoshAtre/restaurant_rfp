"""
Step 3: Find Local Distributors
Finds food distributors in the restaurant's area that supply required ingredients.
"""

import httpx
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.llm import call_llm
from app.models.tables import Ingredient, Distributor, DistributorIngredient

settings = get_settings()

# Distributor search categories based on ingredient types
DISTRIBUTOR_CATEGORIES = {
    "produce": "fresh produce wholesale distributor",
    "protein": "meat and seafood wholesale distributor",
    "dairy": "dairy wholesale distributor",
    "grain": "bakery and grain wholesale distributor",
    "other": "food service wholesale distributor",
}


async def find_distributors(db: Session) -> list[dict]:
    """Find local distributors for all ingredient categories."""

    # Get unique ingredient categories
    ingredients = db.query(Ingredient).all()
    categories = set(ing.category or "other" for ing in ingredients)

    results = []

    if settings.google_places_api_key:
        # Use Google Places API for real distributor search
        results = await _search_google_places(db, categories, ingredients)
    else:
        # Use LLM to generate plausible local distributors
        results = await _generate_distributors_llm(db, categories, ingredients)

    return results


async def _search_google_places(
    db: Session, categories: set, ingredients: list
) -> list[dict]:
    """Search Google Places API for food distributors."""
    results = []

    async with httpx.AsyncClient(timeout=30) as client:
        for category in categories:
            search_query = DISTRIBUTOR_CATEGORIES.get(category, "food distributor")
            location = f"{settings.restaurant_city}, {settings.restaurant_state}"

            url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
            params = {
                "query": f"{search_query} near {location}",
                "key": settings.google_places_api_key,
            }

            response = await client.get(url, params=params)
            data = response.json()

            for place in data.get("results", [])[:3]:  # Top 3 per category
                # Check if distributor already exists
                existing = db.query(Distributor).filter(
                    Distributor.place_id == place.get("place_id")
                ).first()

                if not existing:
                    distributor = Distributor(
                        name=place.get("name"),
                        address=place.get("formatted_address"),
                        city=settings.restaurant_city,
                        state=settings.restaurant_state,
                        source="google_places",
                        place_id=place.get("place_id"),
                        rating=place.get("rating"),
                        # Generate a mock email for demo purposes
                        email=_mock_email(place.get("name", "")),
                    )
                    db.add(distributor)
                    db.flush()

                    # Link distributor to relevant ingredients
                    _link_distributor_ingredients(
                        db, distributor, category, ingredients
                    )

                    results.append({
                        "name": distributor.name,
                        "address": distributor.address,
                        "category": category,
                        "source": "google_places",
                    })

    db.commit()
    return results


async def _generate_distributors_llm(
    db: Session, categories: set, ingredients: list
) -> list[dict]:
    """Use LLM to generate realistic local distributor data for demo purposes."""

    system_prompt = """You are a food industry expert. Generate realistic wholesale food 
distributors for a given city. These should be plausible businesses with realistic names 
and details, suitable for a demo.

Respond ONLY with valid JSON:
{
  "distributors": [
    {
      "name": "Business Name",
      "category": "produce|protein|dairy|grain|general",
      "address": "Full street address",
      "phone": "(555) 555-1234",
      "website": "https://example.com",
      "supplies": ["ingredient1", "ingredient2"]
    }
  ]
}"""

    ingredient_names = [ing.name for ing in ingredients]
    category_list = ", ".join(categories)

    user_prompt = f"""Generate 5-8 realistic wholesale food distributors in 
{settings.restaurant_city}, {settings.restaurant_state} that could supply these 
ingredient categories: {category_list}

Ingredients needed: {', '.join(ingredient_names[:30])}

Include a mix of:
- Specialty produce distributors
- Protein/meat suppliers  
- General broadline distributors (like a Sysco or US Foods competitor)
- Dairy suppliers
"""

    result = await call_llm(system_prompt, user_prompt)
    distributors_data = result.get("distributors", [])
    results = []

    for dist_data in distributors_data:
        distributor = Distributor(
            name=dist_data["name"],
            email=_mock_email(dist_data["name"]),
            phone=dist_data.get("phone"),
            address=dist_data.get("address"),
            city=settings.restaurant_city,
            state=settings.restaurant_state,
            website=dist_data.get("website"),
            source="llm_generated",
        )
        db.add(distributor)
        db.flush()

        # Link to supplied ingredients
        category = dist_data.get("category", "general")
        supplied = dist_data.get("supplies", [])

        for ing in ingredients:
            if ing.name in supplied or ing.category == category:
                link = DistributorIngredient(
                    distributor_id=distributor.id,
                    ingredient_id=ing.id,
                )
                db.add(link)

        results.append({
            "name": distributor.name,
            "address": distributor.address,
            "category": category,
            "ingredient_count": len(supplied),
            "source": "llm_generated",
        })

    db.commit()
    return results


def _link_distributor_ingredients(
    db: Session, distributor: Distributor, category: str, ingredients: list
):
    """Link a distributor to ingredients matching its category."""
    for ing in ingredients:
        if ing.category == category or category == "other":
            link = DistributorIngredient(
                distributor_id=distributor.id,
                ingredient_id=ing.id,
            )
            db.add(link)


def _mock_email(business_name: str) -> str:
    """Generate a mock email address for demo purposes."""
    slug = business_name.lower().replace(" ", "").replace("'", "")[:20]
    return f"quotes@{slug}.example.com"
