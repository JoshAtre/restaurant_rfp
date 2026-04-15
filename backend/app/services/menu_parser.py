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
          "name": "ingredient name (see naming rules below)",
          "quantity": 2.5,
          "unit": "lbs|oz|cups|each|gallons|bunches",
          "notes": "optional prep notes like 'diced' or 'organic'"
        }
      ]
    }
  ]
}

Ingredient naming rules — FOLLOW EXACTLY, the same commodity must
produce byte-identical `name` values across every dish:

1. Lowercase only. No capitals, no quotes, no punctuation except hyphens
   in established compound names (e.g. "sun-dried tomato").
2. SINGULAR form, always. "tomatoes" → "tomato", "cucumbers" → "cucumber",
   "breadcrumbs" → "breadcrumb", "sweet potatoes" → "sweet potato",
   "berries" → "berry", "carrots" → "carrot".
3. Strip all prep/cosmetic modifiers from the name and move them to
   `notes`. Remove: baby, fresh, organic, raw, cooked, warm, hot, cold,
   chopped, diced, shredded, sliced, minced, grated, whole, crispy,
   toasted, blackened, roasted-when-it's-just-prep, and seasoning
   prefixes like "za'atar breadcrumb" → name "breadcrumb", notes
   "za'atar-seasoned".
4. KEEP modifiers that change the actual commodity / SKU: "sun-dried
   tomato", "wild rice", "goat cheese", "smoked salmon", "pickled onion"
   are their own ingredients — do not collapse them to the base.
5. Canonicalize obvious synonyms: "EVOO" → "olive oil", "scallion" →
   "green onion", "cilantro" stays "cilantro".
6. Before returning, scan your own output: if two ingredients across
   different dishes refer to the same commodity, their `name` fields
   MUST be identical strings. Fix any drift before responding.

Other guidelines:
- Estimate quantities PER SINGLE SERVING (one plate), not daily totals.
  A harvest bowl might have 5 oz of chicken and 2 oz of kale, not 12 lbs.
- Use WEIGHT units (oz) for anything solid — proteins, produce, grains,
  dairy, nuts. Do NOT use "each" for items like avocado, lime, lemon,
  tomato, apple, onion — estimate the edible weight in oz instead
  (e.g. half an avocado ≈ 3 oz, a lime wedge ≈ 0.25 oz, half a tomato
  ≈ 2 oz). "each" is only acceptable for truly uncountable-by-weight
  garnishes like a single herb sprig.
- Use fl oz or tbsp for liquids (dressings, oils, sauces).
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
