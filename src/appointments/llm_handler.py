"""Estrazione intent ed entità tramite LLM (OpenAI).

Corrisponde a due nodi del diagramma Mermaid:
  - LLM   → estrae intent, data, ora, periodo, servizio, nome
  - INTERPRET → interpreta la risposta utente agli slot proposti
             (ACCETTA / RIFIUTA / NUOVE_PREFERENZE)

Usa il modello configurato in settings.LLM_MODEL (default: gpt-4o-mini).
"""

import json
import logging
from typing import Any, Optional

from openai import OpenAI, OpenAIError

from src.config import settings
from src.schemas import LLMChoiceResult, LLMIntentResult, UserPreferences

logger = logging.getLogger(__name__)

# ── Client OpenAI (lazy init) ─────────────────────────────────────────────

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """Restituisce l'istanza OpenAI, creandola al primo uso."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


# ── Prompt di sistema ─────────────────────────────────────────────────────

_INTENT_SYSTEM_PROMPT = """
Sei un assistente per uno studio professionale italiano che gestisce prenotazioni via WhatsApp.
Analizza il messaggio dell'utente ed estrai le informazioni in formato JSON.

Restituisci SOLO un oggetto JSON valido (nessun testo aggiuntivo) con questa struttura:
{
  "intent": "BOOKING" | "INFO" | "ALTRO",
  "entities": {
    "service_name": string | null,
    "date_preference": string | null,
    "time_preference": string | null,
    "period": "mattina" | "pomeriggio" | "sera" | null,
    "customer_name": string | null
  }
}

Regole IMPORTANTI per classificare l'intent:

BOOKING → usa questa categoria in modo GENEROSO per qualsiasi messaggio che indica
  il desiderio di prenotare, fissare, prendere, avere, volere un appuntamento,
  o che chiede disponibilità/quando è possibile venire/orari liberi.
  Esempi BOOKING:
  - "vorrei prenotare", "voglio un appuntamento", "fissare visita"
  - "quando posso venire?", "quando sarebbe possibile?", "avete disponibilità?"
  - "c'è posto domani?", "quando siete liberi?", "vorrei venire la prossima settimana"
  - "ho bisogno di una visita", "devo prendere appuntamento", "posso prenotare?"
  - "visita dal dottore", "appuntamento col medico", "controllo", "consulenza"

INFO → SOLO se chiede esplicitamente prezzi, costi, quanto costa, lista servizi offerti.
  Esempi INFO:
  - "quanto costa una visita?", "che servizi offrite?", "quali sono i prezzi?"

ALTRO → saluti, ringraziamenti, messaggi non pertinenti.
  Esempi ALTRO:
  - "ciao", "grazie", "arrivederci", "ok"

IMPORTANTE: Se c'è anche solo un minimo dubbio tra BOOKING e INFO, scegli BOOKING.

Per le entities:
- date_preference: mantieni il testo originale ("domani", "lunedì", "15 agosto", "quando possibile")
- time_preference: mantieni il testo originale ("10:00", "mattina", "nel pomeriggio")
- Non inventare dati non presenti nel messaggio (usa null)
"""

_INTERPRET_SYSTEM_PROMPT = """
Sei un assistente per uno studio professionale italiano.
L'utente ha ricevuto una proposta di slot per un appuntamento e ha risposto.
Analizza la risposta ed estrai la scelta in formato JSON.

Restituisci SOLO un oggetto JSON valido con questa struttura:
{
  "choice": "ACCETTA" | "RIFIUTA" | "NUOVE_PREFERENZE" | "SCONOSCIUTO",
  "slot_index": number | null,
  "new_preferences": {
    "service_name": string | null,
    "date_preference": string | null,
    "time_preference": string | null,
    "period": string | null,
    "customer_name": string | null
  } | null
}

Regole per choice:
- ACCETTA: l'utente sceglie uno slot ("va bene", "scelgo il primo", "ok per lunedì")
- RIFIUTA: nessuno slot va bene ("no", "non va bene", "nessuno di questi")
- NUOVE_PREFERENZE: chiede date/ore diverse ("preferirei martedì", "la mattina non posso")
- SCONOSCIUTO: risposta incomprensibile

Per slot_index:
- 0 se sceglie il primo slot, 1 il secondo, 2 il terzo
- null se non si capisce quale slot ha scelto (o choice != ACCETTA)

Gli slot proposti (per contesto) sono passati nel messaggio utente.
"""


# ── Funzione pubblica: estrazione intent ──────────────────────────────────


def extract_intent_and_entities(
    message_text: str,
    conversation_history: Optional[list[dict[str, str]]] = None,
) -> LLMIntentResult:
    """Estrae intent ed entità dal messaggio utente.

    Nodo Mermaid: LLM → estrae intent, data, ora, periodo, servizio, nome

    Args:
        message_text: Testo del messaggio WhatsApp ricevuto.
        conversation_history: Storico conversazione per contesto aggiuntivo.

    Returns:
        LLMIntentResult con intent e entities.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
    ]
    if conversation_history:
        messages.extend(conversation_history[-4:])  # Max 4 turni di contesto
    messages.append({"role": "user", "content": message_text})

    raw = _call_llm(messages)
    return _parse_intent_result(raw, message_text)


