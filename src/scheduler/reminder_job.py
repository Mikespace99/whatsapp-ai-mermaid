"""Job per l'invio dei reminder appuntamenti.

Cerca periodicamente gli appuntamenti che:
  - hanno status SCHEDULED o CONFIRMED
  - sono tra REMINDER_HOURS_BEFORE e REMINDER_HOURS_BEFORE-1 ore nel futuro
  - non hanno ancora ricevuto il reminder (reminder_sent=False)

Per ogni appuntamento trovato:
  1. Invia il template reminder via WhatsApp
  2. Imposta reminder_sent=True nel DB
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.appointments.repository import AppointmentRepository
from src.config import settings
from src.whatsapp.client import whatsapp_client
from src.whatsapp.templates import build_reminder_template

logger = logging.getLogger(__name__)
TZ_ITALY = ZoneInfo("Europe/Rome")


def send_pending_reminders() -> None:
    """Job principale per l'invio dei reminder.

    Questa funzione viene chiamata periodicamente dallo scheduler
    (ogni REMINDER_CHECK_INTERVAL_MINUTES minuti).

    Workflow:
        1. Calcola la finestra temporale [now+reminder_hours-1h, now+reminder_hours]
        2. Recupera appuntamenti nella finestra con reminder_sent=False
        3. Per ogni appuntamento: invia reminder e aggiorna il flag
    """
    from src.database import SessionLocal

    db = SessionLocal()
    try:
        repo = AppointmentRepository(db)
        now = datetime.now(TZ_ITALY)

        # Finestra: tra X e X-1 ore (es. tra 23h e 24h nel futuro)
        from_dt = now + timedelta(hours=settings.REMINDER_HOURS_BEFORE - 1)
        to_dt = now + timedelta(hours=settings.REMINDER_HOURS_BEFORE)

        pending = repo.list_pending_reminders(from_dt=from_dt, to_dt=to_dt)

        if not pending:
            logger.info("Reminder job: nessun reminder da inviare.")
            return

        logger.info("Reminder job: %d reminder da inviare.", len(pending))

        for appt in pending:
            _send_single_reminder(repo, appt)

    except Exception as exc:
        logger.error("Errore nel reminder job: %s", exc)
    finally:
        db.close()


def _send_single_reminder(repo: AppointmentRepository, appt) -> None:
    """Invia il reminder a un singolo appuntamento e aggiorna il flag.

    Args:
        repo: Repository appuntamenti con sessione DB attiva.
        appt: Istanza Appointment da ricordare.
    """
    try:
        template_data = build_reminder_template(
            customer_name=appt.customer_name or "Cliente",
            service_name=appt.service_name,
            appointment_datetime=appt.appointment_datetime,
        )
        whatsapp_client.send_template_message(
            phone_number=appt.phone_number,
            template_name=template_data["template_name"],
            language_code=template_data["language_code"],
            components=template_data["components"],
        )
        repo.mark_reminder_sent(appt.id)
        logger.info(
            "Reminder inviato: appt_id=%d phone=%s*** dt=%s",
            appt.id,
            str(appt.phone_number)[:4],
            appt.appointment_datetime.isoformat(),
        )
    except Exception as exc:
        logger.error(
            "Errore invio reminder appt_id=%d phone=%s***: %s",
            appt.id,
            str(appt.phone_number)[:4],
            exc,
        )
        # Non rilancia: il job continua con gli altri appuntamenti
