from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar


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

    # Subclasses override to declare which action_types route through
    # `_dispatch_outbound` against the live system. Anything not in the
    # set falls through to the mock-mode pass-through (`applied_mock` or
    # `draft_created` with no I/O), so adding a new outbound surface is
    # additive — existing action_types stay on the safe path.
    LIVE_ACTION_TYPES: ClassVar[set[str]] = set()

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
        """Apply a previously approved outbox action.

        Routing:
          1. Already terminal (`applied` / `applied_mock` / `draft_created`)
             → return the row unchanged. Idempotent re-clicks of the cockpit
             apply button are safe.
          2. Mock mode (no creds) OR action_type not in `LIVE_ACTION_TYPES`
             → land `applied_mock` / `draft_created` without I/O. New action
             types start here automatically.
          3. Configured + supported action type → call `_dispatch_outbound`.
             Empty `external_id` returned, or any exception, lands the row
             in `error`. Success lands `draft_created` with the dispatch
             outcome's message + details.
        """
        from app.integrations import store

        action = store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action

        # Mock-mode and unsupported action_types fall through to the
        # legacy mock pass-through (no I/O).
        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return self._land_mock(action_id, action)

        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - exercised only with live system
            return self._land_error(
                action_id,
                action,
                error=str(exc),
                message=(
                    f"{self.definition.display_name} rejected the apply. The outbox "
                    "action is left in error state — fix the upstream payload and retry."
                ),
            )

        if not outcome.get("external_id"):
            # Helper ran cleanly but couldn't actually write anything
            # (empty payload, no matching row, vendor returned no id).
            # Land in `error` instead of lying with `draft_created`.
            return self._land_error(
                action_id,
                action,
                error=outcome.get(
                    "message",
                    f"{self.definition.display_name} apply produced no external_id",
                ),
                message=outcome.get(
                    "message",
                    f"{self.definition.display_name} returned no external_id — nothing was written. "
                    "Check the outbox payload and retry.",
                ),
                details=outcome.get("details", {}),
            )

        return store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get(
                    "message",
                    f"Draft created in {self.definition.display_name}.",
                ),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        """Subclass hook. Only invoked when configured + action_type is in
        `LIVE_ACTION_TYPES`. Return `{external_id, message, details}` on
        success; an empty/missing `external_id` lands the row in `error`.
        Raise on hard failures (the base shell catches and lands `error`).
        """
        raise NotImplementedError(
            f"{type(self).__name__} declares LIVE_ACTION_TYPES but didn't override _dispatch_outbound"
        )

    def _land_mock(self, action_id: int, action: dict[str, Any]) -> dict[str, Any]:
        from app.integrations import store

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

    def _land_error(
        self,
        action_id: int,
        action: dict[str, Any],
        error: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.integrations import store

        result: dict[str, Any] = {
            "error": error,
            "system_id": self.definition.system_id,
            "external_domain": action["external_domain"],
            "message": message,
        }
        if details:
            result["details"] = details
        return store.update_outbox_action(action_id, status="error", result=result)