# ── Funzione pubblica: interpretazione risposta slot ─────────────────────


def interpret_user_choice(
    message_text: str,
    proposed_slots_labels: list[str],
) -> LLMChoiceResult:
    """Interpreta la risposta dell'utente agli slot proposti.

    Nodo Mermaid: INTERPRET → interpreta risposta utente

    Args:
        message_text: Risposta dell'utente.
        proposed_slots_labels: Etichette human-readable degli slot proposti
                               (es. ["Lunedì 4 agosto alle 09:00", ...]).

    Returns:
        LLMChoiceResult con choice, slot_index, new_preferences.
    """
    slots_context = "\n".join(
        f"{i + 1}. {label}" for i, label in enumerate(proposed_slots_labels)
    )
    user_msg = f"Slot proposti:\n{slots_context}\n\nRisposta utente: {message_text}"

    messages = [
        {"role": "system", "content": _INTERPRET_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    raw = _call_llm(messages)
    return _parse_choice_result(raw)


# ── Helper: chiamata LLM ──────────────────────────────────────────────────


def _call_llm(messages: list[dict[str, str]]) -> str:
    """Chiama l'API OpenAI e restituisce il testo della risposta.

    Args:
        messages: Lista di messaggi nel formato OpenAI Chat.

    Returns:
        Testo della risposta del modello.

    Raises:
        OpenAIError: In caso di errori API.
    """
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        logger.info("LLM risposta ricevuta (%d chars).", len(content))
        return content
    except OpenAIError as exc:
        logger.error("Errore OpenAI API: %s", exc)
        raise


# ── Helper: parsing intent ────────────────────────────────────────────────


def _parse_intent_result(raw: str, original_message: str) -> LLMIntentResult:
    """Parsa la risposta JSON del LLM in LLMIntentResult.

    Args:
        raw: Stringa JSON restituita dal LLM.
        original_message: Messaggio originale (per fallback).

    Returns:
        LLMIntentResult con valori di default in caso di errori di parsing.
    """
    try:
        data: dict[str, Any] = json.loads(raw)
        intent = data.get("intent", "ALTRO").upper()
        if intent not in ("BOOKING", "INFO", "ALTRO"):
            intent = "ALTRO"

        entities_raw = data.get("entities", {}) or {}
        entities = UserPreferences(
            service_name=entities_raw.get("service_name"),
            date_preference=entities_raw.get("date_preference"),
            time_preference=entities_raw.get("time_preference"),
            period=entities_raw.get("period"),
            customer_name=entities_raw.get("customer_name"),
        )
        logger.info("LLM intent=%s entities=%s", intent, entities)
        return LLMIntentResult(intent=intent, entities=entities)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Parsing risposta LLM fallito: %s. Uso fallback ALTRO.", exc)
        return LLMIntentResult(intent="ALTRO", entities=UserPreferences())


# ── Helper: parsing choice ────────────────────────────────────────────────


def _parse_choice_result(raw: str) -> LLMChoiceResult:
    """Parsa la risposta JSON del LLM in LLMChoiceResult.

    Args:
        raw: Stringa JSON restituita dal LLM.

    Returns:
        LLMChoiceResult con valori di default in caso di errori.
    """
    try:
        data: dict[str, Any] = json.loads(raw)
        choice = data.get("choice", "SCONOSCIUTO").upper()
        if choice not in ("ACCETTA", "RIFIUTA", "NUOVE_PREFERENZE", "SCONOSCIUTO"):
            choice = "SCONOSCIUTO"

        slot_index = data.get("slot_index")
        if slot_index is not None:
            slot_index = int(slot_index)

        new_pref_raw = data.get("new_preferences")
        new_preferences: Optional[UserPreferences] = None
        if new_pref_raw and choice == "NUOVE_PREFERENZE":
            new_preferences = UserPreferences(
                service_name=new_pref_raw.get("service_name"),
                date_preference=new_pref_raw.get("date_preference"),
                time_preference=new_pref_raw.get("time_preference"),
                period=new_pref_raw.get("period"),
                customer_name=new_pref_raw.get("customer_name"),
            )

        logger.info("LLM choice=%s slot_index=%s", choice, slot_index)
        return LLMChoiceResult(
            choice=choice,
            slot_index=slot_index,
            new_preferences=new_preferences,
        )
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Parsing choice LLM fallito: %s. Uso fallback SCONOSCIUTO.", exc)
        return LLMChoiceResult(choice="SCONOSCIUTO")
