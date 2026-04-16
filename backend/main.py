"""
Pathway RFP Automation System — FastAPI Backend
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.core.database import get_db, init_db
from app.models.tables import (
    Menu, Recipe, Ingredient, RecipeIngredient,
    IngredientPrice, Distributor, DistributorIngredient,
    RFPEmail, PipelineRun,
)
from app.schemas.api import (
    MenuCreate, PipelineRunRequest, RecipeOut, IngredientOut,
    PriceOut, DistributorOut, RFPEmailOut, PipelineStatusOut,
)
from app.pipeline.orchestrator import run_pipeline
from app.services.quote_monitor import simulate_quote_responses, build_comparison


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Pathway RFP Automation",
    description="Automated restaurant procurement pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Menu Endpoints ────────────────────────────────────────

@app.post("/api/menus", status_code=201)
def create_menu(menu: MenuCreate, db: Session = Depends(get_db)):
    db_menu = Menu(
        name=menu.name,
        source_url=menu.source_url,
        raw_text=menu.raw_text,
    )
    db.add(db_menu)
    db.commit()
    db.refresh(db_menu)
    return {"id": db_menu.id, "name": db_menu.name}


@app.get("/api/menus/{menu_id}/recipes")
def get_recipes(menu_id: int, db: Session = Depends(get_db)):
    recipes = db.query(Recipe).filter(Recipe.menu_id == menu_id).all()
    result = []
    for recipe in recipes:
        ingredients = []
        for ri in recipe.recipe_ingredients:
            ingredients.append({
                "id": ri.ingredient.id,
                "name": ri.ingredient.name,
                "category": ri.ingredient.category,
                "quantity": ri.quantity,
                "unit": ri.unit,
                "notes": ri.notes,
            })
        result.append({
            "id": recipe.id,
            "name": recipe.name,
            "description": recipe.description,
            "category": recipe.category,
            "ingredients": ingredients,
        })
    return result


# ─── Ingredient & Pricing Endpoints ───────────────────────

@app.get("/api/ingredients")
def get_ingredients(db: Session = Depends(get_db)):
    ingredients = db.query(Ingredient).all()
    result = []
    for ing in ingredients:
        latest_price = (
            db.query(IngredientPrice)
            .filter(IngredientPrice.ingredient_id == ing.id)
            .order_by(IngredientPrice.fetched_at.desc())
            .first()
        )
        result.append({
            "id": ing.id,
            "name": ing.name,
            "category": ing.category,
            "usda_fdc_id": ing.usda_fdc_id,
            "latest_price": latest_price.price if latest_price else None,
            "price_unit": latest_price.unit if latest_price else None,
        })
    return result


@app.get("/api/pricing/trends")
def get_pricing_trends(db: Session = Depends(get_db)):
    prices = (
        db.query(IngredientPrice)
        .join(Ingredient)
        .order_by(Ingredient.name)
        .all()
    )
    return [
        {
            "ingredient": p.ingredient.name,
            "price": p.price,
            "unit": p.unit,
            "source": p.source,
            "report_date": p.report_date,
        }
        for p in prices
    ]


# ─── Distributor Endpoints ─────────────────────────────────

@app.get("/api/distributors")
def get_distributors(db: Session = Depends(get_db)):
    distributors = db.query(Distributor).all()
    result = []
    for dist in distributors:
        ing_count = (
            db.query(DistributorIngredient)
            .filter(DistributorIngredient.distributor_id == dist.id)
            .count()
        )
        result.append({
            "id": dist.id,
            "name": dist.name,
            "email": dist.email,
            "phone": dist.phone,
            "address": dist.address,
            "city": dist.city,
            "state": dist.state,
            "rating": dist.rating,
            "ingredient_count": ing_count,
        })
    return result


# ─── Email Endpoints ───────────────────────────────────────

@app.get("/api/emails")
def get_emails(db: Session = Depends(get_db)):
    emails = db.query(RFPEmail).all()
    return [
        {
            "id": e.id,
            "distributor_name": e.distributor.name,
            "distributor_email": e.distributor.email,
            "subject": e.subject,
            "body": e.body,
            "status": e.status,
            "quote_deadline": e.quote_deadline,
            "sent_at": e.sent_at,
        }
        for e in emails
    ]


@app.post("/api/emails/{email_id}/send")
def send_email(email_id: int, db: Session = Depends(get_db)):
    email = db.query(RFPEmail).filter(RFPEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    # In a real system, this would call the SMTP sender
    email.status = "sent"
    from datetime import datetime
    email.sent_at = datetime.utcnow()
    db.commit()
    return {"status": "sent", "id": email.id}


# ─── Quote Endpoints ───────────────────────────────────────

@app.post("/api/quotes/simulate")
async def simulate_quotes(db: Session = Depends(get_db)):
    """Generate simulated distributor quote replies via LLM and persist them."""
    results = await simulate_quote_responses(db)
    return {"simulated": results, "comparison": build_comparison(db)}


@app.get("/api/quotes/comparison")
def get_quote_comparison(db: Session = Depends(get_db)):
    return build_comparison(db)


# ─── Pipeline Endpoints ────────────────────────────────────

@app.post("/api/pipeline/run")
async def start_pipeline(
    request: PipelineRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start the full RFP pipeline for a given menu."""
    menu = db.query(Menu).filter(Menu.id == request.menu_id).first()
    if not menu:
        raise HTTPException(status_code=404, detail="Menu not found")

    run_id = await run_pipeline(db, request.menu_id, request.send_emails)
    return {"run_id": run_id, "status": "started"}


@app.get("/api/pipeline/{run_id}/status")
def get_pipeline_status(run_id: int, db: Session = Depends(get_db)):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return {
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "step_1_status": run.step_1_status,
        "step_2_status": run.step_2_status,
        "step_3_status": run.step_3_status,
        "step_4_status": run.step_4_status,
        "step_5_status": run.step_5_status,
        "error_log": run.error_log,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


# ─── Health ─────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    return {"status": "ok"}
