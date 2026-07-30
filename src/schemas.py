"""Pydantic schemas per validazione input/output dell'API.

Separati dai modelli SQLAlchemy per rispettare la separazione
tra layer DB e layer API (FastAPI best practice).
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, field_validator

from src.models import AppointmentStatus


# ── Appointment schemas ───────────────────────────────────────────────────


class AppointmentCreate(BaseModel):
    """Schema per la creazione di un appuntamento."""

    phone_number: str
    service_name: str
    appointment_datetime: datetime
    customer_name: Optional[str] = None

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Verifica formato E.164 (es. +39123456789)."""
        v = v.strip()
        if not v.startswith("+") or not v[1:].isdigit():
            raise ValueError("Numero telefono deve essere in formato E.164 (es. +39xxxxxxxxxx).")
        if len(v) < 8 or len(v) > 16:
            raise ValueError("Numero telefono E.164 deve avere tra 8 e 16 caratteri.")
        return v

    @field_validator("appointment_datetime")
    @classmethod
    def validate_future_date(cls, v: datetime) -> datetime:
        """Verifica che la data sia nel futuro."""
        if v.tzinfo is None:
            raise ValueError("appointment_datetime deve essere timezone-aware.")
        if v <= datetime.now(v.tzinfo):
            raise ValueError("appointment_datetime deve essere una data futura.")
        return v


class AppointmentUpdate(BaseModel):
    """Schema per la modifica parziale di un appuntamento."""

    new_datetime: Optional[datetime] = None
    service_name: Optional[str] = None
    status: Optional[AppointmentStatus] = None


class AppointmentOut(BaseModel):
    """Schema di output per un appuntamento (risposta API)."""

    id: int
    phone_number: str
    customer_name: Optional[str]
    service_name: str
    appointment_datetime: datetime
    status: AppointmentStatus
    created_at: datetime
    updated_at: datetime
    reminder_sent: bool
    whatsapp_message_id: Optional[str]

    model_config = {"from_attributes": True}


# ── Webhook schemas ───────────────────────────────────────────────────────


class WebhookVerifyParams(BaseModel):
    """Query params per la verifica GET del webhook Meta."""

    hub_mode: str
    hub_challenge: str
    hub_verify_token: str


class WhatsAppMessage(BaseModel):
    """Messaggio singolo estratto dal payload webhook WhatsApp."""

    phone_number: str
    message_text: str
    message_id: Optional[str] = None
    timestamp: Optional[datetime] = None


# ── Flow / Context schemas ────────────────────────────────────────────────


class SlotInfo(BaseModel):
    """Rappresenta un singolo slot disponibile."""

    datetime_iso: str
    """Data e ora dello slot in formato ISO8601."""

    label: str
    """Etichetta human-readable, es. 'Lunedì 4 agosto alle 10:00'."""


class UserPreferences(BaseModel):
    """Preferenze utente estratte dall'LLM (nodo LLM + CTX.preferences)."""

    service_name: Optional[str] = None
    date_preference: Optional[str] = None
    """Data grezza estratta, es. 'domani', 'lunedì prossimo'."""

    time_preference: Optional[str] = None
    """Orario preferito, es. 'mattina', '15:00'."""

    period: Optional[str] = None
    """Periodo del giorno: mattina / pomeriggio / sera."""

    customer_name: Optional[str] = None


class LLMIntentResult(BaseModel):
    """Risultato dell'estrazione intent dall'LLM (nodo LLM del Mermaid)."""

    intent: str
    """BOOKING / INFO / ALTRO."""

    entities: UserPreferences
    """Entità estratte dal messaggio."""


class LLMChoiceResult(BaseModel):
    """Risultato dell'interpretazione risposta utente (nodo INTERPRET)."""

    choice: str
    """ACCETTA / RIFIUTA / NUOVE_PREFERENZE / SCONOSCIUTO."""

    slot_index: Optional[int] = None
    """Indice (0-based) dello slot scelto, se ACCETTA."""

    new_preferences: Optional[UserPreferences] = None
    """Nuove preferenze, se NUOVE_PREFERENZE."""
