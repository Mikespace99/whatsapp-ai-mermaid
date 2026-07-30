"""Helper per la costruzione dei template message WhatsApp.

I template devono essere preventivamente approvati da Meta.
Questo modulo compila i parametri (variabili) per ciascun template.

Template previsti:
  - appointment_confirmation  → nodo CONFIRM del Mermaid
  - appointment_reminder      → job scheduler
  - appointment_cancellation  → flusso cancellazione
"""

from datetime import datetime
from typing import Any


# ── Helpers interni ───────────────────────────────────────────────────────


def _text_param(value: str) -> dict[str, Any]:
    """Restituisce un parametro di tipo testo per i componenti template."""
    return {"type": "text", "text": value}


def _format_datetime_it(dt: datetime) -> str:
    """Formatta una datetime in italiano leggibile.

    Args:
        dt: datetime timezone-aware.

    Returns:
        Es. 'lunedì 4 agosto 2025 alle 10:00'
    """
    GIORNI = [
        "lunedì", "martedì", "mercoledì",
        "giovedì", "venerdì", "sabato", "domenica",
    ]
    MESI = [
        "", "gennaio", "febbraio", "marzo", "aprile",
        "maggio", "giugno", "luglio", "agosto", "settembre",
        "ottobre", "novembre", "dicembre",
    ]
    giorno = GIORNI[dt.weekday()]
    mese = MESI[dt.month]
    return f"{giorno} {dt.day} {mese} {dt.year} alle {dt.strftime('%H:%M')}"


# ── Builder template: conferma prenotazione ───────────────────────────────


def build_confirmation_template(
    customer_name: str,
    service_name: str,
    appointment_datetime: datetime,
) -> dict[str, Any]:
    """Costruisce il payload template per la conferma prenotazione.

    Nodo Mermaid: CONFIRM

    Args:
        customer_name: Nome del cliente.
        service_name: Servizio prenotato.
        appointment_datetime: Data e ora appuntamento (timezone-aware).

    Returns:
        Dizionario 'template' da passare a send_template_message().
    """
    label = _format_datetime_it(appointment_datetime)
    return {
        "template_name": "appointment_confirmation",
        "language_code": "it",
        "components": [
            {
                "type": "body",
                "parameters": [
                    _text_param(customer_name or "Cliente"),
                    _text_param(service_name),
                    _text_param(label),
                ],
            }
        ],
    }


# ── Builder template: reminder appuntamento ───────────────────────────────


def build_reminder_template(
    customer_name: str,
    service_name: str,
    appointment_datetime: datetime,
) -> dict[str, Any]:
    """Costruisce il payload template per il reminder appuntamento.

    Inviato X ore prima dell'appuntamento dal job scheduler.

    Args:
        customer_name: Nome del cliente.
        service_name: Servizio prenotato.
        appointment_datetime: Data e ora appuntamento (timezone-aware).

    Returns:
        Dizionario 'template' da passare a send_template_message().
    """
    label = _format_datetime_it(appointment_datetime)
    return {
        "template_name": "appointment_reminder",
        "language_code": "it",
        "components": [
            {
                "type": "body",
                "parameters": [
                    _text_param(customer_name or "Cliente"),
                    _text_param(service_name),
                    _text_param(label),
                ],
            }
        ],
    }


# ── Builder template: cancellazione appuntamento ──────────────────────────


def build_cancellation_template(
    customer_name: str,
    service_name: str,
    appointment_datetime: datetime,
) -> dict[str, Any]:
    """Costruisce il payload template per la conferma di cancellazione.

    Args:
        customer_name: Nome del cliente.
        service_name: Servizio cancellato.
        appointment_datetime: Data e ora dell'appuntamento cancellato.

    Returns:
        Dizionario 'template' da passare a send_template_message().
    """
    label = _format_datetime_it(appointment_datetime)
    return {
        "template_name": "appointment_cancellation",
        "language_code": "it",
        "components": [
            {
                "type": "body",
                "parameters": [
                    _text_param(customer_name or "Cliente"),
                    _text_param(service_name),
                    _text_param(label),
                ],
            }
        ],
    }
