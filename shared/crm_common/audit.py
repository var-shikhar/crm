"""Activity logging via a transactional outbox.

`AuditEmitter.emit` writes the event into the service's own database inside the
caller's transaction, so an action and its audit record commit or roll back
together. `AuditShipper` then delivers pending events to the audit service in
the background. Delivery is at-least-once; the audit service de-duplicates on
`event_id`. If the audit service is down, events queue up and nothing is lost.
"""

import json
import logging
import threading
import uuid
from typing import Callable

import httpx
from fastapi import Request
from sqlalchemy import JSON, Integer, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .authz import Principal
from .db import UTCDateTime, utcnow
from .web import request_meta

log = logging.getLogger("audit")


def define_outbox(base):
    """Create the `audit_outbox` table on a service's declarative base."""

    class AuditOutbox(base):
        __tablename__ = "audit_outbox"

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        payload: Mapped[dict] = mapped_column(JSON)
        created_at = mapped_column(UTCDateTime, default=utcnow)
        sent_at = mapped_column(UTCDateTime, nullable=True, index=True)

    return AuditOutbox


class AuditEmitter:
    def __init__(self, service: str, outbox_model):
        self.service = service
        self._outbox = outbox_model

    def emit(
        self,
        db: Session,
        action: str,
        *,
        request: Request | None = None,
        actor: Principal | None = None,
        actor_email: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        outcome: str = "success",
        details: dict | None = None,
    ) -> dict:
        """Queue an audit event. The caller owns the commit."""
        meta = request_meta(request)
        event = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": utcnow().isoformat(),
            "service": self.service,
            "action": action,
            "outcome": outcome,
            "actor_id": actor.id if actor else None,
            "actor_type": actor.type if actor else "anonymous",
            "actor_email": actor.email if actor else actor_email,
            "actor_name": actor.name if actor else None,
            "actor_roles": list(actor.roles) if actor else [],
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id is not None else None,
            "ip_address": meta.get("ip_address"),
            "user_agent": meta.get("user_agent"),
            "request_id": meta.get("request_id"),
            # Round-trip through JSON so dates, UUIDs and Decimals are storable.
            "details": json.loads(json.dumps(details or {}, default=str)),
        }
        db.add(self._outbox(payload=event))
        log.info("audit %s", action, extra={"audit": event})
        return event


class AuditShipper(threading.Thread):
    def __init__(
        self,
        session_factory,
        outbox_model,
        audit_url: str,
        token_provider: Callable[[], str],
        interval: float = 2.0,
        batch_size: int = 100,
    ):
        super().__init__(name="audit-shipper", daemon=True)
        self._session_factory = session_factory
        self._outbox = outbox_model
        self._token_provider = token_provider
        self._interval = interval
        self._batch_size = batch_size
        self._http = httpx.Client(base_url=audit_url, timeout=5.0)
        self._stop_event = threading.Event()
        self._failing = False

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                # Keep draining while there is a backlog.
                while self.drain_once() == self._batch_size and not self._stop_event.is_set():
                    pass
                if self._failing:
                    log.info("audit delivery recovered")
                    self._failing = False
            except Exception as exc:  # delivery must never take the service down
                if not self._failing:
                    log.warning("audit delivery failing, events are queued: %s", exc)
                    self._failing = True
            self._stop_event.wait(self._interval)

    def drain_once(self) -> int:
        with self._session_factory() as db:
            rows = db.scalars(
                select(self._outbox)
                .where(self._outbox.sent_at.is_(None))
                .order_by(self._outbox.id)
                .limit(self._batch_size)
            ).all()
            if not rows:
                return 0
            resp = self._http.post(
                "/api/v1/events/batch",
                json={"events": [row.payload for row in rows]},
                headers={"Authorization": f"Bearer {self._token_provider()}"},
            )
            resp.raise_for_status()
            now = utcnow()
            for row in rows:
                row.sent_at = now
            db.commit()
            return len(rows)
