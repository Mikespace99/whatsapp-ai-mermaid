"""Business Rules Engine per la filtrazione e ordinamento degli slot.

Corrisponde al nodo RULES del diagramma Mermaid:
  - Durata servizio (slot_duration)
  - Orari di apertura dello studio
  - Esclusioni (festività, pause)
  - Ordinamento slot risultanti

La configurazione degli orari e dei servizi è definita qui come
costanti modificabili (da spostare su DB o config file in produzione).
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ_ITALY = ZoneInfo("Europe/Rome")

# ── Configurazione studio ─────────────────────────────────────────────────

# Durata (minuti) per ciascun servizio.
# Chiave: nome servizio (lower-case), Valore: durata in minuti.
SERVICE_DURATIONS: dict[str, int] = {
    "visita di controllo": 30,
    "visita generale": 45,
    "consulenza": 60,
    "seduta": 60,
    "igiene dentale": 45,
    "radiografia": 20,
    "default": 30,  # Fallback se servizio non mappato
}

# Orari di apertura: weekday → (ora_inizio, ora_fine)
# 0=lunedì … 5=sabato, 6=domenica (None = chiuso)
OPENING_HOURS: dict[int, Optional[tuple[time, time]]] = {
    0: (time(9, 0), time(18, 0)),   # Lunedì
    1: (time(9, 0), time(18, 0)),   # Martedì
    2: (time(9, 0), time(18, 0)),   # Mercoledì
    3: (time(9, 0), time(18, 0)),   # Giovedì
    4: (time(9, 0), time(17, 0)),   # Venerdì
    5: (time(9, 0), time(13, 0)),   # Sabato (mezza giornata)
    6: None,                         # Domenica: chiuso
}

# Pausa pranzo: (ora_inizio, ora_fine) oppure None se non c'è pausa
LUNCH_BREAK: Optional[tuple[time, time]] = (time(13, 0), time(14, 0))

# Festività italiane fisse (giorno, mese)
ITALIAN_HOLIDAYS: set[tuple[int, int]] = {
    (1, 1),   # Capodanno
    (6, 1),   # Epifania
    (25, 4),  # Liberazione
    (1, 5),   # Festa del Lavoro
    (2, 6),   # Repubblica
    (15, 8),  # Ferragosto
    (1, 11),  # Ognissanti
    (8, 12),  # Immacolata
    (25, 12), # Natale
    (26, 12), # Santo Stefano
}

# Granularità slot in minuti
SLOT_GRANULARITY_MINUTES = 30


# ── Funzione pubblica principale ──────────────────────────────────────────


def apply_rules(
    candidate_slots: list[datetime],
    service_name: str,
    busy_slots: list[tuple[datetime, datetime]],
) -> list[datetime]:
    """Filtra e ordina i candidate_slots secondo le Business Rules.

    Nodo Mermaid: RULES → durata servizio, orari apertura, esclusioni, ordinamento

    Args:
        candidate_slots: Lista di datetime (inizio slot) da valutare.
        service_name: Nome del servizio richiesto (per calcolare la durata).
        busy_slots: Lista di tuple (inizio, fine) degli slot già occupati.

    Returns:
        Lista di datetime validi, ordinati cronologicamente.
    """
    duration = get_service_duration(service_name)
    valid: list[datetime] = []

    for slot_start in candidate_slots:
        slot_end = slot_start + timedelta(minutes=duration)

        if not _is_within_opening_hours(slot_start, slot_end):
            continue
        if _is_holiday(slot_start.date()):
            continue
        if _overlaps_lunch(slot_start, slot_end):
            continue
        if _conflicts_with_busy(slot_start, slot_end, busy_slots):
            continue

        valid.append(slot_start)

    valid.sort()
    logger.info(
        "RULES: %d candidati → %d slot validi per servizio=%r (durata=%dmin).",
        len(candidate_slots),
        len(valid),
        service_name,
        duration,
    )
    return valid


# ── Funzione pubblica: durata servizio ────────────────────────────────────


def get_service_duration(service_name: str) -> int:
    """Restituisce la durata in minuti per il servizio dato.

    Args:
        service_name: Nome del servizio (case-insensitive).

    Returns:
        Durata in minuti (default 30 se non mappato).
    """
    key = (service_name or "").strip().lower()
    for mapped_name, duration in SERVICE_DURATIONS.items():
        if mapped_name in key or key in mapped_name:
            return duration
    return SERVICE_DURATIONS["default"]


# ── Funzione pubblica: check orari di apertura ────────────────────────────


def is_studio_open(dt: datetime) -> bool:
    """Verifica se lo studio è aperto nel datetime dato.

    Args:
        dt: datetime timezone-aware da verificare.

    Returns:
        True se aperto, False altrimenti.
    """
    if _is_holiday(dt.date()):
        return False
    hours = OPENING_HOURS.get(dt.weekday())
    if hours is None:
        return False
    open_time, close_time = hours
    return open_time <= dt.time() < close_time


# ── Helpers privati ────────────────────────────────────────────────────────


def _is_within_opening_hours(slot_start: datetime, slot_end: datetime) -> bool:
    """Verifica che lo slot (start → end) cada interamente negli orari apertura."""
    weekday = slot_start.weekday()
    hours = OPENING_HOURS.get(weekday)
    if hours is None:
        return False
    open_time, close_time = hours
    return (
        slot_start.time() >= open_time
        and slot_end.time() <= close_time
    )


def _is_holiday(d: date) -> bool:
    """Verifica se la data è una festività italiana."""
    return (d.day, d.month) in ITALIAN_HOLIDAYS


def _overlaps_lunch(slot_start: datetime, slot_end: datetime) -> bool:
    """Verifica se lo slot si sovrappone alla pausa pranzo."""
    if LUNCH_BREAK is None:
        return False
    lunch_start = datetime.combine(slot_start.date(), LUNCH_BREAK[0], tzinfo=TZ_ITALY)
    lunch_end = datetime.combine(slot_start.date(), LUNCH_BREAK[1], tzinfo=TZ_ITALY)
    return slot_start < lunch_end and slot_end > lunch_start


def _conflicts_with_busy(
    slot_start: datetime,
    slot_end: datetime,
    busy_slots: list[tuple[datetime, datetime]],
) -> bool:
    """Verifica se lo slot si sovrappone a uno slot già occupato.

    Args:
        slot_start: Inizio slot da verificare.
        slot_end: Fine slot da verificare.
        busy_slots: Lista di tuple (inizio, fine) slot occupati.

    Returns:
        True se c'è sovrapposizione, False altrimenti.
    """
    for busy_start, busy_end in busy_slots:
        if slot_start < busy_end and slot_end > busy_start:
            return True
    return False
