"""Configurazione SQLAlchemy: engine, sessioni, Base declarativa.

Supporta SQLite (dev) e PostgreSQL/MySQL (prod) tramite la
variabile DATABASE_URL in config.
"""

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config import settings

logger = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────

_connect_args: dict = {}
if "sqlite" in settings.DATABASE_URL:
    # SQLite richiede check_same_thread=False per uso in FastAPI
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    echo=False,  # Impostare True in dev per vedere le query SQL
)

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
