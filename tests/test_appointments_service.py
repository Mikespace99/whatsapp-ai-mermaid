"""Test del service layer per la gestione degli appuntamenti."""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

from src.appointments.service import AppointmentService, _validate_phone, _validate_future_datetime
from src.models import Appointment, AppointmentStatus

TZ_ITALY = ZoneInfo("Europe/Rome")


# ── Fixture ───────────────────────────────────────────────────────────────


def future_dt(hours: int = 24) -> datetime:
    """Restituisce un datetime nel futuro."""
    return datetime.now(TZ_ITALY) + timedelta(hours=hours)


@pytest.fixture
def mock_db():
    """Sessione DB mockata."""
    return MagicMock()


@pytest.fixture
def service(mock_db):
    """AppointmentService con DB mockato."""
    return AppointmentService(mock_db)


# ── Test: _validate_phone ─────────────────────────────────────────────────


class TestValidatePhone:
    def test_valid_e164(self):
        _validate_phone("+39123456789")  # Non solleva

    def test_missing_plus(self):
        with pytest.raises(ValueError, match="E.164"):
            _validate_phone("39123456789")

    def test_contains_letters(self):
        with pytest.raises(ValueError):
            _validate_phone("+39abc456789")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            _validate_phone("")


# ── Test: _validate_future_datetime ──────────────────────────────────────


class TestValidateFutureDatetime:
    def test_future_aware_ok(self):
        _validate_future_datetime(future_dt(1))  # Non solleva

    def test_past_raises(self):
        past = datetime.now(TZ_ITALY) - timedelta(hours=1)
        with pytest.raises(ValueError, match="futuro"):
            _validate_future_datetime(past)

    def test_naive_raises(self):
        naive = datetime.now() + timedelta(hours=1)
        with pytest.raises(ValueError, match="timezone-aware"):
            _validate_future_datetime(naive)


# ── Test: create_appointment ──────────────────────────────────────────────


class TestCreateAppointment:
    def test_create_success(self, service, mock_db):
        """Crea un appuntamento con parametri validi."""
        # Mock del repository
        mock_appt = Appointment(
            id=1,
            phone_number="+39123456789",
            service_name="Visita di controllo",
            appointment_datetime=future_dt(24),
            status=AppointmentStatus.SCHEDULED,
        )
        with patch.object(
            service._repo, "create", return_value=mock_appt
        ) as mock_create, patch.object(
            service._repo, "list_by_phone", return_value=[]
        ):
            result = service.create_appointment(
                phone_number="+39123456789",
                service_name="Visita di controllo",
                appointment_datetime=future_dt(24),
                customer_name="Mario Rossi",
            )
        mock_create.assert_called_once()
        assert result.id == 1

    def test_create_invalid_phone(self, service):
        """Numero non E.164 → ValueError."""
        with pytest.raises(ValueError, match="E.164"):
            service.create_appointment(
                phone_number="3312345678",
                service_name="Visita",
                appointment_datetime=future_dt(24),
            )

    def test_create_past_date(self, service):
        """Data nel passato → ValueError."""
        with pytest.raises(ValueError, match="futuro"):
            service.create_appointment(
                phone_number="+39123456789",
                service_name="Visita",
                appointment_datetime=datetime.now(TZ_ITALY) - timedelta(hours=1),
            )


# ── Test: cancel_appointment ──────────────────────────────────────────────


class TestCancelAppointment:
    def test_cancel_success(self, service):
        """Cancella un appuntamento esistente."""
        mock_appt = Appointment(
            id=5,
            phone_number="+39111111111",
            service_name="Visita",
            appointment_datetime=future_dt(24),
            status=AppointmentStatus.SCHEDULED,
        )
        cancelled = Appointment(
            id=5,
            phone_number="+39111111111",
            service_name="Visita",
            appointment_datetime=future_dt(24),
            status=AppointmentStatus.CANCELLED,
        )
        with patch.object(service._repo, "get_by_id", return_value=mock_appt), \
             patch.object(service._repo, "update_status", return_value=cancelled):
            result = service.cancel_appointment(5)
        assert result.status == AppointmentStatus.CANCELLED

    def test_cancel_not_found(self, service):
        """Appuntamento inesistente → ValueError."""
        with patch.object(service._repo, "get_by_id", return_value=None):
            with pytest.raises(ValueError, match="non trovato"):
                service.cancel_appointment(999)

    def test_cancel_already_cancelled(self, service):
        """Già cancellato → ValueError."""
        already_cancelled = Appointment(
            id=3,
            status=AppointmentStatus.CANCELLED,
        )
        with patch.object(service._repo, "get_by_id", return_value=already_cancelled):
            with pytest.raises(ValueError, match="già cancellato"):
                service.cancel_appointment(3)


# ── Test: list_appointments ───────────────────────────────────────────────


class TestListAppointments:
    def test_list_valid_phone(self, service):
        """Lista appuntamenti per numero valido."""
        with patch.object(service._repo, "list_by_phone", return_value=[]) as mock_list:
            result = service.list_appointments("+39123456789")
        mock_list.assert_called_once()
        assert result == []

    def test_list_invalid_phone(self, service):
        """Numero non valido → ValueError."""
        with pytest.raises(ValueError):
            service.list_appointments("numero_sbagliato")
