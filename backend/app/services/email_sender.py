"""
Step 4: Send RFP Emails to Distributors
Composes and sends RFP emails requesting price quotes for required ingredients.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.llm import call_llm
from app.core.units import sum_canonical, prettify
from app.models.tables import (
    Distributor, DistributorIngredient, Ingredient,
    RecipeIngredient, RFPEmail,
)

settings = get_settings()
logger = logging.getLogger(__name__)

QUOTE_DEADLINE_DAYS = 7
COVERS_PER_DAY = 150  # must match frontend/src/App.jsx COVERS_PER_DAY


async def compose_and_send_rfp_emails(db: Session, send: bool = False) -> list[dict]:
    """Compose RFP emails for each distributor with their relevant ingredients."""

    distributors = db.query(Distributor).all()
    results = []
    logger.info(
        "Starting RFP email composition: distributor_count=%s send=%s",
        len(distributors),
        send,
    )

    for distributor in distributors:
        # Get ingredients this distributor supplies
        dist_ingredients = (
            db.query(Ingredient)
            .join(DistributorIngredient)
            .filter(DistributorIngredient.distributor_id == distributor.id)
            .all()
        )

        if not dist_ingredients:
            logger.info(
                "Skipping distributor with no linked ingredients: distributor_id=%s name=%r",
                distributor.id,
                distributor.name,
            )
            continue

        logger.info(
            "Composing RFP email for distributor_id=%s name=%r ingredient_count=%s",
            distributor.id,
            distributor.name,
            len(dist_ingredients),
        )

        # Get aggregated quantities from recipes
        ingredient_details = []
        for ing in dist_ingredients:
            total_qty = (
                db.query(RecipeIngredient)
                .filter(RecipeIngredient.ingredient_id == ing.id)
                .all()
            )
            # recipe_ingredients.quantity is PER SINGLE SERVING, and the
            # unit can drift across recipes ("2 oz" vs "1 tbsp" vs "0.5 cup").
            # Canonicalize each row into a single base unit per category
            # (oz / fl oz / each) before summing — rows in non-dominant
            # categories are dropped to avoid silent double-counting.
            agg = sum_canonical([(ri.quantity, ri.unit) for ri in total_qty])
            if agg.dropped_categories or agg.dropped_unknown:
                logger.warning(
                    "Mixed-unit ingredient: ingredient_id=%s name=%r dropped_categories=%s dropped_unknown=%s",
                    ing.id,
                    ing.name,
                    agg.dropped_categories,
                    agg.dropped_unknown,
                )
            if agg.canonical is None:
                logger.info(
                    "Skipping ingredient with no canonicalizable qty: ingredient_id=%s name=%r",
                    ing.id,
                    ing.name,
                )
                continue

            # Scale per-serving → weekly procurement volume.
            agg.canonical.quantity *= COVERS_PER_DAY * 7
            weekly_qty, weekly_unit = prettify(agg.canonical)

            ingredient_details.append({
                "name": ing.name,
                "weekly_quantity": weekly_qty,
                "unit": weekly_unit,
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
        logger.info(
            "Created RFP email draft: rfp_email_id=%s distributor_id=%s subject=%r",
            rfp_email.id,
            distributor.id,
            rfp_email.subject,
        )

        # Optionally send the email
        if send and distributor.email:
            sent = _send_email(
                to=distributor.email,
                subject=email_content["subject"],
                body=email_content["body"],
            )
            rfp_email.status = "sent" if sent else "failed"
            rfp_email.sent_at = datetime.utcnow() if sent else None
            logger.info(
                "RFP email send attempted: rfp_email_id=%s distributor_id=%s status=%s",
                rfp_email.id,
                distributor.id,
                rfp_email.status,
            )
        elif send and not distributor.email:
            logger.warning(
                "Cannot send RFP email without distributor email: rfp_email_id=%s distributor_id=%s",
                rfp_email.id,
                distributor.id,
            )

        results.append({
            "distributor": distributor.name,
            "email": distributor.email,
            "subject": email_content["subject"],
            "ingredient_count": len(ingredient_details),
            "status": rfp_email.status,
            "rfp_email_id": rfp_email.id,
        })

    db.commit()
    logger.info("Completed RFP email composition: emails_created=%s", len(results))
    return results


async def _compose_email(distributor: Distributor, ingredients: list[dict]) -> dict:
    """Use LLM to compose a professional RFP email."""
    logger.info(
        "Calling LLM to compose RFP email for distributor_id=%s ingredient_count=%s",
        distributor.id,
        len(ingredients),
    )

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
        logger.info("Mock sending email because SMTP credentials are not configured: to=%s subject=%r", to, subject)
        return True  # Mock success for demo

    try:
        logger.info("Sending email via SMTP: to=%s subject=%r host=%s", to, subject, settings.smtp_host)
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
        logger.exception("Email send failed: to=%s subject=%r", to, subject)
        return False
