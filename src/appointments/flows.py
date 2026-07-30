"""State machine conversazionale – cuore del sistema di booking WhatsApp.

Implementa INTEGRALMENTE il flusso del diagramma Mermaid (codice-Mermaid.txt).

Mappa nodo → metodo:
  MSG        → FlowEngine.process_message()
  TIME       → (timestamp salvato in ctx.last_message_at)
  LLM        → llm_handler.extract_intent_and_entities()
  INTENT     → FlowEngine._route_by_intent()
  INFO       → FlowEngine._handle_info()
  ALTRO      → FlowEngine._handle_altro()
  CHRONO     → chrono.normalize_date_preference()
  UPDATE     → FlowEngine._update_context()
  LIMIT      → FlowEngine._check_date_limit()
  LIMITNO    → FlowEngine._send_limit_exceeded()
  CALENDAR   → calendar_search.search_available_slots()
  RULES      → (interno a calendar_search via business_rules)
  RESULT     → (slot salvati in ctx.proposed_slots)
  FOUND      → len(slots) > 0
  NOSLOT     → FlowEngine._send_no_slots()
  OFFER      → FlowEngine._send_slot_offer()
  WAIT       → FlowEngine._set_waiting_reply()
  REPLY      → (gestito da process_message in ingresso)
  EXPIRE     → FlowEngine._handle_expiry()
  INTERPRET  → llm_handler.interpret_user_choice()
  CHOICE     → FlowEngine._route_by_choice()
  ACCEPT     → FlowEngine._handle_accept()
  VERIFY     → calendar_search.verify_slot_available()
  CREATE     → service.create_appointment()
  CONFIRM    → FlowEngine._send_confirmation()
  REJECT     → FlowEngine._handle_reject()
  CYCLE      → ctx.proposal_cycle += 1
  MAX        → ctx.proposal_cycle >= settings.MAX_PROPOSAL_CYCLES
  AGAIN      → FlowEngine._search_and_offer() con excluded slots
  PREF       → FlowEngine._update_preferences()
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.appointments import calendar_search, chrono, llm_handler
from src.appointments.repository import ConversationRepository
from src.appointments.service import AppointmentService
from src.config import settings
from src.models import ConversationContext, ConversationState
from src.schemas import SlotInfo, UserPreferences
from src.whatsapp.client import whatsapp_client
from src.whatsapp.templates import build_confirmation_template, build_cancellation_template

logger = logging.getLogger(__name__)
TZ_ITALY = ZoneInfo("Europe/Rome")

# ── Messaggi testuali dello studio (costanti) ─────────────────────────────

MSG_WELCOME = (
    "Ciao! 👋 Sono l'assistente dello studio.\n"
    "Posso aiutarti a *prenotare*, *modificare* o *cancellare* un appuntamento.\n"
    "Come posso aiutarti?"
)
MSG_INFO = (
    "ℹ️ Per informazioni su servizi, orari e prezzi ti chiedo di contattarci direttamente:\n"
    "📞 [numero studio] oppure 📧 [email studio]"
)
MSG_NOT_UNDERSTOOD = (
    "Mi dispiace, non ho capito. 😅\n"
    "Scrivi *'prenotare'* per fissare un appuntamento oppure *'cancellare'* "
    "per cancellare uno esistente."
)
MSG_LIMIT_EXCEEDED = (
    "⚠️ Posso cercare appuntamenti solo entro i prossimi {days} giorni.\n"
    "Riprova con una data più vicina oppure contattaci direttamente."
)
MSG_NO_SLOTS = (
    "😔 Non ho trovato disponibilità per le tue preferenze.\n"
    "Ti invito a contattarci direttamente: potremmo trovare una soluzione su misura.\n"
    "📞 [numero studio]"
)
MSG_SLOT_OFFER_HEADER = (
    "📅 Ho trovato questi slot disponibili per *{service}*:\n\n"
)
MSG_SLOT_OFFER_FOOTER = (
    "\nRispondi con il numero dello slot che preferisci, "
    "oppure scrivi *'nessuno'* per vederne altri."
)
MSG_CONFIRM = (
    "✅ Perfetto! Il tuo appuntamento è confermato:\n\n"
    "📋 Servizio: *{service}*\n"
    "📅 Data: *{datetime_label}*\n\n"
    "Ti ricorderemo l'appuntamento in anticipo. A presto! 🙂"
)
MSG_SLOT_TAKEN = (
    "⚠️ Lo slot che hai scelto non è più disponibile.\n"
    "Ecco altre opzioni:"
)
MSG_EXPIRY = (
    "⏱️ La tua sessione è scaduta dopo {minutes} minuti di inattività.\n"
    "Scrivi di nuovo quando vuoi prenotare!"
)
MSG_MAX_CYCLES = (
    "😔 Ho esaurito le opzioni disponibili per le tue preferenze.\n"
    "Contattaci direttamente per trovare una soluzione: 📞 [numero studio]"
)
MSG_CANCEL_LIST = (
    "Ecco i tuoi appuntamenti futuri:\n\n{list}\n\n"
    "Scrivi il *numero* dell'appuntamento che vuoi cancellare."
)
MSG_CANCEL_NONE = "Non hai appuntamenti futuri da cancellare. 🙂"
MSG_CANCEL_CONFIRM = (
    "✅ Il tuo appuntamento del *{datetime_label}* è stato cancellato.\n"
    "Se vuoi prenotarne uno nuovo, scrivi *'prenotare'*."
)
MSG_CANCEL_NOT_FOUND = (
    "Non ho trovato quell'appuntamento. Riprova con il numero corretto."
)


# ════════════════════════════════════════════════════════════════
# FlowEngine
# ════════════════════════════════════════════════════════════════


class FlowEngine:
    """State machine conversazionale completa.

    Ogni chiamata a process_message() corrisponde alla ricezione di
    un nuovo messaggio WhatsApp (nodo MSG del Mermaid).
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._conv_repo = ConversationRepository(db)
        self._appt_service = AppointmentService(db)

    # ── Entry point principale (nodo MSG) ─────────────────────────────────

    async def process_message(
        self,
        phone_number: str,
        message_text: str,
        message_id: str = "",
    ) -> None:
        """Processa un messaggio in arrivo e avanza la state machine.

        Nodo Mermaid: MSG → TIME → (routing per stato e intent)

        Args:
            phone_number: Numero mittente E.164.
            message_text: Testo del messaggio.
            message_id: ID messaggio WhatsApp (per tracciamento).
        """
        # Nodo TIME: salva timestamp
        now = datetime.now(TZ_ITALY)
        ctx = self._conv_repo.get_or_create(phone_number)
        ctx.last_message_at = now

        logger.info(
            "process_message: phone=%s*** state=%s msg=%r",
            phone_number[:4],
            ctx.state,
            message_text[:50],
        )

        # ── Branch per stato corrente ──────────────────────────────────

        if ctx.state == ConversationState.WAITING_REPLY:
            # Verifica scadenza timer (nodo REPLY → NO → EXPIRE)
            if ctx.waiting_reply_until and now > ctx.waiting_reply_until:
                await self._handle_expiry(ctx)
                return
            # Messaggio ricevuto in tempo → nodo INTERPRET
            await self._handle_waiting_reply(ctx, message_text)
            return

        if ctx.state == ConversationState.WAITING_FOR_CANCEL_CONFIRMATION:
            await self._handle_cancel_selection(ctx, message_text)
            return

        # Stato IDLE o APPOINTMENT_CREATED → nuovo flusso
        await self._start_new_flow(ctx, message_text, message_id)

    # ── Avvio nuovo flusso (da IDLE) ──────────────────────────────────────

    async def _start_new_flow(
        self,
        ctx: ConversationContext,
        message_text: str,
        message_id: str,
    ) -> None:
        """Avvia un nuovo flusso conversazionale dall'inizio.

        Nodo Mermaid: LLM → INTENT → (BOOKING / INFO / ALTRO)
        """
        # Nodo LLM: estrazione intent + entità
        llm_result = llm_handler.extract_intent_and_entities(message_text)

        # Salva intent nel context (nodo UPDATE)
        ctx.intent = llm_result.intent
        self._conv_repo.save(ctx)

        logger.info("INTENT riconosciuto: %s", llm_result.intent)

        # Nodo INTENT: routing
        await self._route_by_intent(ctx, llm_result.intent, llm_result.entities, message_text)

    # ── Routing per intent ────────────────────────────────────────────────

    async def _route_by_intent(
        self,
        ctx: ConversationContext,
        intent: str,
        entities: UserPreferences,
        raw_message: str,
    ) -> None:
        """Nodo Mermaid: INTENT → BOOKING | INFO | ALTRO"""
        if intent == "BOOKING":
            await self._handle_booking(ctx, entities)
        elif intent == "INFO":
            await self._handle_info(ctx)
        else:
            await self._handle_altro(ctx, raw_message)

    # ── Flusso INFO ───────────────────────────────────────────────────────

    async def _handle_info(self, ctx: ConversationContext) -> None:
        """Nodo Mermaid: INFO → risposta informativa."""
        whatsapp_client.send_text(ctx.phone_number, MSG_INFO)

    # ── Flusso ALTRO ──────────────────────────────────────────────────────

    async def _handle_altro(self, ctx: ConversationContext, raw_message: str) -> None:
        """Nodo Mermaid: ALTRO → risposta generica di aiuto."""
        # Saluto iniziale se il messaggio sembra un inizio conversazione
        greetings = {"ciao", "salve", "buongiorno", "buonasera", "hello", "hi"}
        if any(g in raw_message.lower() for g in greetings):
            whatsapp_client.send_text(ctx.phone_number, MSG_WELCOME)
        else:
            whatsapp_client.send_text(ctx.phone_number, MSG_NOT_UNDERSTOOD)

    # ── Flusso BOOKING ────────────────────────────────────────────────────

    async def _handle_booking(
        self,
        ctx: ConversationContext,
        entities: UserPreferences,
    ) -> None:
        """Avvia il flusso di prenotazione.

        Nodo Mermaid: BOOKING → CHRONO → UPDATE → LIMIT → CALENDAR → RULES → FOUND?
        """
        # Nodo CHRONO: normalizzazione date
        hint_dt = chrono.normalize_date_preference(entities)

        # Nodo UPDATE: aggiorna contesto con preferenze
        ctx.preferences = entities.model_dump()
        ctx.proposed_slots = []
        ctx.rejected_slots = []
        ctx.proposal_cycle = 0
        self._conv_repo.save(ctx)

        # Nodo LIMIT: data entro limite configurato?
        if hint_dt and not self._check_date_limit(hint_dt):
            await self._send_limit_exceeded(ctx)
            return

        # CALENDAR + RULES + RESULT + FOUND → OFFER o NOSLOT
        await self._search_and_offer(ctx, hint_dt)

    # ── Check limite data (nodo LIMIT) ────────────────────────────────────

    def _check_date_limit(self, dt: datetime) -> bool:
        """Verifica che la data sia entro SEARCH_DAYS.

        Nodo Mermaid: LIMIT → SI/NO

        Args:
            dt: Data da verificare.

        Returns:
            True se entro il limite, False altrimenti.
        """
        now = datetime.now(TZ_ITALY)
        limit = now + timedelta(days=settings.SEARCH_DAYS)
        return dt <= limit

    async def _send_limit_exceeded(self, ctx: ConversationContext) -> None:
        """Nodo Mermaid: LIMITNO → risposta prenotazione oltre limite."""
        whatsapp_client.send_text(
            ctx.phone_number,
            MSG_LIMIT_EXCEEDED.format(days=settings.SEARCH_DAYS),
        )

    # ── Ricerca slot e proposta (CALENDAR → FOUND → OFFER/NOSLOT) ─────────

    async def _search_and_offer(
        self,
        ctx: ConversationContext,
        hint_dt: Optional[datetime],
    ) -> None:
        """Cerca slot disponibili e li propone all'utente.

        Nodo Mermaid: CALENDAR → RULES → RESULT → FOUND? → OFFER / NOSLOT

        Args:
            ctx: Contesto conversazione corrente.
            hint_dt: Datetime suggerito dal CHRONO (può essere None).
        """
        preferences = UserPreferences(**(ctx.preferences or {}))
        excluded = list(ctx.rejected_slots or [])

        # Nodo CALENDAR (+ RULES interno)
        slots: list[SlotInfo] = calendar_search.search_available_slots(
            db=self._db,
            preferences=preferences,
            hint_datetime=hint_dt,
            excluded_slots=excluded,
            max_results=settings.FIRST_OFFER_SLOTS * 3,
        )

        # Nodo FOUND?
        if not slots:
            logger.info("FOUND: nessuno slot disponibile → NOSLOT")
            await self._send_no_slots(ctx)
            return

        # Prendi i primi N slot (nodo OFFER: first_offer_slots=3)
        to_offer = slots[: settings.FIRST_OFFER_SLOTS]

        # Nodo RESULT: salva slot proposti nel contesto
        ctx.proposed_slots = [s.datetime_iso for s in to_offer]
        self._conv_repo.save(ctx)

        # Nodo OFFER: mostra slot
        await self._send_slot_offer(ctx, to_offer, preferences.service_name or "appuntamento")

        # Nodo WAIT: imposta timer e stato
        self._set_waiting_reply(ctx)

    # ── Invio proposta slot (nodo OFFER) ──────────────────────────────────

    async def _send_slot_offer(
        self,
        ctx: ConversationContext,
        slots: list[SlotInfo],
        service_name: str,
    ) -> None:
        """Invia la lista di slot disponibili all'utente.

        Nodo Mermaid: OFFER → mostra primi 3 slot
        Usa lista interattiva WhatsApp se possibile, altrimenti testo.
        """
        # Costruisci testo con numerazione
        slot_lines = ""
        for i, slot in enumerate(slots, 1):
            slot_lines += f"  *{i}.* {slot.label}\n"

        text = (
            MSG_SLOT_OFFER_HEADER.format(service=service_name)
            + slot_lines
            + MSG_SLOT_OFFER_FOOTER
        )

        # Usa lista interattiva WhatsApp
        sections = [
            {
                "title": "Slot disponibili",
                "rows": [
                    {
                        "id": f"slot_{i}",
                        "title": slot.label[:24],  # Max 24 chars per WhatsApp
                        "description": service_name[:72],
                    }
                    for i, slot in enumerate(slots)
                ],
            }
        ]

        try:
            whatsapp_client.send_interactive_list(
                phone_number=ctx.phone_number,
                body_text=MSG_SLOT_OFFER_HEADER.format(service=service_name) + slot_lines,
                button_label="Scegli slot",
                sections=sections,
            )
        except Exception:
            # Fallback testo semplice
            logger.warning("Lista interattiva non disponibile, uso testo semplice.")
            whatsapp_client.send_text(ctx.phone_number, text)

    # ── Timer attesa risposta (nodo WAIT) ─────────────────────────────────

    def _set_waiting_reply(self, ctx: ConversationContext) -> None:
        """Imposta lo stato WAITING_REPLY e il timer.

        Nodo Mermaid: WAIT → WAITING_REPLY, timer REPLY_TIMEOUT_MINUTES
        """
        now = datetime.now(TZ_ITALY)
        ctx.state = ConversationState.WAITING_REPLY
        ctx.waiting_reply_until = now + timedelta(minutes=settings.REPLY_TIMEOUT_MINUTES)
        self._conv_repo.save(ctx)
        logger.info(
            "WAIT: stato=WAITING_REPLY, scadenza=%s",
            ctx.waiting_reply_until.isoformat(),
        )

    # ── Gestione risposta utente (nodo INTERPRET + CHOICE) ────────────────

    async def _handle_waiting_reply(
        self,
        ctx: ConversationContext,
        message_text: str,
    ) -> None:
        """Interpreta la risposta dell'utente allo slot offer.

        Nodo Mermaid: REPLY → SI → INTERPRET → CHOICE → ACCETTA/RIFIUTA/NUOVE_PREF
        """
        # Nodo INTERPRET: LLM interpreta la risposta
        proposed_labels = self._get_proposed_labels(ctx)
        choice_result = llm_handler.interpret_user_choice(message_text, proposed_labels)

        logger.info("CHOICE: %s (slot_index=%s)", choice_result.choice, choice_result.slot_index)

        # Nodo CHOICE: routing
        if choice_result.choice == "ACCETTA":
            await self._handle_accept(ctx, choice_result.slot_index)

        elif choice_result.choice == "RIFIUTA":
            await self._handle_reject(ctx)

        elif choice_result.choice == "NUOVE_PREFERENZE":
            await self._handle_new_preferences(ctx, choice_result.new_preferences)

        else:
            # SCONOSCIUTO → chiedi di ripetere
            whatsapp_client.send_text(
                ctx.phone_number,
                "Non ho capito. Rispondi con il numero dello slot che preferisci "
                "oppure scrivi *'nessuno'* per altre opzioni.",
            )

    # ── Accetta slot (nodo ACCEPT → VERIFY → CREATE/AGAIN) ────────────────

    async def _handle_accept(
        self,
        ctx: ConversationContext,
        slot_index: Optional[int],
    ) -> None:
        """Gestisce l'accettazione di uno slot.

        Nodo Mermaid: ACCEPT → VERIFY → SI→CREATE / NO→AGAIN
        """
        proposed = list(ctx.proposed_slots or [])

        # Determina slot scelto
        if slot_index is not None and 0 <= slot_index < len(proposed):
            chosen_iso = proposed[slot_index]
        elif proposed:
            chosen_iso = proposed[0]  # default al primo
        else:
            await self._send_no_slots(ctx)
            return

        chosen_dt = datetime.fromisoformat(chosen_iso)
        preferences = UserPreferences(**(ctx.preferences or {}))
        service_name = preferences.service_name or "appuntamento"

        # Nodo VERIFY: slot ancora libero?
        still_free = calendar_search.verify_slot_available(self._db, chosen_dt, service_name)

        if not still_free:
            logger.info("VERIFY: slot %s non più disponibile → AGAIN", chosen_iso)
            whatsapp_client.send_text(ctx.phone_number, MSG_SLOT_TAKEN)
            # Aggiungi agli esclusi e cerca di nuovo
            rejected = list(ctx.rejected_slots or [])
            rejected.append(chosen_iso)
            ctx.rejected_slots = rejected
            self._conv_repo.save(ctx)
            await self._search_and_offer(ctx, chosen_dt)
            return

        # Nodo CREATE: crea appuntamento
        ctx.selected_slot = chosen_iso
        self._conv_repo.save(ctx)

        try:
            appt = self._appt_service.create_appointment(
                phone_number=ctx.phone_number,
                service_name=service_name,
                appointment_datetime=chosen_dt,
                customer_name=preferences.customer_name,
            )
        except Exception as exc:
            logger.error("CREATE fallito: %s", exc)
            whatsapp_client.send_text(
                ctx.phone_number,
                "❌ Si è verificato un errore nella creazione dell'appuntamento. Riprova.",
            )
            return

        # Nodo CONFIRM
        await self._send_confirmation(ctx, appt.service_name, chosen_dt)

        # Reset stato
        self._conv_repo.reset(ctx.phone_number)
        # Marca come APPOINTMENT_CREATED
        ctx2 = self._conv_repo.get_or_create(ctx.phone_number)
        ctx2.state = ConversationState.APPOINTMENT_CREATED
        self._conv_repo.save(ctx2)

    # ── Rifiuta slot (nodo REJECT → CYCLE → MAX → AGAIN/NOSLOT) ──────────

    async def _handle_reject(self, ctx: ConversationContext) -> None:
        """Gestisce il rifiuto degli slot proposti.

        Nodo Mermaid: REJECT → salva slot rifiutati → CYCLE → MAX? → AGAIN/NOSLOT
        """
        # Aggiungi tutti i proposti agli esclusi
        proposed = list(ctx.proposed_slots or [])
        rejected = list(ctx.rejected_slots or [])
        rejected.extend(proposed)
        ctx.rejected_slots = list(set(rejected))

        # Nodo CYCLE: incrementa ciclo
        ctx.proposal_cycle = (ctx.proposal_cycle or 0) + 1
        self._conv_repo.save(ctx)

        logger.info(
            "REJECT: ciclo=%d/%d",
            ctx.proposal_cycle,
            settings.MAX_PROPOSAL_CYCLES,
        )

        # Nodo MAX: ciclo massimo raggiunto?
        if ctx.proposal_cycle >= settings.MAX_PROPOSAL_CYCLES:
            logger.info("MAX cicli raggiunti → NOSLOT")
            await self._send_no_slots(ctx, use_max_msg=True)
            self._conv_repo.reset(ctx.phone_number)
            return

        # Nodo AGAIN: nuova ricerca escludendo slot rifiutati
        await self._search_and_offer(ctx, hint_dt=None)

    # ── Nuove preferenze (nodo PREF → CHRONO) ────────────────────────────

    async def _handle_new_preferences(
        self,
        ctx: ConversationContext,
        new_prefs: Optional[UserPreferences],
    ) -> None:
        """Aggiorna preferenze e rilancia la ricerca.

        Nodo Mermaid: NUOVE_PREFERENZE → PREF → CHRONO
        """
        if new_prefs is None:
            whatsapp_client.send_text(
                ctx.phone_number,
                "Puoi dirmi una data o un orario che preferisci? 📅",
            )
            return

        # Nodo PREF: aggiorna preferenze nel contesto
        existing = UserPreferences(**(ctx.preferences or {}))
        merged = UserPreferences(
            service_name=new_prefs.service_name or existing.service_name,
            date_preference=new_prefs.date_preference or existing.date_preference,
            time_preference=new_prefs.time_preference or existing.time_preference,
            period=new_prefs.period or existing.period,
            customer_name=new_prefs.customer_name or existing.customer_name,
        )
        ctx.preferences = merged.model_dump()
        self._conv_repo.save(ctx)

        # Nodo CHRONO: normalizzazione nuove date
        hint_dt = chrono.normalize_date_preference(merged)

        # Torna al CALENDAR
        await self._search_and_offer(ctx, hint_dt)

    # ── Scadenza timer (nodo EXPIRE) ──────────────────────────────────────

    async def _handle_expiry(self, ctx: ConversationContext) -> None:
        """Gestisce la scadenza del timer di attesa risposta.

        Nodo Mermaid: REPLY → NO → EXPIRE
        """
        logger.info(
            "EXPIRE: conversazione scaduta per %s*** (timeout %d min)",
            ctx.phone_number[:4],
            settings.REPLY_TIMEOUT_MINUTES,
        )
        whatsapp_client.send_text(
            ctx.phone_number,
            MSG_EXPIRY.format(minutes=settings.REPLY_TIMEOUT_MINUTES),
        )
        self._conv_repo.reset(ctx.phone_number)

    # ── Invio nessuno slot (nodo NOSLOT) ──────────────────────────────────

    async def _send_no_slots(
        self,
        ctx: ConversationContext,
        use_max_msg: bool = False,
    ) -> None:
        """Nodo Mermaid: NOSLOT → invita a contattare lo studio."""
        msg = MSG_MAX_CYCLES if use_max_msg else MSG_NO_SLOTS
        whatsapp_client.send_text(ctx.phone_number, msg)

    # ── Invio conferma prenotazione (nodo CONFIRM) ────────────────────────

    async def _send_confirmation(
        self,
        ctx: ConversationContext,
        service_name: str,
        slot_dt: datetime,
    ) -> None:
        """Nodo Mermaid: CONFIRM → conferma prenotazione."""
        from src.whatsapp.templates import _format_datetime_it

        label = _format_datetime_it(slot_dt)
        whatsapp_client.send_text(
            ctx.phone_number,
            MSG_CONFIRM.format(service=service_name, datetime_label=label),
        )
        logger.info(
            "CONFIRM inviata a %s*** per %s",
            ctx.phone_number[:4],
            slot_dt.isoformat(),
        )

    # ── Flusso cancellazione ──────────────────────────────────────────────

    async def _start_cancel_flow(self, ctx: ConversationContext) -> None:
        """Avvia il flusso di cancellazione appuntamento.

        Nodo Mermaid (Agents.md): WAITING_FOR_CANCEL_CONFIRMATION
        """
        future_appts = self._appt_service.list_future_appointments(ctx.phone_number)
        if not future_appts:
            whatsapp_client.send_text(ctx.phone_number, MSG_CANCEL_NONE)
            return

        from src.whatsapp.templates import _format_datetime_it

        appt_list = "\n".join(
            f"  *{i + 1}.* {_format_datetime_it(a.appointment_datetime)} – {a.service_name}"
            for i, a in enumerate(future_appts)
        )
        ctx.state = ConversationState.WAITING_FOR_CANCEL_CONFIRMATION
        # Salva temporaneamente gli ID degli appuntamenti futuri nel contesto
        ctx.proposed_slots = [str(a.id) for a in future_appts]
        self._conv_repo.save(ctx)

        whatsapp_client.send_text(
            ctx.phone_number,
            MSG_CANCEL_LIST.format(list=appt_list),
        )

    async def _handle_cancel_selection(
        self,
        ctx: ConversationContext,
        message_text: str,
    ) -> None:
        """Gestisce la selezione dell'appuntamento da cancellare."""
        try:
            idx = int(message_text.strip()) - 1
            appt_ids = [int(x) for x in (ctx.proposed_slots or [])]
            if idx < 0 or idx >= len(appt_ids):
                raise ValueError("Indice fuori range")
            appt_id = appt_ids[idx]
        except (ValueError, TypeError):
            whatsapp_client.send_text(ctx.phone_number, MSG_CANCEL_NOT_FOUND)
            return

        try:
            appt = self._appt_service.cancel_appointment(appt_id)
        except ValueError as exc:
            logger.warning("Cancellazione fallita: %s", exc)
            whatsapp_client.send_text(ctx.phone_number, MSG_CANCEL_NOT_FOUND)
            return

        from src.whatsapp.templates import _format_datetime_it

        label = _format_datetime_it(appt.appointment_datetime)
        whatsapp_client.send_text(
            ctx.phone_number,
            MSG_CANCEL_CONFIRM.format(datetime_label=label),
        )
        self._conv_repo.reset(ctx.phone_number)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_proposed_labels(self, ctx: ConversationContext) -> list[str]:
        """Converte gli ISO8601 degli slot proposti in label leggibili.

        Args:
            ctx: Contesto conversazione.

        Returns:
            Lista di stringhe label per gli slot proposti.
        """
        from src.appointments.calendar_search import _to_slot_info

        labels: list[str] = []
        for iso in (ctx.proposed_slots or []):
            try:
                dt = datetime.fromisoformat(iso)
                labels.append(_to_slot_info(dt).label)
            except ValueError:
                labels.append(iso)
        return labels


