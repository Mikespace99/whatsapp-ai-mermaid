"""Test del webhook WhatsApp (GET verifica + POST ricezione messaggi)."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from src.main import app
from src.config import settings

client = TestClient(app)


# ── GET /webhook – verifica token ────────────────────────────────────────


class TestWebhookVerify:
    """Test per la verifica GET del webhook Meta."""

    def test_verify_success(self):
        """Token corretto → risponde con hub_challenge."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "test_challenge_123",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            },
        )
        assert response.status_code == 200
        assert response.text == "test_challenge_123"

    def test_verify_wrong_token(self):
        """Token errato → 403 Forbidden."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "test_challenge_123",
                "hub.verify_token": "token_sbagliato",
            },
        )
        assert response.status_code == 403

    def test_verify_wrong_mode(self):
        """Mode diverso da 'subscribe' → 403."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.challenge": "test_challenge_123",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            },
        )
        assert response.status_code == 403


# ── POST /webhook – ricezione messaggi ───────────────────────────────────


class TestWebhookReceive:
    """Test per la ricezione POST del webhook."""

    def _make_payload(self, phone: str, text: str, msg_id: str = "msg_001") -> dict:
        """Costruisce un payload webhook Meta realistico."""
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "business_id",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "contacts": [{"wa_id": phone.replace("+", "")}],
                                "messages": [
                                    {
                                        "id": msg_id,
                                        "from": phone.replace("+", ""),
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

    def test_receive_text_message_returns_200(self):
        """POST con messaggio valido → 200 ok."""
        payload = self._make_payload("+39123456789", "Vorrei prenotare")
        with patch("src.whatsapp.webhook._dispatch_to_flow", new_callable=AsyncMock):
            response = client.post("/webhook", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_receive_empty_payload_returns_200(self):
        """POST con payload vuoto → 200 ok (Meta può inviare notifiche di stato)."""
        response = client.post("/webhook", json={})
        assert response.status_code == 200

    def test_receive_invalid_json_returns_200(self):
        """POST con body non JSON → 200 ok (non deve bloccare Meta)."""
        response = client.post(
            "/webhook",
            content=b"non-json-body",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200


# ── Test extrazione messaggi dal payload ──────────────────────────────────


class TestExtractMessages:
    """Test per _extract_messages."""

    def test_extract_text_message(self):
        """Estrae correttamente un messaggio di testo."""
        from src.whatsapp.webhook import _extract_messages

        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "39123456789"}],
                                "messages": [
                                    {
                                        "id": "msg_001",
                                        "from": "39123456789",
                                        "type": "text",
                                        "text": {"body": "Ciao"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        msgs = _extract_messages(payload)
        assert len(msgs) == 1
        assert msgs[0]["message_text"] == "Ciao"
        assert msgs[0]["phone_number"] == "+39123456789"
        assert msgs[0]["message_id"] == "msg_001"

    def test_extract_interactive_message(self):
        """Estrae risposta da lista interattiva (slot picker)."""
        from src.whatsapp.webhook import _extract_messages

        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "39123456789"}],
                                "messages": [
                                    {
                                        "id": "msg_002",
                                        "from": "39123456789",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "list_reply",
                                            "list_reply": {
                                                "id": "slot_0",
                                                "title": "Lunedì 4 agosto alle 09:00",
                                            },
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        msgs = _extract_messages(payload)
        assert len(msgs) == 1
        assert "slot_0" in msgs[0]["message_text"]

    def test_ignores_non_text_types(self):
        """Ignora messaggi di tipo immagine/audio."""
        from src.whatsapp.webhook import _extract_messages

        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {"type": "image", "image": {"id": "img_001"}}
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        msgs = _extract_messages(payload)
        assert len(msgs) == 0
