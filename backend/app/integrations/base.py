from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class IntegrationDefinition:
    system_id: str
    display_name: str
    domain: str
    docs_url: str
    env_keys: tuple[str, ...] = ()
    write_mode: str = "approval_gated"
    notes: str = ""


@dataclass
class IntegrationResult:
    status: str
    records_read: int = 0
    records_written: int = 0
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records_read": self.records_read,
            "records_written": self.records_written,
            "summary": self.summary,
            "error": self.error,
        }


class IntegrationAdapter:
    definition: IntegrationDefinition

    def configured(self) -> bool:
        return all(os.getenv(key, "").strip() for key in self.definition.env_keys)

    def system_row(self) -> dict[str, Any]:
        configured = self.configured()
        return {
            "system_id": self.definition.system_id,
            "display_name": self.definition.display_name,
            "domain": self.definition.domain,
            "enabled": True,
            "configured": configured,
            "mode": "connected" if configured else "mock",
            "docs_url": self.definition.docs_url,
            "metadata": {
                "env_keys": list(self.definition.env_keys),
                "write_mode": self.definition.write_mode,
                "notes": self.definition.notes,
            },
        }

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return IntegrationResult(
                status="mock",
                summary={"message": "No credentials configured; using mock-mode read model."},
            )
        return IntegrationResult(status="connected", summary={"message": "Credentials configured."})

    def sync_inbound(self) -> IntegrationResult:
        raise NotImplementedError

    def propose_outbound(
        self,
        action_queue_id: int | None,
        agent: str,
        action_type: str,
        title: str,
        payload: dict[str, Any],
        external_domain: str | None = None,
    ) -> dict[str, Any]:
        from app.integrations import store

        domain = external_domain or self.outbound_domain(action_type)
        return store.create_outbox_action(
            system_id=self.definition.system_id,
            action_queue_id=action_queue_id,
            agent=agent,
            action_type=action_type,
            title=title,
            external_domain=domain,
            payload=payload,
            configured=self.configured(),
        )

    def outbound_domain(self, action_type: str) -> str:
        return {
            "campaign_brief": "Campaign",
            "campaign_launch": "Campaign",
            "campaign_measurement": "Campaign",
            "promotion": "Promotion",
            "store_transfer": "Transfer",
            "fulfillment_routing": "Fulfillment",
            "po_held": "Purchase Order",
            "po_expedited": "Purchase Order",
            "store_task": "Store Task",
        }.get(action_type, "Action")

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        from app.integrations import store

        action = store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action
        status = "draft_created" if self.configured() else "applied_mock"
        result = {
            "message": (
                "Approved action recorded as an external draft."
                if self.configured()
                else "Approved action applied in mock mode only; no external system was mutated."
            ),
            "system_id": self.definition.system_id,
            "external_domain": action["external_domain"],
            "external_id": action["external_id"],
        }
        return store.update_outbox_action(action_id, status=status, result=result)
