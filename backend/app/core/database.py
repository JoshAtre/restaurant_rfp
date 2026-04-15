from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # SQLite-specific
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models.tables import (
        Menu, Recipe, Ingredient, RecipeIngredient,
        IngredientPrice, Distributor, DistributorIngredient,
        RFPEmail, Quote, PipelineRun,
    )
    Base.metadata.create_all(bind=engine)
    print("Database tables created.")
