"""Configurazione SQLAlchemy: engine, sessioni, Base declarativa.

Supporta SQLite (dev) e PostgreSQL/MySQL (prod) tramite la
variabile DATABASE_URL in config.
"""

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config import settings

logger = logging.getLogger(__name__)

# ── Fix URL: Supabase usa postgres:// ma SQLAlchemy 2.0 richiede postgresql:// ──
_db_url = settings.DATABASE_URL
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    logger.info("DATABASE_URL: prefisso corretto postgres:// → postgresql://")

# ── Engine ────────────────────────────────────────────────────────────────

_connect_args: dict = {}
if "sqlite" in _db_url:
    _connect_args = {"check_same_thread": False}

_engine_kwargs: dict = {
    "connect_args": _connect_args,
    "pool_pre_ping": True,   # verifica connessione prima di usarla (Supabase chiude idle)
    "echo": False,
}

# PostgreSQL: limita il pool per il piano free di Supabase (max 15 connessioni)
if "postgresql" in _db_url:
    _engine_kwargs["pool_size"] = 5
    _engine_kwargs["max_overflow"] = 2
    _engine_kwargs["pool_recycle"] = 300  # ricicla connessioni ogni 5 minuti

engine = create_engine(_db_url, **_engine_kwargs)

# Abilita foreign keys su SQLite
if "sqlite" in settings.DATABASE_URL:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# ── Sessione ──────────────────────────────────────────────────────────────

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Base declarativa ──────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Base class per tutti i modelli SQLAlchemy."""


# ── Helpers ───────────────────────────────────────────────────────────────


def get_db():
    """Dependency FastAPI: fornisce una sessione DB e la chiude dopo l'uso.

    Yields:
        Session: sessione SQLAlchemy attiva.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Crea tutte le tabelle definite nei modelli (se non esistono già).

    Chiamare all'avvio dell'applicazione prima di accettare richieste.
    """
    from src import models  # noqa: F401 – importa per registrare i modelli

    Base.metadata.create_all(bind=engine)
    logger.info("Tabelle DB create/verificate con successo.")
