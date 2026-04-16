from pydantic_settings import BaseSettings
from functools import lru_cache
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)


class Settings(BaseSettings):
    # API Keys
    openai_api_key: str = ""
    usda_api_key: str = ""
    google_places_api_key: str = ""

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    sender_name: str = "Josh Atre"
    sender_email: str = "josh@sweetgreen.com"

    # Restaurant location
    restaurant_city: str = "New York"
    restaurant_state: str = "NY"
    restaurant_zip: str = "10001"

    # Database
    database_url: str = "sqlite:///./data/rfp.db"

    # LLM
    llm_model: str = "gpt-4o"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
