import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")
os.environ["DATABASE_URL"] = "sqlite:///./test-import.db"

import pytest
from tests.asgi_client import ASGIClient as TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.core.config import get_business_tz
from app.db import Base, get_db, make_engine
from app.main import app
from app.models import AppointmentSlot, Role, User


@pytest.fixture
def api(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(username="gerente", full_name="Gerente", password_hash=hash_password("ManagerPassword123!"), role=Role.GERENTE))
        db.commit()

    def override():
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client, engine
    app.dependency_overrides.clear()
    engine.dispose()


def login(client, username="gerente", password="ManagerPassword123!"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def future_slot(days=7, hour=9):
    local = datetime.now(get_business_tz()) + timedelta(days=days)
    while local.weekday() == 6:
        local += timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def request_for(client, headers, plate="ABC123"):
    client_id = client.post("/api/v1/clients", headers=headers, json={"full_name": "Ana Pérez", "phone": "999888777"}).json()["id"]
    vehicle_id = client.post("/api/v1/vehicles", headers=headers, json={"client_id": client_id, "plate": plate}).json()["id"]
    response = client.post("/api/v1/requests", headers=headers, json={"client_id": client_id, "vehicle_id": vehicle_id, "channel": "whatsapp", "reason": "Revisar frenos del vehículo"})
    assert response.status_code == 201, response.text
    return response.json()["id"], client_id, vehicle_id


def test_registration_login_permissions_and_revocation(api):
    client, _ = api
    assert client.post("/api/v1/auth/register", json={"username": "intruso", "full_name": "Intruso", "password": "LongPassword123!", "role": "gerente"}).status_code == 401
    manager = login(client)
    created = client.post("/api/v1/auth/register", headers=manager, json={"username": "mecanico", "full_name": "Técnico Uno", "password": "LongPassword123!", "role": "tecnico"})
    assert created.status_code == 201
    tech = login(client, "mecanico", "LongPassword123!")
    assert client.get("/api/v1/auth/users", headers=tech).status_code == 403
    assert client.post("/api/v1/auth/register", headers=tech, json={"username": "otro", "full_name": "Técnico Dos", "password": "LongPassword123!", "role": "gerente"}).status_code == 403
    assert client.post("/api/v1/auth/logout", headers=tech).status_code == 204
    assert client.get("/api/v1/auth/me", headers=tech).status_code == 401
    tech = login(client, "mecanico", "LongPassword123!")
    assert client.patch(f"/api/v1/auth/users/{created.json()['id']}/active?active=false", headers=manager).status_code == 200
    assert client.get("/api/v1/auth/me", headers=tech).status_code == 401


def test_p01_links_client_vehicle_and_request(api):
    client, _ = api
    headers = login(client)
    request_id, client_id, vehicle_id = request_for(client, headers)
    assert client.get(f"/api/v1/requests/{request_id}", headers=headers).json()["vehicle_id"] == vehicle_id
    assert client.get(f"/api/v1/clients/{client_id}/vehicles", headers=headers).json()[0]["plate"] == "ABC123"
    duplicate = client.post("/api/v1/vehicles", headers=headers, json={"client_id": client_id, "plate": "abc123"})
    assert duplicate.status_code == 409
    other = client.post("/api/v1/clients", headers=headers, json={"full_name": "Otro Cliente", "phone": "988777666"}).json()["id"]
    invalid = client.post("/api/v1/requests", headers=headers, json={"client_id": other, "vehicle_id": vehicle_id, "channel": "llamada", "reason": "Diagnóstico general"})
    assert invalid.status_code == 422


def test_p02_capacity_transition_and_history(api):
    client, engine = api
    headers = login(client)
    first, _, _ = request_for(client, headers)
    second, _, _ = request_for(client, headers, plate="XYZ789")
    slot = future_slot()
    created = client.post("/api/v1/appointments", headers=headers, json={"request_id": first, "starts_at": slot})
    assert created.status_code == 201, created.text
    appointment_id = created.json()["id"]
    assert created.json()["starts_at"].endswith("+00:00")
    full = client.post("/api/v1/appointments", headers=headers, json={"request_id": second, "starts_at": slot})
    assert full.status_code == 409
    assert client.post(f"/api/v1/appointments/{appointment_id}/attend", headers=headers).status_code == 409
    assert client.post(f"/api/v1/appointments/{appointment_id}/confirm", headers=headers).json()["status"] == "confirmada"
    assert client.post(f"/api/v1/appointments/{appointment_id}/cancel", headers=headers).json()["status"] == "cancelada"
    assert client.post("/api/v1/appointments", headers=headers, json={"request_id": second, "starts_at": slot}).status_code == 201
    moved = client.post(f"/api/v1/appointments/{appointment_id}/reschedule", headers=headers, json={"starts_at": future_slot(hour=10)})
    assert moved.status_code == 200, moved.text
    history = client.get(f"/api/v1/appointments/{appointment_id}/history", headers=headers).json()
    assert [event["action"] for event in history] == ["programar", "confirm", "cancel", "reprogramar"]
    with Session(engine) as db:
        assert db.query(AppointmentSlot).count() == 2


def test_invalid_date_and_direct_reception(api):
    client, _ = api
    headers = login(client)
    request_id, _, _ = request_for(client, headers)
    assert client.post("/api/v1/appointments", headers=headers, json={"request_id": request_id, "starts_at": "2030-05-10T09:00:00"}).status_code == 422
    assert client.post(f"/api/v1/requests/{request_id}/direct-reception", headers=headers).json()["status"] == "recepcion_directa"
    assert client.post("/api/v1/appointments", headers=headers, json={"request_id": request_id, "starts_at": future_slot()}).status_code == 409
