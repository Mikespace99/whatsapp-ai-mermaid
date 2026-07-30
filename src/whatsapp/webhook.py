"""Endpoint webhook WhatsApp.

Implementa:
  GET /webhook → verifica del token (challenge Meta)
  POST /webhook → ricezione messaggi in arrivo e inoltro a flows.py

Sicurezza:
  - Verifica WHATSAPP_VERIFY_TOKEN sul GET
  - I numeri di telefono nei log sono oscurati (Agents.md § Sicurezza)
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


# ── GET /webhook – verifica Meta ──────────────────────────────────────────


@router.get("", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
) -> str:
    """Endpoint di verifica webhook richiesto da Meta.

    Meta invia una GET con hub.mode='subscribe' e hub.verify_token.
    Se il token coincide, rispondiamo con hub.challenge (plain text).

    Args:
        hub_mode: Deve essere 'subscribe'.
        hub_challenge: Stringa da restituire invariata a Meta.
        hub_verify_token: Token da confrontare con WHATSAPP_VERIFY_TOKEN.

    Returns:
        hub_challenge come plain text (richiesto da Meta).

    Raises:
        HTTPException 403: Se il token non corrisponde.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verificato correttamente da Meta.")
        return hub_challenge

    logger.warning("Verifica webhook fallita: token non valido.")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Token di verifica non valido.",
    )


# ── POST /webhook – ricezione messaggi ───────────────────────────────────


@router.post("", status_code=status.HTTP_200_OK)
async def receive_message(request: Request) -> dict[str, str]:
    """Riceve e processa i messaggi WhatsApp in arrivo.

    Meta richiede risposta 200 entro pochi secondi; tutta la logica
    pesante viene delegata a flows.py in modo sincrono (per ora).

    Args:
        request: Richiesta FastAPI con body JSON dal webhook Meta.

    Returns:
        {"status": "ok"} – sempre 200 per evitare retry di Meta.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        logger.warning("Payload webhook non parsabile, ignorato.")
        return {"status": "ok"}

    # Estrai i messaggi dal payload Meta
    messages = _extract_messages(body)

    if not messages:
        # Notifiche di status delivery, read receipt, ecc. → ignora
        return {"status": "ok"}

    for msg in messages:
        phone_number: str = msg.get("phone_number", "")
        message_text: str = msg.get("message_text", "")
        message_id: str = msg.get("message_id", "")

        logger.info(
            "Messaggio ricevuto da %s*** | msg_id=%s",
            phone_number[:4],
            message_id,
        )

        # Delega al flusso conversazionale
        await _dispatch_to_flow(phone_number, message_text, message_id)

    return {"status": "ok"}


# ── Helpers privati ───────────────────────────────────────────────────────


def _extract_messages(body: dict[str, Any]) -> list[dict[str, str]]:
    """Estrae i messaggi testuali dal payload webhook Meta.

    Gestisce il formato standard della Cloud API v18+.

    Args:
        body: Payload JSON completo del webhook.

    Returns:
        Lista di dict con phone_number, message_text, message_id.
    """
    results: list[dict[str, str]] = []
    try:
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for message in messages:
                    msg_type = message.get("type", "")

                    # Gestisci testo
                    if msg_type == "text":
                        text = message.get("text", {}).get("body", "").strip()
                    # Gestisci risposta lista interattiva (slot picker)
                    elif msg_type == "interactive":
                        interactive = message.get("interactive", {})
                        list_reply = interactive.get("list_reply", {})
                        text = list_reply.get("id", "") or list_reply.get("title", "")
                    else:
                        # Tipo non gestito (immagini, audio, ecc.)
                        continue

                    if not text:
                        continue

                    contacts = value.get("contacts", [])
                    phone = (
                        contacts[0].get("wa_id", "")
                        if contacts
                        else message.get("from", "")
                    )
                    if phone and not phone.startswith("+"):
                        phone = "+" + phone

                    results.append(
                        {
                            "phone_number": phone,
                            "message_text": text,
                            "message_id": message.get("id", ""),
                        }
                    )
    except Exception as exc:
        logger.error("Errore parsing payload webhook: %s", exc)

    return results


async def _dispatch_to_flow(
    phone_number: str,
    message_text: str,
    message_id: str,
) -> None:
    """Instrada il messaggio alla state machine conversazionale.

    Args:
        phone_number: Numero mittente E.164.
        message_text: Testo del messaggio.
        message_id: ID messaggio WhatsApp.
    """
    # Import qui per evitare import circolari
    from src.appointments.flows import FlowEngine
    from src.database import SessionLocal

    db = SessionLocal()
    try:
        engine = FlowEngine(db)
        await engine.process_message(phone_number, message_text, message_id)
    except Exception as exc:
        logger.error(
            "Errore nel flusso conversazionale per %s***: %s",
            phone_number[:4],
            exc,
        )
    finally:
        db.close()
