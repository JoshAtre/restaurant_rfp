from pydantic import BaseModel
from datetime import datetime, date


# --- Request Models ---

class MenuCreate(BaseModel):
    name: str
    source_url: str | None = None
    raw_text: str


class PipelineRunRequest(BaseModel):
    menu_id: int
    send_emails: bool = False


# --- Response Models ---

class IngredientOut(BaseModel):
    id: int
    name: str
    category: str | None
    quantity: float | None = None
    unit: str | None = None
    notes: str | None = None

    class Config:
        from_attributes = True


class RecipeOut(BaseModel):
    id: int
    name: str
    description: str | None
    category: str | None
    ingredients: list[IngredientOut] = []

    class Config:
        from_attributes = True


class PriceOut(BaseModel):
    ingredient_name: str
    price: float | None
    unit: str | None
    source: str
    report_date: date | None

    class Config:
        from_attributes = True


class DistributorOut(BaseModel):
    id: int
    name: str
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    state: str | None
    rating: float | None
    ingredient_count: int = 0

    class Config:
        from_attributes = True


class RFPEmailOut(BaseModel):
    id: int
    distributor_name: str
    distributor_email: str | None
    subject: str
    body: str
    status: str
    quote_deadline: date | None
    sent_at: datetime | None

    class Config:
        from_attributes = True


class PipelineStatusOut(BaseModel):
    id: int
    status: str
    current_step: int
    step_1_status: str
    step_2_status: str
    step_3_status: str
    step_4_status: str
    step_5_status: str
    error_log: str | None
    started_at: datetime | None
    completed_at: datetime | None

    class Config:
        from_attributes = True
