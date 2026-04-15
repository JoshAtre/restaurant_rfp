"""
Pipeline Orchestrator
Runs Steps 1-5 sequentially, updating status in the database between each step.
"""

from datetime import datetime
from sqlalchemy.orm import Session
from app.models.tables import PipelineRun
from app.services.menu_parser import parse_menu
from app.services.usda_pricing import fetch_pricing_for_all_ingredients
from app.services.distributor_finder import find_distributors
from app.services.email_sender import compose_and_send_rfp_emails


async def run_pipeline(db: Session, menu_id: int, send_emails: bool = False) -> int:
    """Execute the full RFP pipeline for a given menu. Returns the pipeline run ID."""

    # Create a pipeline run record
    run = PipelineRun(
        menu_id=menu_id,
        status="running",
        current_step=1,
        started_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()

    try:
        # Step 1: Parse menu into recipes and ingredients
        run.current_step = 1
        run.step_1_status = "running"
        db.commit()

        recipes = await parse_menu(db, menu_id)

        run.step_1_status = "completed"
        db.commit()

        # Step 2: Fetch USDA pricing data
        run.current_step = 2
        run.step_2_status = "running"
        db.commit()

        pricing = await fetch_pricing_for_all_ingredients(db)

        run.step_2_status = "completed"
        db.commit()

        # Step 3: Find local distributors
        run.current_step = 3
        run.step_3_status = "running"
        db.commit()

        distributors = await find_distributors(db)

        run.step_3_status = "completed"
        db.commit()

        # Step 4: Compose and send RFP emails
        run.current_step = 4
        run.step_4_status = "running"
        db.commit()

        emails = await compose_and_send_rfp_emails(db, send=send_emails)

        run.step_4_status = "completed"
        db.commit()

        # Mark pipeline as complete
        run.status = "completed"
        run.current_step = 5
        run.completed_at = datetime.utcnow()
        db.commit()

    except Exception as e:
        run.status = "failed"
        run.error_log = str(e)
        step_field = f"step_{run.current_step}_status"
        setattr(run, step_field, "failed")
        db.commit()
        raise

    return run.id
