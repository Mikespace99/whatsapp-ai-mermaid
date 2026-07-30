"""Repository: accesso al database per Appointment e ConversationContext.

Questo modulo isola tutte le query SQLAlchemy, mantenendo
service.py e flows.py liberi da logica di DB diretta.
"""

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.models import Appointment, AppointmentStatus, ConversationContext, ConversationState

logger = logging.getLogger(__name__)
TZ_ITALY = ZoneInfo("Europe/Rome")


# ════════════════════════════════════════════════════════════════
# Appointment Repository
# ════════════════════════════════════════════════════════════════


class AppointmentRepository:
    """Repository per le operazioni CRUD sugli appuntamenti."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, appointment: Appointment) -> Appointment:
        """Persiste un nuovo appuntamento nel database.

        Args:
            appointment: Istanza Appointment da salvare.

        Returns:
            Appointment salvato con id assegnato.
        """
        self.db.add(appointment)
        self.db.commit()
        self.db.refresh(appointment)
        logger.info("Appointment creato: id=%d, phone=%s***", appointment.id, str(appointment.phone_number)[:4])
        return appointment

    def get_by_id(self, appointment_id: int) -> Optional[Appointment]:
        """Recupera un appuntamento per ID primario.

        Args:
            appointment_id: ID dell'appuntamento.

        Returns:
            Appointment oppure None.
        """
        return self.db.query(Appointment).filter(Appointment.id == appointment_id).first()

    def list_by_phone(
        self,
        phone_number: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        statuses: Optional[list[AppointmentStatus]] = None,
    ) -> list[Appointment]:
        """Lista appuntamenti per numero di telefono con filtri opzionali.

        Args:
            phone_number: Numero E.164.
            from_date: Data/ora inizio filtro (inclusa).
            to_date: Data/ora fine filtro (esclusa).
            statuses: Lista di stati ammessi. Se None, tutti gli stati.

        Returns:
            Lista di Appointment ordinati per data crescente.
        """
        query = self.db.query(Appointment).filter(
            Appointment.phone_number == phone_number
        )
        if from_date:
            query = query.filter(Appointment.appointment_datetime >= from_date)
        if to_date:
            query = query.filter(Appointment.appointment_datetime < to_date)
        if statuses:
            query = query.filter(Appointment.status.in_(statuses))
        return query.order_by(Appointment.appointment_datetime.asc()).all()

    def list_future(self, phone_number: str) -> list[Appointment]:
        """Lista appuntamenti futuri attivi per un numero di telefono.

        Args:
            phone_number: Numero E.164.

        Returns:
            Lista di appuntamenti SCHEDULED o CONFIRMED nel futuro.
        """
        now = datetime.now(TZ_ITALY)
        return self.list_by_phone(
            phone_number,
            from_date=now,
            statuses=[AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED],
        )

    def update_status(self, appointment_id: int, new_status: AppointmentStatus) -> Optional[Appointment]:
        """Aggiorna lo stato di un appuntamento.

        Args:
            appointment_id: ID appuntamento.
            new_status: Nuovo stato.

        Returns:
            Appointment aggiornato oppure None se non trovato.
        """
        appt = self.get_by_id(appointment_id)
        if appt is None:
            logger.warning("update_status: appuntamento id=%d non trovato.", appointment_id)
            return None
        appt.status = new_status
        self.db.commit()
        self.db.refresh(appt)
        logger.info("Appointment id=%d aggiornato a status=%s.", appointment_id, new_status)
        return appt

    def update_datetime(self, appointment_id: int, new_datetime: datetime) -> Optional[Appointment]:
        """Aggiorna data/ora di un appuntamento.

        Args:
            appointment_id: ID appuntamento.
            new_datetime: Nuovo datetime timezone-aware.

        Returns:
            Appointment aggiornato oppure None.
        """
        appt = self.get_by_id(appointment_id)
        if appt is None:
            logger.warning("update_datetime: appuntamento id=%d non trovato.", appointment_id)
            return None
        appt.appointment_datetime = new_datetime
        self.db.commit()
        self.db.refresh(appt)
        logger.info("Appointment id=%d spostato a %s.", appointment_id, new_datetime.isoformat())
        return appt

    def mark_reminder_sent(self, appointment_id: int) -> None:
        """Marca il reminder come inviato per un appuntamento.

        Args:
            appointment_id: ID appuntamento.
        """
        appt = self.get_by_id(appointment_id)
        if appt:
            appt.reminder_sent = True
            self.db.commit()

    def list_pending_reminders(
        self,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[Appointment]:
        """Recupera appuntamenti che necessitano di reminder.

        Args:
            from_dt: Inizio finestra temporale.
            to_dt: Fine finestra temporale.

        Returns:
            Appuntamenti SCHEDULED/CONFIRMED con reminder_sent=False nella finestra.
        """
        return (
            self.db.query(Appointment)
            .filter(
                Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED]),
                Appointment.reminder_sent.is_(False),
                Appointment.appointment_datetime >= from_dt,
                Appointment.appointment_datetime < to_dt,
            )
            .all()
        )


# ════════════════════════════════════════════════════════════════
# ConversationContext Repository
# ════════════════════════════════════════════════════════════════


class ConversationRepository:
    """Repository per la gestione del contesto conversazionale.

    Ogni numero di telefono ha al più un ConversationContext (unico).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(self, phone_number: str) -> ConversationContext:
        """Recupera il contesto esistente o ne crea uno nuovo (IDLE).

        Args:
            phone_number: Numero E.164.

        Returns:
            ConversationContext esistente o appena creato.
        """
        ctx = (
            self.db.query(ConversationContext)
            .filter(ConversationContext.phone_number == phone_number)
            .first()
        )
        if ctx is None:
            ctx = ConversationContext(
                phone_number=phone_number,
                state=ConversationState.IDLE,
                proposal_cycle=0,
                proposed_slots=[],
                rejected_slots=[],
            )
            self.db.add(ctx)
            self.db.commit()
            self.db.refresh(ctx)
            logger.info("Nuovo ConversationContext creato per %s***", phone_number[:4])
        return ctx

    def save(self, ctx: ConversationContext) -> ConversationContext:
        """Persiste le modifiche al contesto.

        Args:
            ctx: Istanza ConversationContext con modifiche applicate.

        Returns:
            ConversationContext aggiornato.
        """
        self.db.add(ctx)
        self.db.commit()
        self.db.refresh(ctx)
        return ctx

    def reset(self, phone_number: str) -> Optional[ConversationContext]:
        """Reimposta il contesto a IDLE azzerando tutti i campi.

        Args:
            phone_number: Numero E.164.

        Returns:
            ConversationContext resettato oppure None.
        """
        ctx = self.get_or_create(phone_number)
        ctx.state = ConversationState.IDLE
        ctx.intent = None
        ctx.preferences = None
        ctx.proposed_slots = []
        ctx.rejected_slots = []
        ctx.selected_slot = None
        ctx.proposal_cycle = 0
        ctx.waiting_reply_until = None
        return self.save(ctx)

    def list_expired_waiting(self, now: datetime) -> list[ConversationContext]:
        """Recupera contesti in WAITING_REPLY con timer scaduto.

        Args:
            now: Timestamp corrente.

        Returns:
            Lista di ConversationContext da marcare come scaduti.
        """
        return (
            self.db.query(ConversationContext)
            .filter(
                ConversationContext.state == ConversationState.WAITING_REPLY,
                ConversationContext.waiting_reply_until < now,
            )
            .all()
        )
