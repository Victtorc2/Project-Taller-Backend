from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.audit import record
from app.core.config import get_business_tz, get_settings
from app.clients import staff
from app.db import get_db
from app.models import Appointment, AppointmentEvent, AppointmentSlot, AppointmentStatus, RequestStatus, ServiceRequest, utcnow
from app.schemas import AppointmentCreate, AppointmentOut, AppointmentReschedule, AvailabilityOut

router = APIRouter(prefix="/appointments", tags=["Citas P02"])


def checked_start(value: datetime) -> datetime:
    settings = get_settings()
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "La fecha debe incluir zona horaria, por ejemplo -05:00")
    local = value.astimezone(get_business_tz())
    opening = local.replace(hour=settings.open_hour, minute=0, second=0, microsecond=0)
    closing = local.replace(hour=settings.close_hour, minute=0, second=0, microsecond=0)
    elapsed = (local - opening).total_seconds()
    if (local.utcoffset() != value.utcoffset() or local.weekday() == 6 or
        elapsed < 0 or elapsed % (settings.appointment_minutes * 60) != 0 or
        local + timedelta(minutes=settings.appointment_minutes) > closing):
        raise HTTPException(422, "Horario fuera de las franjas disponibles del taller")
    if value.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(422, "La cita debe estar en el futuro")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def reserve(db: Session, appointment_id: int, starts_at: datetime) -> None:
    settings = get_settings()
    occupied = {row[0] for row in db.query(AppointmentSlot.slot_number).filter(AppointmentSlot.starts_at == starts_at).all()}
    available = next((number for number in range(1, settings.appointment_capacity + 1) if number not in occupied), None)
    if available is None:
        raise HTTPException(409, "Horario sin cupos disponibles")
    db.add(AppointmentSlot(appointment_id=appointment_id, starts_at=starts_at, slot_number=available))
    db.flush()


def lock_slots_for_sqlite(db: Session) -> None:
    # Serializa lectura de cupos e inserción en SQLite; PostgreSQL protege la
    # misma invariante con la restricción UNIQUE (starts_at, slot_number).
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def appointment_or_404(db: Session, appointment_id: int) -> Appointment:
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Cita no encontrada")
    return appointment


def locked_appointment(db: Session, appointment_id: int) -> Appointment:
    query = db.query(Appointment).filter(Appointment.id == appointment_id)
    if db.bind.dialect.name != "sqlite":
        query = query.with_for_update()
    appointment = query.first()
    if not appointment:
        raise HTTPException(404, "Cita no encontrada")
    return appointment


@router.get("/availability", response_model=list[AvailabilityOut])
def availability(day: date, _=Depends(staff), db: Session = Depends(get_db)):
    settings = get_settings()
    tz = get_business_tz()
    opening = datetime(day.year, day.month, day.day, settings.open_hour, tzinfo=tz)
    closing = opening.replace(hour=settings.close_hour)
    if day.weekday() == 6:
        return []
    result = []
    current = opening
    while current + timedelta(minutes=settings.appointment_minutes) <= closing:
        utc_start = current.astimezone(timezone.utc).replace(tzinfo=None)
        count = db.query(func.count(AppointmentSlot.id)).filter(AppointmentSlot.starts_at == utc_start).scalar()
        if current.astimezone(timezone.utc) > datetime.now(timezone.utc):
            result.append(AvailabilityOut(starts_at=current, available=max(0, settings.appointment_capacity - count)))
        current += timedelta(minutes=settings.appointment_minutes)
    return result


@router.post("", response_model=AppointmentOut, status_code=201)
def create_appointment(data: AppointmentCreate, actor=Depends(staff), db: Session = Depends(get_db)):
    starts_at = checked_start(data.starts_at)
    try:
        lock_slots_for_sqlite(db)
        request_query = db.query(ServiceRequest).filter(ServiceRequest.id == data.request_id)
        if db.bind.dialect.name != "sqlite":
            request_query = request_query.with_for_update()
        request = request_query.first()
        if not request:
            raise HTTPException(404, "Solicitud no encontrada")
        if request.status != RequestStatus.PENDIENTE:
            raise HTTPException(409, "La solicitud ya fue derivada")
        appointment = Appointment(request_id=request.id, starts_at=starts_at, created_by=actor.id)
        db.add(appointment)
        db.flush()
        reserve(db, appointment.id, starts_at)
        request.status = RequestStatus.CITA
        db.add(AppointmentEvent(appointment_id=appointment.id, actor_id=actor.id, action="programar", new_starts_at=starts_at))
        record(db, actor, "programar", "cita", appointment.id)
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "Cupo ocupado o solicitud ya programada; actualice disponibilidad") from None
    db.refresh(appointment)
    return appointment


