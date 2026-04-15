from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, Date,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class Menu(Base):
    __tablename__ = "menus"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    source_url = Column(String)
    raw_text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    recipes = relationship("Recipe", back_populates="menu")
    pipeline_runs = relationship("PipelineRun", back_populates="menu")


class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    menu_id = Column(Integer, ForeignKey("menus.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    category = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    menu = relationship("Menu", back_populates="recipes")
    recipe_ingredients = relationship("RecipeIngredient", back_populates="recipe")


class Ingredient(Base):
    __tablename__ = "ingredients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    usda_fdc_id = Column(Integer)
    usda_search_term = Column(String)
    category = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    recipe_ingredients = relationship("RecipeIngredient", back_populates="ingredient")
    prices = relationship("IngredientPrice", back_populates="ingredient")
    distributor_ingredients = relationship("DistributorIngredient", back_populates="ingredient")


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    quantity = Column(Float)
    unit = Column(String)
    notes = Column(Text)

    __table_args__ = (UniqueConstraint("recipe_id", "ingredient_id"),)

    recipe = relationship("Recipe", back_populates="recipe_ingredients")
    ingredient = relationship("Ingredient", back_populates="recipe_ingredients")


class IngredientPrice(Base):
    __tablename__ = "ingredient_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    price = Column(Float)
    unit = Column(String)
    source = Column(String, default="USDA")
    report_date = Column(Date)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    ingredient = relationship("Ingredient", back_populates="prices")


class Distributor(Base):
    __tablename__ = "distributors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    address = Column(Text)
    city = Column(String)
    state = Column(String)
    website = Column(String)
    source = Column(String)
    place_id = Column(String)
    rating = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    distributor_ingredients = relationship("DistributorIngredient", back_populates="distributor")
    rfp_emails = relationship("RFPEmail", back_populates="distributor")


class DistributorIngredient(Base):
    __tablename__ = "distributor_ingredients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    distributor_id = Column(Integer, ForeignKey("distributors.id"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)

    __table_args__ = (UniqueConstraint("distributor_id", "ingredient_id"),)

    distributor = relationship("Distributor", back_populates="distributor_ingredients")
    ingredient = relationship("Ingredient", back_populates="distributor_ingredients")


class RFPEmail(Base):
    __tablename__ = "rfp_emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    distributor_id = Column(Integer, ForeignKey("distributors.id"), nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String, default="draft")
    sent_at = Column(DateTime)
    quote_deadline = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)

    distributor = relationship("Distributor", back_populates="rfp_emails")
    quotes = relationship("Quote", back_populates="rfp_email")


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rfp_email_id = Column(Integer, ForeignKey("rfp_emails.id"))
    distributor_id = Column(Integer, ForeignKey("distributors.id"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    quoted_price = Column(Float)
    unit = Column(String)
    delivery_terms = Column(Text)
    valid_until = Column(Date)
    raw_email_body = Column(Text)
    parsed_at = Column(DateTime, default=datetime.utcnow)

    rfp_email = relationship("RFPEmail", back_populates="quotes")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    menu_id = Column(Integer, ForeignKey("menus.id"))
    status = Column(String, default="pending")
    current_step = Column(Integer, default=0)
    step_1_status = Column(String, default="pending")
    step_2_status = Column(String, default="pending")
    step_3_status = Column(String, default="pending")
    step_4_status = Column(String, default="pending")
    step_5_status = Column(String, default="pending")
    error_log = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    menu = relationship("Menu", back_populates="pipeline_runs")
