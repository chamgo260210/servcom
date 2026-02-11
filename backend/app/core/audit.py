from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models


def record_log(
    db: Session,
    *,
    actor_id: str | None,
    action: str,
    target_user_id: str | None = None,
    request_id: str | None = None,
    details: dict | None = None,
) -> models.AuditLog:
    log = models.AuditLog(
        actor_user_id=actor_id,
        action_type=action,
        target_user_id=target_user_id,
        request_id=request_id,
        details=details,
    )
    db.add(log)
    return log