@router.get("", response_model=list[AppointmentOut])
def list_appointments(day: date | None = None, status: AppointmentStatus | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), _=Depends(staff), db: Session = Depends(get_db)):
    query = db.query(Appointment)
    if day:
        local_start = datetime(day.year, day.month, day.day, tzinfo=get_business_tz())
        utc_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
        utc_end = (local_start + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
        query = query.filter(Appointment.starts_at >= utc_start, Appointment.starts_at < utc_end)
    if status:
        query = query.filter(Appointment.status == status)
    return query.order_by(Appointment.starts_at.desc()).offset(offset).limit(limit).all()


@router.get("/{appointment_id}", response_model=AppointmentOut)
def get_appointment(appointment_id: int, _=Depends(staff), db: Session = Depends(get_db)):
    return appointment_or_404(db, appointment_id)


@router.get("/{appointment_id}/history")
def history(appointment_id: int, _=Depends(staff), db: Session = Depends(get_db)):
    appointment_or_404(db, appointment_id)
    events = db.query(AppointmentEvent).filter(AppointmentEvent.appointment_id == appointment_id).order_by(AppointmentEvent.id).all()
    return [{"action": e.action, "actor_id": e.actor_id, "old_starts_at": e.old_starts_at.replace(tzinfo=timezone.utc) if e.old_starts_at else None, "new_starts_at": e.new_starts_at.replace(tzinfo=timezone.utc) if e.new_starts_at else None, "created_at": e.created_at.replace(tzinfo=timezone.utc)} for e in events]


@router.post("/{appointment_id}/reschedule", response_model=AppointmentOut)
def reschedule(appointment_id: int, data: AppointmentReschedule, actor=Depends(staff), db: Session = Depends(get_db)):
    starts_at = checked_start(data.starts_at)
    try:
        lock_slots_for_sqlite(db)
        appointment = locked_appointment(db, appointment_id)
        if appointment.status in (AppointmentStatus.ATENDIDA, AppointmentStatus.NO_ASISTIO):
            raise HTTPException(409, "No se puede reprogramar una cita finalizada")
        if appointment.starts_at == starts_at and appointment.status != AppointmentStatus.CANCELADA:
            raise HTTPException(409, "Seleccione otro horario")
        old = appointment.starts_at
        slot = db.query(AppointmentSlot).filter(AppointmentSlot.appointment_id == appointment.id).first()
        if slot:
            db.delete(slot)
            db.flush()
        reserve(db, appointment.id, starts_at)
        appointment.starts_at = starts_at
        appointment.status = AppointmentStatus.PROGRAMADA
        appointment.confirmed_at = None
        db.add(AppointmentEvent(appointment_id=appointment.id, actor_id=actor.id, action="reprogramar", old_starts_at=old, new_starts_at=starts_at))
        record(db, actor, "reprogramar", "cita", appointment.id)
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "Cupo ocupado; actualice disponibilidad") from None
    db.refresh(appointment)
    return appointment


TRANSITIONS = {
    "confirm": (AppointmentStatus.PROGRAMADA, AppointmentStatus.CONFIRMADA),
    "attend": (AppointmentStatus.CONFIRMADA, AppointmentStatus.ATENDIDA),
    "no-show": (AppointmentStatus.CONFIRMADA, AppointmentStatus.NO_ASISTIO),
}


def change_status(appointment_id: int, action: str, actor, db: Session) -> Appointment:
    lock_slots_for_sqlite(db)
    appointment = locked_appointment(db, appointment_id)
    if action == "cancel":
        if appointment.status not in (AppointmentStatus.PROGRAMADA, AppointmentStatus.CONFIRMADA):
            raise HTTPException(409, "La cita no puede cancelarse en su estado actual")
        appointment.status = AppointmentStatus.CANCELADA
        slot = db.query(AppointmentSlot).filter(AppointmentSlot.appointment_id == appointment.id).first()
        if slot:
            db.delete(slot)
    else:
        source, target = TRANSITIONS[action]
        if appointment.status != source:
            raise HTTPException(409, f"La cita debe estar en estado {source.value}")
        appointment.status = target
        if action == "confirm":
            appointment.confirmed_at = utcnow()
    db.add(AppointmentEvent(appointment_id=appointment.id, actor_id=actor.id, action=action, old_starts_at=appointment.starts_at))
    record(db, actor, action, "cita", appointment.id)
    db.commit()
    db.refresh(appointment)
    return appointment


@router.post("/{appointment_id}/confirm", response_model=AppointmentOut)
def confirm(appointment_id: int, actor=Depends(staff), db: Session = Depends(get_db)):
    return change_status(appointment_id, "confirm", actor, db)


@router.post("/{appointment_id}/attend", response_model=AppointmentOut)
def attend(appointment_id: int, actor=Depends(staff), db: Session = Depends(get_db)):
    return change_status(appointment_id, "attend", actor, db)


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel(appointment_id: int, actor=Depends(staff), db: Session = Depends(get_db)):
    return change_status(appointment_id, "cancel", actor, db)


@router.post("/{appointment_id}/no-show", response_model=AppointmentOut)
def no_show(appointment_id: int, actor=Depends(staff), db: Session = Depends(get_db)):
    return change_status(appointment_id, "no-show", actor, db)
