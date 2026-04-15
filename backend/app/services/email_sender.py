"""
Step 4: Send RFP Emails to Distributors
Composes and sends RFP emails requesting price quotes for required ingredients.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.llm import call_llm
from app.models.tables import (
    Distributor, DistributorIngredient, Ingredient,
    RecipeIngredient, RFPEmail,
)

settings = get_settings()

QUOTE_DEADLINE_DAYS = 7


async def compose_and_send_rfp_emails(db: Session, send: bool = False) -> list[dict]:
    """Compose RFP emails for each distributor with their relevant ingredients."""

    distributors = db.query(Distributor).all()
    results = []

    for distributor in distributors:
        # Get ingredients this distributor supplies
        dist_ingredients = (
            db.query(Ingredient)
            .join(DistributorIngredient)
            .filter(DistributorIngredient.distributor_id == distributor.id)
            .all()
        )

        if not dist_ingredients:
            continue

        # Get aggregated quantities from recipes
        ingredient_details = []
        for ing in dist_ingredients:
            total_qty = (
                db.query(RecipeIngredient)
                .filter(RecipeIngredient.ingredient_id == ing.id)
                .all()
            )
            agg_qty = sum(ri.quantity or 0 for ri in total_qty)
            unit = total_qty[0].unit if total_qty else "units"

            ingredient_details.append({
                "name": ing.name,
                "weekly_quantity": round(agg_qty * 7, 1),  # Scale to weekly
                "unit": unit,
            })

        # Compose the email using LLM
        email_content = await _compose_email(distributor, ingredient_details)

        deadline = datetime.utcnow().date() + timedelta(days=QUOTE_DEADLINE_DAYS)

        rfp_email = RFPEmail(
            distributor_id=distributor.id,
            subject=email_content["subject"],
            body=email_content["body"],
            status="draft",
            quote_deadline=deadline,
        )
        db.add(rfp_email)
        db.flush()

        # Optionally send the email
        if send and distributor.email:
            sent = _send_email(
                to=distributor.email,
                subject=email_content["subject"],
                body=email_content["body"],
            )
            rfp_email.status = "sent" if sent else "failed"
            rfp_email.sent_at = datetime.utcnow() if sent else None

        results.append({
            "distributor": distributor.name,
            "email": distributor.email,
            "subject": email_content["subject"],
            "ingredient_count": len(ingredient_details),
            "status": rfp_email.status,
            "rfp_email_id": rfp_email.id,
        })

    db.commit()
    return results


async def _compose_email(distributor: Distributor, ingredients: list[dict]) -> dict:
    """Use LLM to compose a professional RFP email."""

    system_prompt = """You are a restaurant procurement manager writing professional RFP 
emails to food distributors. Write clear, concise, business-appropriate emails.

Respond ONLY with valid JSON:
{
  "subject": "Email subject line",
  "body": "Full email body text"
}"""

    deadline = (datetime.utcnow() + timedelta(days=QUOTE_DEADLINE_DAYS)).strftime("%B %d, %Y")

    ingredient_table = "\n".join(
        f"- {ing['name']}: {ing['weekly_quantity']} {ing['unit']}/week"
        for ing in ingredients
    )

    user_prompt = f"""Compose an RFP email to {distributor.name} requesting price quotes.

Distributor: {distributor.name}
Location: {distributor.city}, {distributor.state}
Quote deadline: {deadline}

Ingredients needed (weekly estimates):
{ingredient_table}

The email should:
1. Introduce the restaurant and explain we're seeking competitive pricing
2. List the ingredients with estimated weekly quantities
3. Request per-unit pricing, minimum order quantities, and delivery terms
4. Set a clear deadline for quote submission
5. Be professional but not overly formal
"""

    return await call_llm(system_prompt, user_prompt)


def _send_email(to: str, subject: str, body: str) -> bool:
    """Send an email via SMTP. Returns True on success."""
    if not settings.smtp_user or not settings.smtp_password:
        print(f"[MOCK SEND] To: {to} | Subject: {subject}")
        return True  # Mock success for demo

    try:
        msg = MIMEMultipart()
        msg["From"] = settings.email_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False
