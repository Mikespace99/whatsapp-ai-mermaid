# Project Rules for Antigravity – WhatsApp Appointment Manager

## Obiettivo del progetto

Sviluppare un sistema in Python per gestire appuntamenti (prenotare, modificare, cancellare, inviare reminder) tramite **WhatsApp Business API** (Cloud API di Meta).

Il sistema deve:
- Esporre un webhook per ricevere messaggi WhatsApp in arrivo.
- Gestire un flusso conversazionale per:
  - fissare un appuntamento (data, ora, servizio, cliente)
  - modificare un appuntamento esistente
  - cancellare un appuntamento
  - confermare / ricordare un appuntamento (reminder)
- Salvare gli appuntamenti su un database (SQLite per dev, PostgreSQL/MySQL per prod).
- Inviare messaggi in uscita usando template approvati (per reminder, conferme, ecc.).
- Essere deployabile su un server con HTTPS (es. Docker + gunicorn/uvicorn).

## Stack tecnologico

- **Linguaggio**: Python 3.11+
- **Framework web**: FastAPI (preferito) o Flask
- **Database**: 
  - Dev: SQLite
  - Prod: PostgreSQL o MySQL
- **ORM**: SQLAlchemy (con migrazioni via Alembic, se necessario)
- **WhatsApp Business API**: Meta WhatsApp Cloud API (v18+)
- **Gestione segreti**: variabili d’ambiente (`.env` non commitato)
- **Logging**: `logging` standard di Python, con log su file e console
- **Testing**: pytest
- **Linting / formatting**: 
  - `ruff` per linting veloce
  - `black` per formattazione
- **Deployment**: Docker + Docker Compose (opzionale ma consigliato)

## Struttura del progetto

```text
.
├─ AGENTS.md
├─ README.md
├─ .env.example
├─ docker-compose.yml        # opzionale
├─ Dockerfile                # opzionale
├─ requirements.txt
├─ alembic/                  # se usi migrazioni
├─ src/
│  ├─__init__.py
│  ├─main.py                 # entry point FastAPI/Flask
│  ├─config.py               # config da env
│  ├─database.py             # configurazione DB e sessioni
│  ├─models.py               # modelli SQLAlchemy
│  ├─schemas.py              # pydantic schemas (se FastAPI)
│  ├─whatsapp/
│  │  ├─__init__.py
│  │  ├─client.py            # wrapper per WhatsApp Cloud API
│  │  ├─webhook.py           # endpoint /webhook per messaggi in arrivo
│  │  └─templates.py         # helper per template message
│  ├─appointments/
│  │  ├─__init__.py
│  │  ├─service.py           # logica business (crea, modifica, cancella)
│  │  ├─repository.py        # accesso DB per appuntamenti
│  │  └─flows.py             # gestione flussi conversazionali
│  └─scheduler/
│     ├─__init__.py
│     ├─reminder_job.py      # job per inviare reminder
│     └─scheduler.py         # configurazione scheduler (APScheduler / cron)
├─ tests/
│  ├─__init__.py
│  ├─test_whatsapp_webhook.py
│  ├─test_appointments_service.py
│  └─test_flows.py
└─ logs/
   └─ .gitkeep
```

Se il progetto è piccolo, puoi semplificare, ma mantieni la separazione:
- `whatsapp/` → integrazione API
- `appointments/` → logica business
- `scheduler/` → reminder

## Coding style

- Usa **type hints** in tutte le funzioni e metodi.
- Scrivi **docstring** in stile Google per ogni funzione pubblica.
- Mantieni funzioni piccole (< 50 righe); se una funzione cresce, spezzala.
- Usa nomi chiari ed espliciti (es. `create_appointment`, `cancel_appointment`, `send_reminder`).
- Evita codice “magico”: costanti per timeout, template ID, orari, ecc.
- Usa `logging` invece di `print`:
  - `INFO` per operazioni normali
  - `WARNING` per situazioni anomale ma non bloccanti
  - `ERROR` per errori che impediscono l’operazione

### Esempio di stile funzione