# ════════════════════════════════════════════════════════════════
# Job: gestione scadenza conversazioni (chiamato dallo scheduler)
# ════════════════════════════════════════════════════════════════


def expire_stale_conversations(db: Session) -> None:
    """Marca come scadute le conversazioni WAITING_REPLY con timer esaurito.

    Chiamato periodicamente dallo scheduler (ogni EXPIRY_CHECK_INTERVAL_SECONDS).

    Args:
        db: Sessione SQLAlchemy.
    """
    conv_repo = ConversationRepository(db)
    now = datetime.now(TZ_ITALY)
    expired = conv_repo.list_expired_waiting(now)

    for ctx in expired:
        logger.info(
            "Scadenza automatica conversazione: phone=%s*** (scaduta alle %s)",
            ctx.phone_number[:4],
            ctx.waiting_reply_until,
        )
        try:
            whatsapp_client.send_text(
                ctx.phone_number,
                MSG_EXPIRY.format(minutes=settings.REPLY_TIMEOUT_MINUTES),
            )
        except Exception as exc:
            logger.error(
                "Errore invio messaggio scadenza a %s***: %s",
                ctx.phone_number[:4],
                exc,
            )
        conv_repo.reset(ctx.phone_number)

    if expired:
        logger.info("Scadute %d conversazioni.", len(expired))
