import json

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def audit(db: Session, *, user_id: str, action: str, detail: dict | None = None) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        )
    )
