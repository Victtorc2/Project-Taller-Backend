from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.clients import get_client, get_vehicle, staff
from app.core.audit import record
from app.db import get_db
from app.models import RequestStatus, ServiceRequest
from app.schemas import RequestCreate, RequestOut

router = APIRouter(prefix="/requests", tags=["Solicitudes P01"])


@router.post("", response_model=RequestOut, status_code=201)
def create_request(data: RequestCreate, actor=Depends(staff), db: Session = Depends(get_db)):
    get_client(data.client_id, actor, db)
    vehicle = get_vehicle(data.vehicle_id, actor, db)
    if vehicle.client_id != data.client_id:
        raise HTTPException(422, "El vehículo no pertenece al cliente")
    item = ServiceRequest(**data.model_dump(), created_by=actor.id)
    db.add(item)
    db.flush()
    record(db, actor, "crear", "solicitud", item.id)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[RequestOut])
def list_requests(status: RequestStatus | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), _=Depends(staff), db: Session = Depends(get_db)):
    query = db.query(ServiceRequest)
    if status:
        query = query.filter(ServiceRequest.status == status)
    return query.order_by(ServiceRequest.id.desc()).offset(offset).limit(limit).all()


@router.get("/{request_id}", response_model=RequestOut)
def get_request(request_id: int, _=Depends(staff), db: Session = Depends(get_db)):
    item = db.get(ServiceRequest, request_id)
    if not item:
        raise HTTPException(404, "Solicitud no encontrada")
    return item


@router.post("/{request_id}/direct-reception", response_model=RequestOut)
def direct_reception(request_id: int, actor=Depends(staff), db: Session = Depends(get_db)):
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    query = db.query(ServiceRequest).filter(ServiceRequest.id == request_id)
    if db.bind.dialect.name != "sqlite":
        query = query.with_for_update()
    item = query.first()
    if not item:
        raise HTTPException(404, "Solicitud no encontrada")
    if item.status != RequestStatus.PENDIENTE:
        raise HTTPException(409, "Solo se puede derivar una solicitud pendiente")
    item.status = RequestStatus.RECEPCION_DIRECTA
    record(db, actor, "recepcion_directa", "solicitud", item.id)
    db.commit()
    db.refresh(item)
    return item
