"""Test della state machine conversazionale (flows.py).

Simula messaggi WhatsApp in arrivo e verifica che la FlowEngine
transiti correttamente tra gli stati del diagramma Mermaid.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from src.appointments.flows import FlowEngine
from src.models import ConversationContext, ConversationState
from src.schemas import LLMChoiceResult, LLMIntentResult, SlotInfo, UserPreferences

TZ_ITALY = ZoneInfo("Europe/Rome")


# ── Fixture ───────────────────────────────────────────────────────────────


def future_dt(hours: int = 24) -> datetime:
    return datetime.now(TZ_ITALY) + timedelta(hours=hours)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def engine(mock_db):
    return FlowEngine(mock_db)


@pytest.fixture
def idle_ctx():
    """ConversationContext in stato IDLE."""
    ctx = MagicMock(spec=ConversationContext)
    ctx.phone_number = "+39123456789"
    ctx.state = ConversationState.IDLE
    ctx.intent = None
    ctx.preferences = {}
    ctx.proposed_slots = []
    ctx.rejected_slots = []
    ctx.proposal_cycle = 0
    ctx.waiting_reply_until = None
    ctx.last_message_at = None
    return ctx


@pytest.fixture
def waiting_ctx(idle_ctx):
    """ConversationContext in stato WAITING_REPLY con slot proposti."""
    idle_ctx.state = ConversationState.WAITING_REPLY
    idle_ctx.waiting_reply_until = datetime.now(TZ_ITALY) + timedelta(minutes=10)
    idle_ctx.proposed_slots = [future_dt(25).isoformat(), future_dt(26).isoformat()]
    idle_ctx.preferences = {"service_name": "Visita di controllo"}
    idle_ctx.proposal_cycle = 0
    return idle_ctx


# ── Test: routing per intent ──────────────────────────────────────────────


class TestIntentRouting:
    """Test del nodo INTENT del Mermaid."""

    @pytest.mark.asyncio
    async def test_booking_intent_starts_booking_flow(self, engine, idle_ctx):
        """Intent BOOKING → avvia _handle_booking."""
        with patch.object(engine._conv_repo, "get_or_create", return_value=idle_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch.object(engine, "_handle_booking", new_callable=AsyncMock) as mock_booking, \
             patch(
                 "src.appointments.flows.llm_handler.extract_intent_and_entities",
                 return_value=LLMIntentResult(
                     intent="BOOKING",
                     entities=UserPreferences(service_name="Visita"),
                 ),
             ):
            await engine.process_message("+39123456789", "Vorrei prenotare")

        mock_booking.assert_called_once()

    @pytest.mark.asyncio
    async def test_info_intent_sends_info(self, engine, idle_ctx):
        """Intent INFO → invia messaggio informativo."""
        with patch.object(engine._conv_repo, "get_or_create", return_value=idle_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch(
                 "src.appointments.flows.llm_handler.extract_intent_and_entities",
                 return_value=LLMIntentResult(intent="INFO", entities=UserPreferences()),
             ), \
             patch("src.appointments.flows.whatsapp_client.send_text") as mock_send:
            await engine.process_message("+39123456789", "Quali sono i vostri orari?")

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert call_args[0] == "+39123456789"

    @pytest.mark.asyncio
    async def test_altro_intent_sends_help(self, engine, idle_ctx):
        """Intent ALTRO → invia messaggio di aiuto."""
        with patch.object(engine._conv_repo, "get_or_create", return_value=idle_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch(
                 "src.appointments.flows.llm_handler.extract_intent_and_entities",
                 return_value=LLMIntentResult(intent="ALTRO", entities=UserPreferences()),
             ), \
             patch("src.appointments.flows.whatsapp_client.send_text") as mock_send:
            await engine.process_message("+39123456789", "qualcosa di strano")

        mock_send.assert_called_once()


# ── Test: flusso BOOKING ──────────────────────────────────────────────────


class TestBookingFlow:
    """Test del flusso BOOKING (CHRONO→LIMIT→CALENDAR→OFFER→WAIT)."""

    @pytest.mark.asyncio
    async def test_booking_with_slots_sends_offer(self, engine, idle_ctx):
        """Slot trovati → invia proposta slot e imposta WAITING_REPLY."""
        slots = [
            SlotInfo(datetime_iso=future_dt(24).isoformat(), label="Domani alle 09:00"),
            SlotInfo(datetime_iso=future_dt(25).isoformat(), label="Dopodomani alle 10:00"),
        ]
        with patch.object(engine._conv_repo, "get_or_create", return_value=idle_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch("src.appointments.flows.chrono.normalize_date_preference", return_value=future_dt(24)), \
             patch("src.appointments.flows.calendar_search.search_available_slots", return_value=slots), \
             patch.object(engine, "_send_slot_offer", new_callable=AsyncMock) as mock_offer, \
             patch.object(engine, "_set_waiting_reply") as mock_wait:
            await engine._handle_booking(idle_ctx, UserPreferences(service_name="Visita"))

        mock_offer.assert_called_once()
        mock_wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_booking_no_slots_sends_noslot(self, engine, idle_ctx):
        """Nessuno slot trovato → invia NOSLOT."""
        with patch.object(engine._conv_repo, "get_or_create", return_value=idle_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch("src.appointments.flows.chrono.normalize_date_preference", return_value=future_dt(24)), \
             patch("src.appointments.flows.calendar_search.search_available_slots", return_value=[]), \
             patch("src.appointments.flows.whatsapp_client.send_text") as mock_send:
            await engine._handle_booking(idle_ctx, UserPreferences(service_name="Visita"))

        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_booking_date_over_limit_sends_limit_message(self, engine, idle_ctx):
        """Data oltre SEARCH_DAYS → invia messaggio limite."""
        from src.config import settings
        far_future = datetime.now(TZ_ITALY) + timedelta(days=settings.SEARCH_DAYS + 5)

        with patch.object(engine._conv_repo, "save"), \
             patch("src.appointments.flows.chrono.normalize_date_preference", return_value=far_future), \
             patch("src.appointments.flows.whatsapp_client.send_text") as mock_send:
            await engine._handle_booking(idle_ctx, UserPreferences(service_name="Visita"))

        mock_send.assert_called_once()
        # Verifica che il messaggio contenga riferimento al limite
        msg = mock_send.call_args[0][1]
        assert str(settings.SEARCH_DAYS) in msg


# ── Test: gestione risposta (WAITING_REPLY) ───────────────────────────────


class TestWaitingReplyFlow:
    """Test della gestione risposta utente (nodo INTERPRET + CHOICE)."""

    @pytest.mark.asyncio
    async def test_accept_choice_calls_handle_accept(self, engine, waiting_ctx):
        """Risposta ACCETTA → chiama _handle_accept."""
        with patch.object(engine._conv_repo, "get_or_create", return_value=waiting_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch(
                 "src.appointments.flows.llm_handler.interpret_user_choice",
                 return_value=LLMChoiceResult(choice="ACCETTA", slot_index=0),
             ), \
             patch.object(engine, "_handle_accept", new_callable=AsyncMock) as mock_accept:
            await engine.process_message("+39123456789", "Prendo il primo")

        mock_accept.assert_called_once_with(waiting_ctx, 0)

    @pytest.mark.asyncio
    async def test_reject_choice_calls_handle_reject(self, engine, waiting_ctx):
        """Risposta RIFIUTA → chiama _handle_reject."""
        with patch.object(engine._conv_repo, "get_or_create", return_value=waiting_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch(
                 "src.appointments.flows.llm_handler.interpret_user_choice",
                 return_value=LLMChoiceResult(choice="RIFIUTA"),
             ), \
             patch.object(engine, "_handle_reject", new_callable=AsyncMock) as mock_reject:
            await engine.process_message("+39123456789", "Nessuno va bene")

        mock_reject.assert_called_once()

    @pytest.mark.asyncio
    async def test_expired_timer_sends_expiry_message(self, engine, waiting_ctx):
        """Timer scaduto → invia messaggio di scadenza e resetta stato."""
        waiting_ctx.waiting_reply_until = datetime.now(TZ_ITALY) - timedelta(minutes=1)

        with patch.object(engine._conv_repo, "get_or_create", return_value=waiting_ctx), \
             patch.object(engine._conv_repo, "save"), \
             patch.object(engine._conv_repo, "reset") as mock_reset, \
             patch("src.appointments.flows.whatsapp_client.send_text") as mock_send:
            await engine.process_message("+39123456789", "ok")

        mock_send.assert_called_once()
        mock_reset.assert_called_once()


# ── Test: ciclo rifiuto (REJECT → CYCLE → MAX) ───────────────────────────


class TestRejectCycle:
    """Test del meccanismo di ciclo rifiuto (nodi REJECT, CYCLE, MAX)."""

    @pytest.mark.asyncio
    async def test_reject_within_max_cycles_searches_again(self, engine, waiting_ctx):
        """Rifiuto entro MAX → cerca di nuovo."""
        waiting_ctx.proposal_cycle = 0

        with patch.object(engine._conv_repo, "save"), \
             patch.object(engine, "_search_and_offer", new_callable=AsyncMock) as mock_search:
            await engine._handle_reject(waiting_ctx)

        mock_search.assert_called_once()
        assert waiting_ctx.proposal_cycle == 1

    @pytest.mark.asyncio
    async def test_reject_at_max_cycles_sends_noslot(self, engine, waiting_ctx):
        """Rifiuto al MAX cicli → invia NOSLOT."""
        from src.config import settings
        waiting_ctx.proposal_cycle = settings.MAX_PROPOSAL_CYCLES - 1

        with patch.object(engine._conv_repo, "save"), \
             patch.object(engine._conv_repo, "reset"), \
             patch.object(engine, "_send_no_slots", new_callable=AsyncMock) as mock_noslot:
            await engine._handle_reject(waiting_ctx)

        mock_noslot.assert_called_once_with(waiting_ctx, use_max_msg=True)
