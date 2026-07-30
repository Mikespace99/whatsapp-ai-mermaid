"""Ricerca slot disponibili nel calendario dello studio.

Corrisponde al nodo CALENDAR del diagramma Mermaid.
Interroga il DB per trovare gli appuntamenti esistenti (busy slots)
e genera tutti i candidati nell'arco di tempo di ricerca,
delegando poi la filtrazione al Business Rules Engine (nodo RULES).

Flusso Mermaid:
  CALENDAR → RULES → RESULT → FOUND?
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.appointments.business_rules import (
    SLOT_GRANULARITY_MINUTES,
    apply_rules,
    get_service_duration,
)
from src.config import settings
from src.models import Appointment, AppointmentStatus
from src.schemas import SlotInfo, UserPreferences

logger = logging.getLogger(__name__)

TZ_ITALY = ZoneInfo("Europe/Rome")

# ── Funzione pubblica principale ──────────────────────────────────────────


def search_available_slots(
    db: Session,
    preferences: UserPreferences,
    hint_datetime: Optional[datetime],
    excluded_slots: Optional[list[str]] = None,
    max_results: int = 20,
) -> list[SlotInfo]:
    """Cerca gli slot disponibili nel calendario, rispettando le preferenze.

    Nodo Mermaid: CALENDAR → usa preferenze disponibili

    Args:
        db: Sessione SQLAlchemy attiva.
        preferences: Preferenze utente (servizio, data, ora).
        hint_datetime: Data/ora suggerita dal nodo CHRONO come punto di partenza.
        excluded_slots: Lista di datetime ISO8601 da escludere (slot rifiutati).
        max_results: Numero massimo di slot da restituire.

    Returns:
        Lista di SlotInfo ordinati cronologicamente.
    """
    service_name = preferences.service_name or "default"
    duration_minutes = get_service_duration(service_name)

    # Determina finestra di ricerca
    now = datetime.now(TZ_ITALY)
    search_start = max(
        hint_datetime or now,
        now + timedelta(hours=1),  # Minimo 1 ora nel futuro
    )
    search_end = now + timedelta(days=settings.SEARCH_DAYS)

    logger.info(
        "CALENDAR: ricerca slot dal %s al %s per servizio=%r.",
        search_start.date(),
        search_end.date(),
        service_name,
    )

    # Recupera appuntamenti già prenotati nella finestra
    busy = _get_busy_slots(db, search_start, search_end)

    # Genera tutti i candidati nella finestra
    candidates = _generate_candidates(search_start, search_end, duration_minutes)

    # Converti excluded_slots in datetime per filtraggio
    excluded_dts: set[datetime] = set()
    for iso in (excluded_slots or []):
        try:
            excluded_dts.add(datetime.fromisoformat(iso))
        except ValueError:
            pass

    # Filtra esclusi
    candidates = [c for c in candidates if c not in excluded_dts]

    # Applica business rules (nodo RULES)
    valid = apply_rules(candidates, service_name, busy)

    # Ritorna i primi max_results come SlotInfo
    result = [_to_slot_info(dt) for dt in valid[:max_results]]

    logger.info(
        "CALENDAR: trovati %d slot disponibili (restituiti max %d).",
        len(valid),
        max_results,
    )
    return result


# ── Verifica slot singolo (nodo VERIFY del Mermaid) ──────────────────────


def verify_slot_available(
    db: Session,
    slot_datetime: datetime,
    service_name: str,
) -> bool:
    """Verifica che uno slot specifico sia ancora disponibile.

    Nodo Mermaid: VERIFY → verifica slot ancora libero

    Args:
        db: Sessione SQLAlchemy attiva.
        slot_datetime: Inizio dello slot da verificare (timezone-aware).
        service_name: Servizio (per calcolare la fine slot).

    Returns:
        True se lo slot è ancora libero, False altrimenti.
    """
    duration = get_service_duration(service_name)
    slot_end = slot_datetime + timedelta(minutes=duration)

    busy = _get_busy_slots(db, slot_datetime, slot_end)

    from src.appointments.business_rules import _conflicts_with_busy
    is_free = not _conflicts_with_busy(slot_datetime, slot_end, busy)

    logger.info(
        "VERIFY slot %s: %s.",
        slot_datetime.isoformat(),
        "LIBERO" if is_free else "OCCUPATO",
    )
    return is_free


# ── Helpers privati ────────────────────────────────────────────────────────


def _get_busy_slots(
    db: Session,
    from_dt: datetime,
    to_dt: datetime,
) -> list[tuple[datetime, datetime]]:
    """Recupera gli appuntamenti attivi nella finestra indicata.

    Args:
        db: Sessione SQLAlchemy.
        from_dt: Inizio finestra.
        to_dt: Fine finestra.

    Returns:
        Lista di tuple (inizio, fine) degli appuntamenti occupati.
    """
    active_statuses = [AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED]
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.status.in_(active_statuses),
            Appointment.appointment_datetime >= from_dt,
            Appointment.appointment_datetime < to_dt,
        )
        .all()
    )
    busy: list[tuple[datetime, datetime]] = []
    for appt in appointments:
        duration = get_service_duration(appt.service_name)
        end_dt = appt.appointment_datetime + timedelta(minutes=duration)
        busy.append((appt.appointment_datetime, end_dt))

    return busy


def _generate_candidates(
    from_dt: datetime,
    to_dt: datetime,
    duration_minutes: int,
) -> list[datetime]:
    """Genera tutti i possibili slot candidati nella finestra indicata.

    Ogni slot inizia ogni SLOT_GRANULARITY_MINUTES minuti.

    Args:
        from_dt: Inizio della finestra di ricerca.
        to_dt: Fine della finestra di ricerca.
        duration_minutes: Durata del servizio (per non sforare la finestra).

    Returns:
        Lista di datetime (inizio slot) candidati.
    """
    candidates: list[datetime] = []
    # Arrotonda from_dt al prossimo multiplo di granularità
    minutes_rem = from_dt.minute % SLOT_GRANULARITY_MINUTES
    if minutes_rem != 0:
        from_dt = from_dt + timedelta(minutes=SLOT_GRANULARITY_MINUTES - minutes_rem)
    from_dt = from_dt.replace(second=0, microsecond=0)

    current = from_dt
    while current + timedelta(minutes=duration_minutes) <= to_dt:
        candidates.append(current)
        current += timedelta(minutes=SLOT_GRANULARITY_MINUTES)

    return candidates


def _to_slot_info(dt: datetime) -> SlotInfo:
    """Converte un datetime in SlotInfo con label italiano.

    Args:
        dt: datetime dello slot (timezone-aware).

    Returns:
        SlotInfo con datetime_iso e label leggibile.
    """
    GIORNI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
    label = (
        f"{GIORNI[dt.weekday()]} {dt.day} {MESI[dt.month]} alle {dt.strftime('%H:%M')}"
    )
    return SlotInfo(datetime_iso=dt.isoformat(), label=label)
