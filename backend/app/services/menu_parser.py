"""
Step 1: Menu → Recipes & Ingredients
Parses a restaurant menu into structured recipes with ingredients and quantities.
"""

import logging
from sqlalchemy.orm import Session
from app.core.llm import call_llm
from app.models.tables import Menu, Recipe, Ingredient, RecipeIngredient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional chef and restaurant consultant. Given a restaurant menu,
break down each dish into a detailed recipe with commercial-scale ingredients and quantities.

Respond ONLY with valid JSON (no markdown, no commentary) in this exact format:
{
  "dishes": [
    {
      "name": "Dish Name",
      "description": "Brief description of the dish",
      "category": "salad|bowl|side|drink|dessert|appetizer|entree",
      "ingredients": [
        {
          "name": "ingredient name (lowercase, singular, generic)",
          "quantity": 2.5,
          "unit": "lbs|oz|cups|each|gallons|bunches",
          "notes": "optional prep notes like 'diced' or 'organic'"
        }
      ]
    }
  ]
}

Guidelines:
- Estimate quantities for a restaurant serving ~50 covers per day
- Use commercial units (lbs, gallons, cases) not home-cooking units
- Normalize ingredient names: "baby arugula" → "arugula", "EVOO" → "olive oil"
- Include ALL components: dressings, garnishes, toppings, bases
- Category should reflect the menu section
"""


async def parse_menu(db: Session, menu_id: int) -> list[dict]:
    """Parse a menu's raw text into structured recipes and persist to DB."""
    logger.info("Starting menu parsing for menu_id=%s", menu_id)

    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    if not menu:
        logger.warning("Menu parsing failed: menu_id=%s not found", menu_id)
        raise ValueError(f"Menu {menu_id} not found")

    logger.info(
        "Loaded menu for parsing: menu_id=%s name=%r raw_text_chars=%s",
        menu.id,
        menu.name,
        len(menu.raw_text or ""),
    )

    user_prompt = f"""Parse this restaurant menu into structured recipes:

Restaurant: {menu.name}
Menu:
{menu.raw_text}
"""

    logger.info("Calling LLM to extract dishes for menu_id=%s", menu_id)
    result = await call_llm(SYSTEM_PROMPT, user_prompt)
    dishes = result.get("dishes", [])
    logger.info("LLM returned %s dishes for menu_id=%s", len(dishes), menu_id)

    created_recipes = []
    created_ingredient_count = 0

    for dish in dishes:
        # Create or find the recipe
        recipe = Recipe(
            menu_id=menu_id,
            name=dish["name"],
            description=dish.get("description", ""),
            category=dish.get("category", ""),
        )
        db.add(recipe)
        db.flush()  # Get the ID

        for ing_data in dish.get("ingredients", []):
            ing_name = ing_data["name"].lower().strip()

            # Deduplicate ingredients by name
            ingredient = db.query(Ingredient).filter(
                Ingredient.name == ing_name
            ).first()

            if not ingredient:
                ingredient = Ingredient(
                    name=ing_name,
                    usda_search_term=ing_name,  # Will be refined in Step 2
                    category=_guess_category(ing_name),
                )
                db.add(ingredient)
                db.flush()
                created_ingredient_count += 1

            # Create the recipe-ingredient link
            recipe_ingredient = RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=ingredient.id,
                quantity=ing_data.get("quantity"),
                unit=ing_data.get("unit"),
                notes=ing_data.get("notes"),
            )
            db.add(recipe_ingredient)

        created_recipes.append({
            "id": recipe.id,
            "name": recipe.name,
            "ingredient_count": len(dish.get("ingredients", [])),
        })

    db.commit()
    logger.info(
        "Completed menu parsing for menu_id=%s: recipes_created=%s new_ingredients=%s",
        menu_id,
        len(created_recipes),
        created_ingredient_count,
    )
    return created_recipes


def _guess_category(name: str) -> str:
    """Simple heuristic to categorize an ingredient."""
    produce = ["lettuce", "tomato", "onion", "pepper", "spinach", "kale",
               "arugula", "carrot", "cucumber", "avocado", "corn", "broccoli",
               "cabbage", "cilantro", "basil", "mint", "lemon", "lime"]
    protein = ["chicken", "beef", "steak", "salmon", "shrimp", "tofu",
               "turkey", "pork", "egg", "tuna", "lamb"]
    dairy = ["cheese", "cream", "yogurt", "butter", "milk", "mozzarella",
             "parmesan", "feta", "goat cheese"]
    grain = ["rice", "bread", "tortilla", "pasta", "quinoa", "noodle",
             "flour", "oat", "pita", "crouton"]

    for word in produce:
        if word in name:
            return "produce"
    for word in protein:
        if word in name:
            return "protein"
    for word in dairy:
        if word in name:
            return "dairy"
    for word in grain:
        if word in name:
            return "grain"
    return "other"
