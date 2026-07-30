"""Configurazione APScheduler per i job periodici.

Job registrati:
  1. send_pending_reminders  → ogni REMINDER_CHECK_INTERVAL_MINUTES minuti
  2. expire_stale_conversations → ogni EXPIRY_CHECK_INTERVAL_SECONDS secondi

Lo scheduler viene avviato insieme all'app FastAPI (lifespan).
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import settings

logger = logging.getLogger(__name__)

# Istanza singleton dello scheduler
scheduler = BackgroundScheduler(timezone="Europe/Rome")


def setup_scheduler() -> BackgroundScheduler:
    """Registra tutti i job e restituisce lo scheduler pronto all'avvio.

    Returns:
        BackgroundScheduler configurato (non ancora avviato).
    """
    # ── Job 1: Reminder appuntamenti ─────────────────────────────────────
    from src.scheduler.reminder_job import send_pending_reminders

    scheduler.add_job(
        func=send_pending_reminders,
        trigger=IntervalTrigger(minutes=settings.REMINDER_CHECK_INTERVAL_MINUTES),
        id="reminder_job",
        name="Invio reminder appuntamenti",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info(
        "Job registrato: reminder ogni %d minuti.",
        settings.REMINDER_CHECK_INTERVAL_MINUTES,
    )

    # ── Job 2: Scadenza conversazioni ─────────────────────────────────────
    from src.appointments.flows import expire_stale_conversations
    from src.database import SessionLocal

    def _run_expire_job() -> None:
        """Wrapper con gestione sessione DB per expire_stale_conversations."""
        db = SessionLocal()
        try:
            expire_stale_conversations(db)
        except Exception as exc:
            logger.error("Errore nel job scadenza conversazioni: %s", exc)
        finally:
            db.close()

    scheduler.add_job(
        func=_run_expire_job,
        trigger=IntervalTrigger(seconds=settings.EXPIRY_CHECK_INTERVAL_SECONDS),
        id="expiry_job",
        name="Scadenza conversazioni WAITING_REPLY",
        replace_existing=True,
        misfire_grace_time=30,
    )
    logger.info(
        "Job registrato: scadenza conversazioni ogni %d secondi.",
        settings.EXPIRY_CHECK_INTERVAL_SECONDS,
    )

    return scheduler
