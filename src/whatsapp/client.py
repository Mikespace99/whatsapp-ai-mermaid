"""Wrapper per la Meta WhatsApp Cloud API.

Gestisce:
  - Invio messaggi di testo
  - Invio messaggi con lista interattiva (slot picker)
  - Invio template message (reminder, conferme, cancellazioni)
  - Retry con backoff esponenziale per errori 429 / 5xx

Riferimento API: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

import logging
import time
from typing import Any, Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# ── Costanti ──────────────────────────────────────────────────────────────

_BASE_URL = "https://graph.facebook.com"
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0  # secondi (raddoppia ad ogni retry)


# ── Client ────────────────────────────────────────────────────────────────


class WhatsAppClient:
    """Client per la WhatsApp Cloud API.

    Usa httpx in modalità sincrona. Per uso asincrono sostituire
    httpx.Client con httpx.AsyncClient e aggiungere await.
    """

    def __init__(self) -> None:
        self._base = (
            f"{_BASE_URL}/{settings.WHATSAPP_API_VERSION}"
            f"/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        self._headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }

    # ── Metodo interno: invia richiesta con retry ─────────────────────────

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Invia una richiesta POST all'API con retry + backoff esponenziale.

        Args:
            payload: Corpo JSON della richiesta.

        Returns:
            Risposta JSON dell'API.

        Raises:
            httpx.HTTPStatusError: Se tutti i retry falliscono.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=15.0) as client:
                    response = client.post(self._base, headers=self._headers, json=payload)

                if response.status_code in (429, 500, 502, 503, 504):
                    wait = _RETRY_BACKOFF_BASE**attempt
                    logger.warning(
                        "WhatsApp API errore %s (tentativo %d/%d). "
                        "Retry tra %.1fs.",
                        response.status_code,
                        attempt,
                        _MAX_RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                    continue

                # Errori 4xx → non fare retry
                if response.status_code >= 400:
                    logger.error(
                        "WhatsApp API errore %s: %s",
                        response.status_code,
                        response.text,
                    )
                    response.raise_for_status()

                return response.json()

            except httpx.RequestError as exc:
                last_exc = exc
                wait = _RETRY_BACKOFF_BASE**attempt
                logger.warning(
                    "Errore di rete WhatsApp API (tentativo %d/%d): %s. "
                    "Retry tra %.1fs.",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)

        logger.error("WhatsApp API non raggiungibile dopo %d tentativi.", _MAX_RETRIES)
        if last_exc:
            raise last_exc
        raise RuntimeError("WhatsApp API: tutti i retry esauriti.")

    # ── Messaggi testuali ─────────────────────────────────────────────────

    def send_text(self, phone_number: str, text: str) -> dict[str, Any]:
        """Invia un messaggio di testo semplice.

        Args:
            phone_number: Numero destinatario in formato E.164.
            text: Testo del messaggio (max 4096 caratteri).

        Returns:
            Risposta API con message_id.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_number,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        logger.info("Invio testo a %s***", phone_number[:4])
        return self._post(payload)

    # ── Lista interattiva (slot picker) ──────────────────────────────────

    def send_interactive_list(
        self,
        phone_number: str,
        body_text: str,
        button_label: str,
        sections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Invia un messaggio interattivo con lista di opzioni.

        Usato per mostrare gli slot disponibili (nodo OFFER del Mermaid).

        Args:
            phone_number: Numero destinatario E.164.
            body_text: Testo principale del messaggio.
            button_label: Etichetta pulsante (max 20 caratteri).
            sections: Lista sezioni con rows (id, title, description).

        Returns:
            Risposta API con message_id.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_number,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text},
                "action": {
                    "button": button_label,
                    "sections": sections,
                },
            },
        }
        logger.info("Invio lista interattiva a %s***", phone_number[:4])
        return self._post(payload)

    # ── Template messages ─────────────────────────────────────────────────

    def send_template_message(
        self,
        phone_number: str,
        template_name: str,
        language_code: str,
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Invia un template message approvato da Meta.

        Args:
            phone_number: Numero destinatario E.164.
            template_name: Nome del template approvato.
            language_code: Codice lingua, es. 'it'.
            components: Lista componenti (header, body, buttons) con parametri.

        Returns:
            Risposta API con message_id.
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": components,
            },
        }
        logger.info(
            "Invio template '%s' a %s***",
            template_name,
            phone_number[:4],
        )
        return self._post(payload)


# ── Istanza singleton ─────────────────────────────────────────────────────

whatsapp_client = WhatsAppClient()