```python
from datetime import datetime
from typing import Optional

import logging

logger = logging.getLogger(__name__)


async def create_appointment(
    phone_number: str,
    service_name: str,
    appointment_datetime: datetime,
    customer_name: Optional[str] = None,
) -> dict:
    """Crea un nuovo appuntamento nel database.

    Args:
        phone_number: Numero di telefono del cliente (formato E.164).
        service_name: Nome del servizio prenotato.
        appointment_datetime: Data e ora dell'appuntamento (timezone-aware).
        customer_name: Nome del cliente, se disponibile.

    Returns:
        Un dizionario con l'ID dell'appuntamento creato e i dettagli.

    Raises:
        ValueError: Se i parametri non sono validi.
    """
    ...
```

## Gestione appuntamenti

### Modello dati minimo

Crea un modello `Appointment` con almeno:

- `id` (PK)
- `phone_number` (stringa, E.164)
- `customer_name` (stringa, opzionale)
- `service_name` (stringa)
- `appointment_datetime` (datetime, timezone-aware)
- `status` (enum: `SCHEDULED`, `CONFIRMED`, `CANCELLED`, `COMPLETED`, `NO_SHOW`)
- `created_at` (datetime)
- `updated_at` (datetime)
- `reminder_sent` (booleano)
- `whatsapp_message_id` (opzionale, per tracciare l’ultimo messaggio)

### Operazioni principali

Implementa nel modulo `appointments/service.py`:

- `create_appointment(...)`: crea un nuovo appuntamento.
- `update_appointment(appointment_id, new_datetime, ...)`: modifica data/ora o servizio.
- `cancel_appointment(appointment_id)`: cancella un appuntamento esistente.
- `list_appointments(phone_number, from_date, to_date)`: lista appuntamenti per cliente/periodo.
- `get_appointment(appointment_id)`: dettaglio appuntamento.

Ogni funzione deve:

- Validare input (date future, formato telefono, ecc.).
- Gestire conflitti (es. sovrapposizione orari).
- Loggare errori e successi.

## Integrazione WhatsApp Business API

### Configurazione

- Usa **Meta WhatsApp Cloud API** (non librerie non ufficiali per WhatsApp “personale”).
- Configura tramite variabili d’ambiente:

  - `WHATSAPP_PHONE_NUMBER_ID`
  - `WHATSAPP_BUSINESS_ACCOUNT_ID`
  - `WHATSAPP_ACCESS_TOKEN`
  - `WHATSAPP_VERIFY_TOKEN` (per il webhook)
  - `WHATSAPP_API_VERSION` (es. `v18.0`)
  - `WEBHOOK_URL` (URL pubblico del tuo server)

- Salva queste variabili in `.env` (non commitare `.env`), usa `.env.example` come template.

### Webhook

- Implementa un endpoint `/webhook` (GET per verifica, POST per messaggi).
- Verifica il token di verifica (GET) secondo la documentazione Meta.
- Per i messaggi in arrivo (POST):
  - Verifica la firma / token se richiesto.
  - Estrai:
    - `phone_number` del mittente
    - testo del messaggio
    - eventuali payload interattivi (bottoni, quick replies)
  - Instrada il messaggio al gestore di flussi conversazionali (`appointments/flows.py`).

### Flussi conversazionali

Il modulo `appointments/flows.py` deve gestire stati tipo:

- `WAITING_FOR_SERVICE`
- `WAITING_FOR_DATE`
- `WAITING_FOR_TIME`
- `WAITING_FOR_CONFIRMATION`
- `APPOINTMENT_CREATED`
- `WAITING_FOR_CANCEL_CONFIRMATION`

Ogni stato deve:

- Leggere il messaggio corrente.
- Aggiornare lo stato nel DB (o in cache, se necessario).
- Inviare risposte WhatsApp appropriate usando `whatsapp/client.py`.

Esempio di flusso per fissare appuntamento:

1. Utente: “Vorrei prenotare”
2. Bot: chiede servizio.
3. Utente: “Visita di controllo”
4. Bot: chiede data.
5. Utente: “domani”
6. Bot: propone slot orari.
7. Utente: sceglie ora.
8. Bot: riassume e chiede conferma.
9. Utente: conferma.
10. Bot: crea appuntamento e invia messaggio di conferma.

Per cancellare:

1. Utente: “Voglio cancellare il mio appuntamento”
2. Bot: cerca appuntamenti futuri per quel numero.
3. Bot: mostra elenco e chiede quale cancellare.
4. Utente: seleziona.
5. Bot: chiede conferma.
6. Utente: conferma.
7. Bot: aggiorna stato a `CANCELLED` e invia conferma.

### Template message

