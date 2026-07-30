"""Modelli SQLAlchemy dell'applicazione.

Tabelle:
  - appointments        → appuntamenti (da Agents.md)
  - conversation_contexts → contesto conversazione (nodo CTX del Mermaid)
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from src.database import Base


# ── Enum: stato appuntamento ──────────────────────────────────────────────


class AppointmentStatus(str, enum.Enum):
    """Ciclo di vita di un appuntamento."""

    SCHEDULED = "SCHEDULED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"


# ── Enum: stato conversazione (nodo STATE del Mermaid CTX) ───────────────


class ConversationState(str, enum.Enum):
    """Stati della state machine conversazionale (flows.py).

    Mappa i nodi chiave del diagramma Mermaid:
      IDLE                          → nessuna conversazione attiva
      WAITING_REPLY                 → nodo WAIT (timer attivo, attesa risposta)
      APPOINTMENT_CREATED           → nodo CONFIRM completato
      WAITING_FOR_CANCEL_CONFIRMATION → flusso cancellazione
    """

    IDLE = "IDLE"
    WAITING_REPLY = "WAITING_REPLY"
    APPOINTMENT_CREATED = "APPOINTMENT_CREATED"
    WAITING_FOR_CANCEL_CONFIRMATION = "WAITING_FOR_CANCEL_CONFIRMATION"


# ── Enum: tipo di intent (nodo LLM + INTENT del Mermaid) ─────────────────


class IntentType(str, enum.Enum):
    """Intent estratti dall'LLM."""

    BOOKING = "BOOKING"
    INFO = "INFO"
    ALTRO = "ALTRO"


# ── Modello: Appointment ──────────────────────────────────────────────────


class Appointment(Base):
    """Rappresenta un appuntamento prenotato.

    Attributi obbligatori definiti in Agents.md § Modello dati minimo.
    """

    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String(20), nullable=False, index=True)
    customer_name = Column(String(100), nullable=True)
    service_name = Column(String(150), nullable=False)
    appointment_datetime = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        SAEnum(AppointmentStatus),
        default=AppointmentStatus.SCHEDULED,
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    reminder_sent = Column(Boolean, default=False, nullable=False)
    whatsapp_message_id = Column(String(100), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Appointment id={self.id} "
            f"phone={self.phone_number[:4]}*** "
            f"service={self.service_name!r} "
            f"dt={self.appointment_datetime} "
            f"status={self.status}>"
        )


# ── Modello: ConversationContext (nodo CTX del Mermaid) ──────────────────


class ConversationContext(Base):
    """Contesto persistito della conversazione WhatsApp per numero di telefono.

    Mappa esattamente i campi del nodo CTX nel diagramma Mermaid:
      intent, state, preferences, proposed_slots,
      rejected_slots, selected_slot, proposal_cycle

    Campi aggiuntivi per la gestione del timer (nodo WAIT):
      last_message_at, waiting_reply_until
    """

    __tablename__ = "conversation_contexts"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String(20), nullable=False, unique=True, index=True)

    # ── Campi Mermaid CTX ────────────────────────────────────────────────
    intent = Column(String(20), nullable=True)
    """Ultimo intent riconosciuto: BOOKING / INFO / ALTRO."""

    state = Column(String(50), default=ConversationState.IDLE, nullable=False)
    """Stato corrente della state machine."""

    preferences = Column(JSON, nullable=True)
    """Preferenze estratte: {service_name, date_pref, time_pref, period}."""

    proposed_slots = Column(JSON, nullable=True)
    """Lista di datetime ISO8601 degli slot proposti all'utente (nodo OFFER)."""

    rejected_slots = Column(JSON, nullable=True)
    """Lista di datetime ISO8601 degli slot rifiutati (nodo REJECT)."""

    selected_slot = Column(String(50), nullable=True)
    """Slot accettato dall'utente (nodo ACCEPT), datetime ISO8601."""

    proposal_cycle = Column(Integer, default=0, nullable=False)
    """Contatore cicli proposta (nodo CYCLE). Reset a 0 ad ogni nuova sessione."""

    # ── Gestione timer ────────────────────────────────────────────────────
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    """Timestamp dell'ultimo messaggio ricevuto (nodo TIME del Mermaid)."""

    waiting_reply_until = Column(DateTime(timezone=True), nullable=True)
    """Scadenza timer risposta (nodo WAIT). None = nessun timer attivo."""

    # ── Metadati ─────────────────────────────────────────────────────────
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<ConversationContext "
            f"phone={self.phone_number[:4]}*** "
            f"state={self.state} "
            f"intent={self.intent} "
            f"cycle={self.proposal_cycle}>"
        )
