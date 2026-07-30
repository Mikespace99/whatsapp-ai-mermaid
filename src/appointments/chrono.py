"""Normalizzazione date da linguaggio naturale italiano.

Corrisponde al nodo CHRONO del diagramma Mermaid.

Responsabilità:
  - Convertire espressioni come "domani", "lunedì prossimo",
    "tra due giorni", "15 agosto", "3 settembre alle 10" in
    oggetti datetime timezone-aware.
  - Normalizzare preferenze orarie ("mattina", "pomeriggio", "15:00").

Dipendenze:
  - python-dateutil  (parsing date flessibile)
  - datetime / zoneinfo (timezone Italia)
"""

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser

from src.schemas import UserPreferences

logger = logging.getLogger(__name__)

# ── Costanti ──────────────────────────────────────────────────────────────

TZ_ITALY = ZoneInfo("Europe/Rome")

_GIORNI_IT = {
    "lunedì": 0, "lunedi": 0,
    "martedì": 1, "martedi": 1,
    "mercoledì": 2, "mercoledi": 2,
    "giovedì": 3, "giovedi": 3,
    "venerdì": 4, "venerdi": 4,
    "sabato": 5,
    "domenica": 6,
}

_MESI_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

# Orario di default per ciascun periodo del giorno
_PERIOD_DEFAULT_HOUR = {
    "mattina": 9,
    "pomeriggio": 15,
    "sera": 18,
}


# ── Funzione pubblica principale ──────────────────────────────────────────


def normalize_date_preference(preferences: UserPreferences, today: Optional[date] = None) -> Optional[datetime]:
    """Converte le preferenze di data/ora in un datetime timezone-aware.

    Nodo Mermaid: CHRONO → normalizzazione date

    Args:
        preferences: Preferenze utente (date_preference, time_preference, period).
        today: Data di riferimento (default: oggi in fuso Italia).

    Returns:
        datetime timezone-aware se la conversione riesce, altrimenti None.
    """
    today_dt = today or datetime.now(TZ_ITALY).date()
    date_pref = (preferences.date_preference or "").strip().lower()
    time_pref = (preferences.time_preference or "").strip().lower()
    period = (preferences.period or "").strip().lower()

    # Determina la data target
    target_date = _resolve_date(date_pref, today_dt)
    if target_date is None:
        logger.warning("Impossibile risolvere la data da: %r", date_pref)
        return None

    # Determina l'ora target
    target_time = _resolve_time(time_pref, period)

    result = datetime.combine(target_date, target_time, tzinfo=TZ_ITALY)
    logger.info("CHRONO: %r + %r → %s", date_pref, time_pref, result.isoformat())
    return result


# ── Risoluzione data ───────────────────────────────────────────────────────


def _resolve_date(date_pref: str, today: date) -> Optional[date]:
    """Risolve l'espressione di data in un oggetto date.

    Args:
        date_pref: Espressione naturale in italiano (o vuota).
        today: Data di riferimento.

    Returns:
        Oggetto date oppure None se non risolvibile.
    """
    if not date_pref:
        # Nessuna preferenza → primo giorno lavorativo disponibile
        return _next_working_day(today + timedelta(days=1))

    # ── Keyword semplici ──────────────────────────────────────────────────
    if date_pref in ("oggi", "oggi stesso"):
        return today
    if date_pref in ("domani", "domani mattina", "domani pomeriggio"):
        return today + timedelta(days=1)
    if date_pref in ("dopodomani",):
        return today + timedelta(days=2)

    # ── "tra N giorni / settimane" ────────────────────────────────────────
    match = re.match(r"tra\s+(\d+)\s+(giorn[oi]|settiman[ae])", date_pref)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        delta = timedelta(days=n) if "giorn" in unit else timedelta(weeks=n)
        return today + delta

    # ── Giorno della settimana ("lunedì", "prossimo martedì") ─────────────
    for nome, weekday in _GIORNI_IT.items():
        if nome in date_pref:
            return _next_weekday(today, weekday, force_next="prossim" in date_pref)

    # ── "N mese" (es. "15 agosto", "3 settembre") ─────────────────────────
    match = re.match(r"(\d{1,2})\s+(" + "|".join(_MESI_IT.keys()) + r")", date_pref)
    if match:
        day = int(match.group(1))
        month = _MESI_IT[match.group(2)]
        year = today.year if month >= today.month else today.year + 1
        try:
            return date(year, month, day)
        except ValueError:
            logger.warning("Data non valida: %d/%d/%d", day, month, year)
            return None

    # ── Fallback: dateutil parser con hint italiano ────────────────────────
    try:
        parsed = dateutil_parser.parse(
            date_pref,
            dayfirst=True,
            default=datetime(today.year, today.month, today.day),
        )
        return parsed.date()
    except Exception:
        pass

    return None


def _resolve_time(time_pref: str, period: str) -> time:
    """Risolve la preferenza oraria in un oggetto time.

    Args:
        time_pref: Espressione oraria ("10:00", "mattina", ecc.).
        period: Periodo del giorno ("mattina", "pomeriggio", "sera").

    Returns:
        Oggetto time (default 09:00 se non specificato).
    """
    # Orario esplicito "HH:MM" o "HH"
    match = re.search(r"(\d{1,2})[:.](\d{2})", time_pref)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return time(h, m)

    match = re.match(r"(\d{1,2})$", time_pref)
    if match:
        h = int(match.group(1))
        if 0 <= h <= 23:
            return time(h, 0)

    # Periodo del giorno
    for kw in (time_pref, period):
        for period_name, hour in _PERIOD_DEFAULT_HOUR.items():
            if period_name in kw:
                return time(hour, 0)

    # Default: 09:00
    return time(9, 0)


# ── Helpers ────────────────────────────────────────────────────────────────


def _next_weekday(from_date: date, weekday: int, force_next: bool = False) -> date:
    """Restituisce il prossimo giorno della settimana indicato.

    Args:
        from_date: Data di partenza.
        weekday: 0=lunedì … 6=domenica.
        force_next: Se True, salta alla settimana successiva anche se
                    il giorno è ancora nel corso della settimana corrente.

    Returns:
        Data del prossimo giorno target.
    """
    days_ahead = weekday - from_date.weekday()
    if days_ahead <= 0 or force_next:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def _next_working_day(from_date: date) -> date:
    """Restituisce il prossimo giorno lavorativo (lun-sab).

    Args:
        from_date: Data di partenza.

    Returns:
        Primo giorno lun-sab a partire da from_date.
    """
    d = from_date
    while d.weekday() == 6:  # domenica → salta
        d += timedelta(days=1)
    return d
