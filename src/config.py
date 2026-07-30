"""Configurazione centralizzata dell'applicazione.

Legge tutte le variabili d'ambiente dal file .env e le espone
come attributi tipizzati tramite pydantic-settings.

Nodo Mermaid di riferimento: CONFIG
  - search_days       → SEARCH_DAYS
  - reply_timeout     → REPLY_TIMEOUT_MINUTES
  - max_cycles        → MAX_PROPOSAL_CYCLES
  - first_offer_slots → FIRST_OFFER_SLOTS
"""

import logging
import logging.handlers
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Impostazioni globali dell'applicazione."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── WhatsApp Business API ──────────────────────────────────────────────
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = "default_verify_token"
    WHATSAPP_API_VERSION: str = "v18.0"
    WEBHOOK_URL: str = ""

    # ── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./appointments.db"

    # ── LLM (OpenAI) ──────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"

    # ── Studio Config (nodo CONFIG del Mermaid) ───────────────────────────
    SEARCH_DAYS: int = 30
    """Finestra di ricerca slot disponibili (giorni)."""

    REPLY_TIMEOUT_MINUTES: int = 10
    """Minuti di attesa risposta utente prima di scadenza (nodo WAIT)."""

    MAX_PROPOSAL_CYCLES: int = 2
    """Numero massimo di cicli di proposta slot (nodo MAX)."""

    FIRST_OFFER_SLOTS: int = 3
    """Numero di slot da mostrare al primo invio (nodo OFFER)."""

    # ── Scheduler ─────────────────────────────────────────────────────────
    REMINDER_CHECK_INTERVAL_MINUTES: int = 10
    """Frequenza controllo reminder (in minuti)."""

    REMINDER_HOURS_BEFORE: int = 24
    """Invia reminder X ore prima dell'appuntamento."""

    EXPIRY_CHECK_INTERVAL_SECONDS: int = 60
    """Frequenza controllo conversazioni scadute (in secondi)."""


# Istanza singleton
settings = Settings()

# ── Configurazione logging ─────────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def setup_logging() -> None:
    """Configura il logging su console e file rotante.

    Livelli:
        INFO    → operazioni normali
        WARNING → situazioni anomale non bloccanti
        ERROR   → errori che impediscono l'operazione
    """
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(console_handler)

    # File handler (rotante, max 5 MB × 3 backup)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(file_handler)
