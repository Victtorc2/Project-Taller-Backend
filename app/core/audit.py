from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def record(db: Session, actor: User, action: str, entity: str, entity_id: int, detail: str | None = None) -> None:
    db.add(AuditEvent(actor_id=actor.id, action=action, entity=entity, entity_id=entity_id, detail=detail))