Per messaggi outbound (reminder, conferme):

- Usa **template message** approvati da Meta.
- Definisci template tipo:

  - `appointment_confirmation`
  - `appointment_reminder`
  - `appointment_cancellation`

- In `whatsapp/templates.py` crea helper per compilare i parametri:

```python
def build_reminder_template(
    customer_name: str,
    service_name: str,
    appointment_datetime: datetime,
) -> dict:
    """Costruisce il payload per un template di reminder appuntamento."""
    ...
```

- Invia i reminder tramite `whatsapp/client.py` usando `send_template_message`.

## Scheduler per reminder

- Implementa un job periodico (es. ogni 5–15 minuti) che:

  - Cerca appuntamenti con:
    - `status` in (`SCHEDULED`, `CONFIRMED`)
    - `appointment_datetime` tra X e Y (es. tra 24h e 1h prima)
    - `reminder_sent == False`
  - Per ognuno:
    - Invia un messaggio di reminder via WhatsApp.
    - Imposta `reminder_sent = True`.

- Usa:
  - `APScheduler` dentro l’app, oppure
  - un cron job / task separato in container dedicato.

- Logga ogni invio e gestisci errori (es. API non disponibile).

## Testing

- Usa **pytest** per testare:

  - Logica di creazione / modifica / cancellazione appuntamenti.
  - Flussi conversazionali (simulando messaggi in entrata).
  - Invio di messaggi (mockando il client WhatsApp).

- Struttura minima:

  - `tests/test_appointments_service.py`
  - `tests/test_flows.py`
  - `tests/test_whatsapp_webhook.py`

- Per ogni nuova feature:
  - Scrivi prima i test.
  - Implementa il codice per far passare i test.
  - Esegui `pytest -q` prima di considerare il task completato.

## Sicurezza e privacy

- Tratta i numeri di telefono e i dati degli appuntamenti come dati personali.
- Non loggare mai:
  - numeri completi nei log di produzione (al massimo oscura parte del numero)
  - contenuti sensibili dei messaggi
- Usa HTTPS per il webhook.
- Verifica il `WHATSAPP_VERIFY_TOKEN` per il webhook.
- Implementa rate limiting se esponi endpoint pubblici aggiuntivi.

## Error handling

- Cattura eccezioni specifiche (es. errori di DB, errori HTTP dell’API WhatsApp).
- Non usare `except Exception:` generico se non ai bordi dell’app (es. nel worker del scheduler).
- Per gli errori API WhatsApp:
  - Implementa retry con backoff esponenziale per errori 429/5xx.
  - Logga errori 4xx senza retry (es. token non valido, template errato).

## Deployment

- Prevedi un `Dockerfile` e un `docker-compose.yml` per:
  - app web
  - database
  - (opzionale) scheduler separato
- Usa variabili d’ambiente per configurare:
  - DB connection string
  - credenziali WhatsApp
  - URL del webhook
- In produzione:
  - Usa un DB reale (PostgreSQL/MySQL).
  - Configura un dominio con HTTPS per il webhook.
  - Imposta log su file o su un sistema di log centralizzato.

## Prompt di lavoro per Antigravity

Quando chiedi ad Antigravity di lavorare su questo progetto, usa prompt del tipo:

- “Esplora il progetto (@root) e proponi un piano per implementare il flusso di prenotazione appuntamento da zero.”
- “@src/appointments/flows.py Aggiungi uno stato `WAITING_FOR_CANCEL_CONFIRMATION` e gestisci il flusso di cancellazione appuntamento.”
- “@tests/test_flows.py Scrivi test per il flusso di prenotazione, simulando messaggi WhatsApp in arrivo.”
- “Rileggi `src/whatsapp/webhook.py` e verifica che rispetti le regole di sicurezza e logging in `AGENTS.md`. Correggi eventuali problemi.”

Segui sempre il ciclo:
1. **Exploration** (capire il codice esistente)
2. **Planning** (piano di implementazione)
3. **Execution** (test + implementazione)
4. **Review** (confronto con `AGENTS.md`)

## Note aggiuntive

- Non usare librerie non ufficiali per WhatsApp “personale” (es. `pywhatkit`, ecc.): punta solo su API ufficiali Meta.
- Se necessario, prevedi un semplice admin CLI o endpoint per:
  - listare appuntamenti
  - forzare invio reminder
  - cambiare stato manualmente
