"""Service layer: logica business per la gestione degli appuntamenti.

Implementa le operazioni definite in Agents.md § Operazioni principali:
  - create_appointment
  - update_appointment
  - cancel_appointment
  - list_appointments
  - get_appointment

Ogni funzione:
  - Valida gli input
  - Gestisce conflitti di orario
  - Logga errori e successi
"""

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.appointments.repository import AppointmentRepository
from src.models import Appointment, AppointmentStatus

logger = logging.getLogger(__name__)
TZ_ITALY = ZoneInfo("Europe/Rome")


class AppointmentService:
    """Logica business per la gestione degli appuntamenti."""

    def __init__(self, db: Session) -> None:
        self._repo = AppointmentRepository(db)

    # ── create ────────────────────────────────────────────────────────────

    def create_appointment(
        self,
        phone_number: str,
        service_name: str,
        appointment_datetime: datetime,
        customer_name: Optional[str] = None,
        whatsapp_message_id: Optional[str] = None,
    ) -> Appointment:
        """Crea un nuovo appuntamento nel database.

        Args:
            phone_number: Numero di telefono del cliente (formato E.164).
            service_name: Nome del servizio prenotato.
            appointment_datetime: Data e ora (timezone-aware).
            customer_name: Nome del cliente, se disponibile.
            whatsapp_message_id: ID del messaggio WhatsApp di conferma.

        Returns:
            Appointment creato.

        Raises:
            ValueError: Se i parametri non sono validi.
        """
        _validate_phone(phone_number)
        _validate_future_datetime(appointment_datetime)

        # Controlla sovrapposizioni con appuntamenti esistenti
        existing = self._repo.list_by_phone(
            phone_number,
            from_date=appointment_datetime,
            statuses=[AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED],
        )
        if existing:
            # Avviso ma non blocca: lo studio può avere più clienti contemporaneamente
            logger.warning(
                "Cliente %s*** ha già un appuntamento in zona %s.",
                phone_number[:4],
                appointment_datetime.isoformat(),
            )

        appt = Appointment(
            phone_number=phone_number,
            service_name=service_name,
            appointment_datetime=appointment_datetime,
            customer_name=customer_name,
            status=AppointmentStatus.SCHEDULED,
            whatsapp_message_id=whatsapp_message_id,
        )
        saved = self._repo.create(appt)
        logger.info(
            "Appuntamento creato: id=%d phone=%s*** service=%r dt=%s",
            saved.id,
            phone_number[:4],
            service_name,
            appointment_datetime.isoformat(),
        )
        return saved

    # ── update ────────────────────────────────────────────────────────────

    def update_appointment(
        self,
        appointment_id: int,
        new_datetime: Optional[datetime] = None,
        new_service_name: Optional[str] = None,
    ) -> Appointment:
        """Modifica data/ora o servizio di un appuntamento esistente.

        Args:
            appointment_id: ID dell'appuntamento da modificare.
            new_datetime: Nuovo datetime (timezone-aware), se da cambiare.
            new_service_name: Nuovo nome servizio, se da cambiare.

        Returns:
            Appointment aggiornato.

        Raises:
            ValueError: Se l'appuntamento non esiste o è già cancellato.
        """
        appt = self._repo.get_by_id(appointment_id)
        if appt is None:
            raise ValueError(f"Appuntamento id={appointment_id} non trovato.")
        if appt.status == AppointmentStatus.CANCELLED:
            raise ValueError(f"Appuntamento id={appointment_id} è già cancellato.")

        if new_datetime is not None:
            _validate_future_datetime(new_datetime)
            self._repo.update_datetime(appointment_id, new_datetime)
            logger.info("Appuntamento id=%d spostato a %s.", appointment_id, new_datetime.isoformat())

        if new_service_name is not None:
            appt = self._repo.get_by_id(appointment_id)
            if appt:
                appt.service_name = new_service_name
                self._repo.save_raw(appt)  # noqa – metodo aggiunto sotto
                logger.info("Appuntamento id=%d servizio aggiornato a %r.", appointment_id, new_service_name)

        return self._repo.get_by_id(appointment_id)  # type: ignore[return-value]

    # ── cancel ────────────────────────────────────────────────────────────

    def cancel_appointment(self, appointment_id: int) -> Appointment:
        """Cancella un appuntamento impostando status=CANCELLED.

        Args:
            appointment_id: ID dell'appuntamento da cancellare.

        Returns:
            Appointment con status aggiornato.

        Raises:
            ValueError: Se non trovato o già cancellato.
        """
        appt = self._repo.get_by_id(appointment_id)
        if appt is None:
            raise ValueError(f"Appuntamento id={appointment_id} non trovato.")
        if appt.status == AppointmentStatus.CANCELLED:
            raise ValueError(f"Appuntamento id={appointment_id} è già cancellato.")

        updated = self._repo.update_status(appointment_id, AppointmentStatus.CANCELLED)
        logger.info("Appuntamento id=%d cancellato.", appointment_id)
        return updated  # type: ignore[return-value]

    # ── list ──────────────────────────────────────────────────────────────

    def list_appointments(
        self,
        phone_number: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list[Appointment]:
        """Lista appuntamenti per cliente e periodo.

        Args:
            phone_number: Numero E.164 del cliente.
            from_date: Inizio periodo (inclusa).
            to_date: Fine periodo (esclusa).

        Returns:
            Lista di Appointment ordinati per data.
        """
        _validate_phone(phone_number)
        return self._repo.list_by_phone(phone_number, from_date=from_date, to_date=to_date)

    # ── get ───────────────────────────────────────────────────────────────

    def get_appointment(self, appointment_id: int) -> Appointment:
        """Recupera il dettaglio di un singolo appuntamento.

        Args:
            appointment_id: ID dell'appuntamento.

        Returns:
            Appointment trovato.

        Raises:
            ValueError: Se non trovato.
        """
        appt = self._repo.get_by_id(appointment_id)
        if appt is None:
            raise ValueError(f"Appuntamento id={appointment_id} non trovato.")
        return appt

    # ── list future (per flusso cancellazione) ─────────────────────────

    def list_future_appointments(self, phone_number: str) -> list[Appointment]:
        """Lista appuntamenti futuri attivi per un cliente.

        Usato nel flusso di cancellazione per mostrare cosa può cancellare.

        Args:
            phone_number: Numero E.164.

        Returns:
            Lista di appuntamenti SCHEDULED/CONFIRMED nel futuro.
        """
        return self._repo.list_future(phone_number)


# ── Validatori privati ─────────────────────────────────────────────────────


def _validate_phone(phone_number: str) -> None:
    """Verifica formato E.164.

    Raises:
        ValueError: Se il formato non è valido.
    """
    if not phone_number.startswith("+") or not phone_number[1:].isdigit():
        raise ValueError(f"Numero telefono non valido: {phone_number!r}. Usa formato E.164.")


def _validate_future_datetime(dt: datetime) -> None:
    """Verifica che il datetime sia nel futuro e timezone-aware.

    Raises:
        ValueError: Se la data è nel passato o priva di timezone.
    """
    if dt.tzinfo is None:
        raise ValueError("appointment_datetime deve essere timezone-aware.")
    now = datetime.now(dt.tzinfo)
    if dt <= now:
        raise ValueError(f"La data {dt.isoformat()} deve essere nel futuro.")
