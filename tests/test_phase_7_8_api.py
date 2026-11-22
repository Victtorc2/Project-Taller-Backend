from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import User
from tests.test_closing_continuity import month_before_today, seed_ledger
from tests.test_phases_4_6 import api, auth, send


def test_reports_close_permissions_and_contingency_apis(api):
    client, engine = api
    assert client.get("/health/db").json() == {"status": "ok"}
    start, end = month_before_today()
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        request = seed_ledger(db, db.get(User, 1), start)
        request_id = request.id
    manager = auth(client, "gerente", "ManagerPassword123!")
    tech = auth(client, "tecnico", "TechnicianPass123!")

    report = send(client, "GET", f"/reports?frequency=mensual&start_date={start}&end_date={end}", manager)
    assert report["sales_authorized"] == "300.00"
    assert report["partial_result"] == "270.00"
    assert client.get(f"/api/v1/reports?frequency=mensual&start_date={start}&end_date={end}", headers=tech).status_code == 403
    period = send(client, "POST", "/periods", manager, {"frequency": "mensual", "start_date": str(start), "end_date": str(end)}, 201)
    issue = send(client, "POST", f"/periods/{period['id']}/issues", manager, {"category": "cobros", "description": "Falta cotejar un comprobante", "evidence": "Registro físico pendiente"}, 201)
    assert client.post(f"/api/v1/periods/{period['id']}/confirm", headers=manager).status_code == 409
    send(client, "POST", f"/periods/{period['id']}/issues/{issue['id']}/resolve", manager, {"resolution": "Pago cotejado con el registro digital", "evidence": "Comprobante físico revisado"})
    assert client.post(f"/api/v1/periods/{period['id']}/confirm", headers=tech).status_code == 403
    closed = send(client, "POST", f"/periods/{period['id']}/confirm", manager)
    assert closed["status"] == "cerrado" and closed["report"] == report
    assert send(client, "GET", f"/periods/{period['id']}", manager)["report"] == report
    assert client.post(f"/api/v1/periods/{period['id']}/issues", headers=manager, json={"category": "otros", "description": "Otra diferencia", "evidence": "Registro físico"}).status_code == 409
    assert send(client, "POST", f"/periods/{period['id']}/reopen", manager, {"reason": "Nueva revisión de gerencia"})["status"] == "borrador"

    incident = send(client, "POST", "/incidents", tech, {"process_code": "P03", "severity": "alta", "description": "Interrupción del sistema web", "impact": "Se usaron órdenes físicas"}, 201)
    assert client.post(f"/api/v1/incidents/{incident['id']}/resolve", headers=tech, json={"action_taken": "Sistema restablecido"}).status_code == 403
    assert send(client, "POST", f"/incidents/{incident['id']}/resolve", manager, {"action_taken": "Sistema restablecido y registros revisados"})["status"] == "resuelto"
    happened = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    record = send(client, "POST", "/manual-records", tech, {"temp_code": "PAPEL-12345", "process_code": "P01", "description": "Solicitud atendida en papel durante caída", "occurred_at": happened}, 201)
    assert record["reconciled_at"] is None
    assert client.post("/api/v1/manual-records/PAPEL-12345/reconcile", headers=tech, json={"entity": "solicitud", "entity_id": request_id, "evidence": "Ficha cotejada"}).status_code == 403
    linked = send(client, "POST", "/manual-records/PAPEL-12345/reconcile", manager, {"entity": "solicitud", "entity_id": request_id, "evidence": "Ficha física cotejada con solicitud digital"})
    assert linked["reconciled_id"] == request_id
    assert client.post("/api/v1/manual-records/PAPEL-12345/reconcile", headers=manager, json={"entity": "solicitud", "entity_id": request_id, "evidence": "Segundo intento de conciliación"}).status_code == 409
    assert client.post("/api/v1/training/evaluations", headers=tech, json={"user_id": 2, "tasks_correct": 8, "tasks_total": 10}).status_code == 403
    evaluation = send(client, "POST", "/training/evaluations", manager, {"user_id": 2, "tasks_correct": 8, "tasks_total": 10, "notes": "Evaluación práctica de OT"}, 201)
    assert evaluation["meets_target"] is True
    assert send(client, "GET", "/training/status", manager)[1]["evaluated"] is True
    metric = send(client, "GET", "/metrics/repair-updates", manager)
    assert metric["active_repairs"] == 0 and metric["percentage"] is None
