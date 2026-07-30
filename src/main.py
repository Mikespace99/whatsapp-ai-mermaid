"""Entry point FastAPI dell'applicazione WhatsApp Booking.

Avvio:
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

Endpoint esposti:
    GET  /webhook          → verifica Meta webhook challenge
    POST /webhook          → ricezione messaggi WhatsApp
    GET  /health           → health check
    GET  /appointments     → lista appuntamenti (admin)
    POST /appointments     → crea appuntamento (admin/test)
    GET  /appointments/{id}→ dettaglio appuntamento
    DELETE /appointments/{id} → cancella appuntamento
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from src.config import setup_logging, settings
from src.database import create_tables, get_db
from src.models import AppointmentStatus
from src.schemas import AppointmentCreate, AppointmentOut, AppointmentUpdate
from src.whatsapp.webhook import router as webhook_router

# ── Setup logging (prima di tutto il resto) ───────────────────────────────
setup_logging()
logger = logging.getLogger(__name__)


# ── Lifespan: startup / shutdown ──────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gestisce startup e shutdown dell'applicazione.

    Startup:
        1. Crea le tabelle DB se non esistono
        2. Configura e avvia lo scheduler APScheduler

    Shutdown:
        1. Arresta lo scheduler in modo pulito
    """
    logger.info("=== Avvio applicazione WhatsApp Booking ===")

    # 1. Database
    create_tables()
    logger.info("Database inizializzato.")

    # 2. Scheduler
    from src.scheduler.scheduler import setup_scheduler

    sched = setup_scheduler()
    sched.start()
    logger.info("Scheduler avviato.")

    yield  # L'app è operativa

    # Shutdown
    logger.info("Arresto applicazione...")
    sched.shutdown(wait=False)
    logger.info("Scheduler arrestato.")
    logger.info("=== Applicazione arrestata ===")


# ── App FastAPI ───────────────────────────────────────────────────────────


app = FastAPI(
    title="WhatsApp Appointment Booking",
    description=(
        "Sistema di prenotazione appuntamenti via WhatsApp Business API.\n"
        "Gestisce il flusso conversazionale completo: prenotazione, "
        "modifica, cancellazione e reminder."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (limitato in produzione agli IP del server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "PUT"],
    allow_headers=["*"],
)

# ── Router ────────────────────────────────────────────────────────────────

app.include_router(webhook_router)


# ── Health check ──────────────────────────────────────────────────────────


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    """Root endpoint – richiesto da Render per l'health check."""
    return {"status": "ok", "service": "whatsapp-booking"}


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        Stato dell'applicazione e versione.
    """
    return {"status": "ok", "version": "1.0.0"}


# ── Admin: Appointments CRUD ──────────────────────────────────────────────


@app.get("/appointments", response_model=list[AppointmentOut], tags=["admin"])
def list_appointments_admin(
    phone_number: str = Query(..., description="Numero E.164 del cliente"),
    db: Session = Depends(get_db),
) -> list:
    """Lista tutti gli appuntamenti di un cliente (admin/test).

    Args:
        phone_number: Numero E.164 del cliente.
        db: Sessione DB (iniettata da FastAPI).

    Returns:
        Lista di AppointmentOut.
    """
    from src.appointments.service import AppointmentService

    svc = AppointmentService(db)
    return svc.list_appointments(phone_number)


@app.post(
    "/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
def create_appointment_admin(
    data: AppointmentCreate,
    db: Session = Depends(get_db),
) -> object:
    """Crea un appuntamento direttamente (admin/test).

    Args:
        data: Dati dell'appuntamento.
        db: Sessione DB.

    Returns:
        AppointmentOut con i dati dell'appuntamento creato.
    """
    from src.appointments.service import AppointmentService

    svc = AppointmentService(db)
    try:
        return svc.create_appointment(
            phone_number=data.phone_number,
            service_name=data.service_name,
            appointment_datetime=data.appointment_datetime,
            customer_name=data.customer_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@app.get("/appointments/{appointment_id}", response_model=AppointmentOut, tags=["admin"])
def get_appointment_admin(
    appointment_id: int,
    db: Session = Depends(get_db),
) -> object:
    """Dettaglio appuntamento per ID (admin).

    Args:
        appointment_id: ID dell'appuntamento.
        db: Sessione DB.

    Returns:
        AppointmentOut.

    Raises:
        HTTPException 404: Se non trovato.
    """
    from src.appointments.service import AppointmentService

    svc = AppointmentService(db)
    try:
        return svc.get_appointment(appointment_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@app.delete(
    "/appointments/{appointment_id}",
    response_model=AppointmentOut,
    tags=["admin"],
)
def cancel_appointment_admin(
    appointment_id: int,
    db: Session = Depends(get_db),
) -> object:
    """Cancella un appuntamento per ID (admin).

    Args:
        appointment_id: ID dell'appuntamento.
        db: Sessione DB.

    Returns:
        AppointmentOut con status=CANCELLED.

    Raises:
        HTTPException 404: Se non trovato.
        HTTPException 409: Se già cancellato.
    """
    from src.appointments.service import AppointmentService

    svc = AppointmentService(db)
    try:
        return svc.cancel_appointment(appointment_id)
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_409_CONFLICT if "già cancellato" in detail else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=code, detail=detail)


@app.post("/admin/send-reminders", tags=["admin"])
def force_send_reminders() -> dict[str, str]:
    """Forza l'esecuzione immediata del job reminder (admin).

    Returns:
        Conferma dell'esecuzione.
    """
    from src.scheduler.reminder_job import send_pending_reminders

    send_pending_reminders()
    logger.info("Reminder forzato via endpoint admin.")
    return {"status": "ok", "message": "Job reminder eseguito."}


@app.get("/admin/test-whatsapp", tags=["admin"])
def test_whatsapp_connection(
    to: str = Query(..., description="Numero destinatario E.164, es. +39123456789"),
) -> dict[str, str]:
    """Invia un messaggio WhatsApp di test per verificare la connessione API.

    Args:
        to: Numero destinatario in formato E.164.

    Returns:
        message_id restituito da Meta se OK, oppure dettaglio errore.
    """
    from src.whatsapp.client import whatsapp_client

    try:
        result = whatsapp_client.send_text(
            phone_number=to,
            text="✅ Test connessione WhatsApp OK! Il sistema di prenotazione è attivo.",
        )
        msg_id = result.get("messages", [{}])[0].get("id", "n/a")
        logger.info("Test WhatsApp OK: message_id=%s → %s***", msg_id, to[:4])
        return {"status": "ok", "message_id": msg_id, "to": to}
    except Exception as exc:
        logger.error("Test WhatsApp fallito: %s", exc)
        return {"status": "error", "detail": str(exc)}
