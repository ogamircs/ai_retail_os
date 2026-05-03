"""Mautic adapter — basic-auth REST, segment / contact / campaign sync + outbound drafts."""

from __future__ import annotations

import os
import re
from base64 import b64encode
from typing import Any
from urllib.parse import quote, urlencode

from app.integrations import store
from app.integrations.base import IntegrationAdapter, IntegrationDefinition, IntegrationResult
from app.integrations.http import (
    JsonHttpClient,
    _coerce_id,
)
from app.spine.db import conn

_MAUTIC_MARKER_RE = re.compile(r"\[retail-os:([a-zA-Z0-9_\-]+)\]")


class MauticAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="mautic",
        display_name="Mautic",
        domain="Marketing Automation",
        docs_url="https://devdocs.mautic.org/en/7.0/",
        env_keys=("MAUTIC_BASE_URL",),
        notes="Segments, email/campaign drafts, and webhook-based campaign telemetry.",
    )

    def configured(self) -> bool:
        # Beyond the base-class env-key check, require either the pre-baked
        # MAUTIC_BASIC_TOKEN or the user/password pair the bootstrap script
        # prints. Without auth there is nothing to talk to — so don't claim
        # the adapter is configured.
        if not super().configured():
            return False
        if os.getenv("MAUTIC_BASIC_TOKEN", "").strip():
            return True
        return bool(
            os.getenv("MAUTIC_USERNAME", "").strip()
            and os.getenv("MAUTIC_PASSWORD", "").strip()
        )

    def _client(self) -> JsonHttpClient:
        headers: dict[str, str] = {}
        token = os.getenv("MAUTIC_BASIC_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Basic {token}"
        else:
            raw = f"{os.environ['MAUTIC_USERNAME']}:{os.environ['MAUTIC_PASSWORD']}"
            headers["Authorization"] = f"Basic {b64encode(raw.encode()).decode()}"
        return JsonHttpClient(os.environ["MAUTIC_BASE_URL"], headers=headers)

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._client().request("/api/contacts?limit=1")
            return IntegrationResult(status="connected", summary={"contacts_seen": len(data.get("contacts", []))})
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    # ----- live ------------------------------------------------------------

    def _mautic_list(
        self,
        endpoint: str,
        key: str,
        limit: int = 200,
        max_rows: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """GET /api/<endpoint> with pagination, normalised to a flat list.

        Mautic returns rows keyed by id (`{"42": {...}}`) plus a `total`
        field. A single request only returns one page (`limit` rows); we
        walk `start` until we've consumed `total` (or hit a short page
        when `total` isn't returned).

        Returns `(rows, truncated)`. `max_rows=None` (the default and
        what `_live_sync` passes) means no cap — sync is comprehensive
        by design. A caller that does pass `max_rows` and hits it gets
        `truncated=True`, and the live-sync caller surfaces that in the
        result `summary` so it isn't silent.
        """
        out: list[dict[str, Any]] = []
        start = 0
        truncated = False
        while True:
            page_limit = (
                limit if max_rows is None else min(limit, max(1, max_rows - len(out)))
            )
            data = self._client().request(
                f"/api/{endpoint}?limit={page_limit}&start={start}"
            )
            rows = data.get(key) or {}
            if isinstance(rows, dict):
                page = list(rows.values())
            elif isinstance(rows, list):
                page = rows
            else:
                page = []
            if not page:
                break
            out.extend(page)
            # Prefer Mautic's authoritative `total`; fall back to the
            # short-page heuristic when total isn't returned.
            total_raw = data.get("total")
            try:
                total = int(total_raw) if total_raw is not None else None
            except (TypeError, ValueError):
                total = None
            if total is not None and start + len(page) >= total:
                break
            if len(page) < page_limit:
                break
            if max_rows is not None and len(out) >= max_rows:
                # Cap reached but the API said there's more — flag it loudly
                # so the result summary can mark this domain as truncated.
                truncated = total is None or total > len(out)
                break
            start += page_limit
        return out, truncated

    def _live_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        truncated_domains: list[str] = []
        records_read = 0
        records_written = 0

        # Pull every page (no row cap by default — sync is comprehensive). If
        # an explicit cap is ever wired in via env later, the helper marks
        # truncated=True and we surface that in `summary["truncated_domains"]`
        # so callers don't quietly act on an incomplete cache.
        segs, seg_trunc = self._mautic_list("segments", "lists")
        contacts, contact_trunc = self._mautic_list("contacts", "contacts", limit=500)
        campaigns, cmp_trunc = self._mautic_list("campaigns", "campaigns")

        # Segments: alias == seg_id with - replaced by _ (see infra/mautic/seed.py).
        # Recover the substrate segment_id by reversing that mapping so the
        # external_refs row links the Mautic list back to the spine segment.
        for seg in segs:
            external_id = _coerce_id(seg.get("id"))
            if external_id is None:
                continue
            alias = (seg.get("alias") or "").strip()
            local_id = alias.replace("_", "-") if alias else None
            self._cache("Segment", external_id, seg, local_id)
            domains["Segment"] = domains.get("Segment", 0) + 1
            records_written += 1
            records_read += 1
        if seg_trunc:
            truncated_domains.append("Segment")

        # Contacts: local_id is email since that's deterministic across our
        # seeded personas. If a contact has no email we still cache the row
        # but skip the external_ref (no clean local key to anchor it).
        for contact in contacts:
            external_id = _coerce_id(contact.get("id"))
            if external_id is None:
                continue
            fields = (contact.get("fields") or {}).get("core") or {}
            email = (fields.get("email") or {}).get("value") or contact.get("email")
            self._cache("Contact", external_id, contact, email)
            domains["Contact"] = domains.get("Contact", 0) + 1
            records_written += 1
            records_read += 1
        if contact_trunc:
            truncated_domains.append("Contact")

        # Campaigns: recover the substrate campaign_id from the
        # `[retail-os:<id>]` marker our seeder embeds in description.
        # If the marker isn't present (operator-authored campaign) we still
        # cache the row but with no local_id.
        for cmp in campaigns:
            external_id = _coerce_id(cmp.get("id"))
            if external_id is None:
                continue
            description = cmp.get("description") or ""
            match = _MAUTIC_MARKER_RE.search(description)
            local_id = match.group(1) if match else None
            self._cache("Campaign", external_id, cmp, local_id)
            domains["Campaign"] = domains.get("Campaign", 0) + 1
            records_written += 1
            records_read += 1
        if cmp_trunc:
            truncated_domains.append("Campaign")

        summary: dict[str, Any] = {"mode": "connected", "domains": domains}
        if truncated_domains:
            summary["truncated_domains"] = truncated_domains
        # Status downgrades to "partial" so callers can branch on
        # "this snapshot is incomplete" without parsing summary keys.
        status = "partial" if truncated_domains else "success"
        return IntegrationResult(
            status=status,
            records_read=records_read,
            records_written=records_written,
            summary=summary,
        )

    # ----- mock ------------------------------------------------------------

    def _mock_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_written = 0
        with conn() as c:
            segments = c.execute("SELECT * FROM substrate_customer_segments").fetchall()
            campaigns = c.execute("SELECT * FROM substrate_campaigns").fetchall()
        for row in segments:
            payload = dict(row)
            store.cache_record("mautic", "Segment", payload["segment_id"], payload, payload["segment_id"])
            store.record_external_ref("mautic", "Segment", payload["segment_id"], payload["segment_id"], props={"source": "mock"})
            domains["Segment"] = domains.get("Segment", 0) + 1
            records_written += 1
        for row in campaigns:
            payload = dict(row)
            store.cache_record("mautic", "Campaign", payload["campaign_id"], payload, payload["campaign_id"])
            store.record_external_ref("mautic", "Campaign", payload["campaign_id"], payload["campaign_id"], props={"source": "mock"})
            domains["Campaign"] = domains.get("Campaign", 0) + 1
            records_written += 1
        return IntegrationResult(
            status="success",
            records_read=records_written,
            records_written=records_written,
            summary={"mode": "mock", "domains": domains},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: str | None) -> None:
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local_id)
        if local_id:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                str(local_id),
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "Mautic"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        base = os.getenv("MAUTIC_BASE_URL")
        if not base:
            return None
        path = {
            "Segment": "s/segments/view",
            "Contact": "s/contacts/view",
            "Campaign": "s/campaigns/view",
        }.get(domain)
        if not path:
            return None
        return f"{base.rstrip('/')}/{path}/{quote(external_id)}"

    def outbound_domain(self, action_type: str) -> str:
        return {
            "campaign_brief": "Segment Email",
            "campaign_launch": "Campaign",
            "campaign_measurement": "Campaign Report",
        }.get(action_type, super().outbound_domain(action_type))

    # ----- live outbound apply --------------------------------------------

    # Cockpit actions that map onto real Mautic objects. Everything else falls
    # back to the base mock-apply behaviour. Same draft-only stance as ERPNext:
    # the operator pressed "apply", so we record the intent in Mautic; we
    # don't auto-publish.
    LIVE_ACTION_TYPES = {"campaign_launch", "campaign_brief"}

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        from app.integrations import store

        action = store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action

        # Mock-mode and unsupported action_types fall through to the base
        # adapter, which records `applied_mock` / `draft_created` without I/O.
        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return super().apply_outbound(action_id)

        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - exercised only with live Mautic
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": str(exc),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": (
                        "Mautic rejected the apply. The outbox action is left "
                        "in error state — fix the upstream payload and retry."
                    ),
                },
            )

        # No external_id == helper ran cleanly but couldn't actually write
        # anything (e.g. campaign_launch with no campaign_id, or Mautic
        # returned an empty body). Land in `error` instead of lying with
        # `draft_created`.
        if not outcome.get("external_id"):
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": outcome.get("message", "Mautic apply produced no external_id"),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": outcome.get(
                        "message",
                        "Mautic returned no external_id — nothing was written. Check the outbox payload and retry.",
                    ),
                    "details": outcome.get("details", {}),
                },
            )

        return store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get("message", "Draft created in Mautic."),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        payload = action.get("payload") or {}
        title = action.get("title") or "AI Retail OS action"
        if action_type == "campaign_launch":
            return self._mautic_create_campaign(title, payload)
        if action_type == "campaign_brief":
            return self._mautic_ensure_segment(title, payload)
        raise RuntimeError(f"unsupported action_type {action_type!r} for live Mautic apply")

    def _post(self, endpoint: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        data = self._client().request(f"/api/{endpoint}/new", method="POST", payload=body)
        # Mautic POST /new returns {"<key-singular>": {"id": …, …}}.
        # The key passed in is whatever the dispatcher knows is correct
        # ("campaign", "list" for segments — yes, segments-singular is `list`).
        item = data.get(key) or {}
        return item if isinstance(item, dict) else {}

    def _mautic_create_campaign(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """`campaign_launch` → POST /api/campaigns/new (draft).

        We embed the spine `campaign_id` as `[retail-os:<id>]` in the
        description so a follow-up sync round-trips it back to the same
        substrate row (P3 already parses that marker). Drafts only — Mautic
        campaigns need events / triggers before they can run; the operator
        wires those up in the UI.
        """
        campaign_id = payload.get("campaign_id") or ""
        if not campaign_id:
            return {
                "external_id": None,
                "message": "campaign_launch payload has no campaign_id; nothing to apply.",
            }
        marker = f"[retail-os:{campaign_id}]"
        description = "\n".join(
            [
                marker,
                f"Category: {payload.get('category', '')}",
                f"Segment: {payload.get('segment_id', '')}",
                f"Channel: {payload.get('channel', '')}",
                f"Offer: {payload.get('offer', '')}",
                f"Projected lift: {payload.get('projected_lift', 0)}",
                f"Projected ROI: {payload.get('projected_roi', 0)}",
                f"Budget: {payload.get('budget', 0)}",
            ]
        )
        item = self._post(
            "campaigns",
            {
                "name": title,
                "description": description,
                "isPublished": False,
            },
            "campaign",
        )
        external_id = _coerce_id(item.get("id"))
        if external_id is None:
            return {
                "external_id": None,
                "message": "Mautic campaign creation returned no id.",
                "details": item,
            }
        return {
            "external_id": external_id,
            "message": f"Mautic campaign draft created (id={external_id}).",
            "details": {"campaign_id": campaign_id, "marker": marker},
        }

    def _mautic_ensure_segment(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """`campaign_brief` → ensure a Mautic Segment exists for the brief.

        Uses the same alias scheme as the seed (segment_id with `-` → `_`),
        so re-applying the brief lands on the same Mautic list rather than
        creating a duplicate. Looks up by alias first; only POSTs if not
        present.
        """
        segment_id = payload.get("segment_id") or ""
        if not segment_id:
            return {
                "external_id": None,
                "message": "campaign_brief payload has no segment_id; nothing to apply.",
            }
        alias = segment_id.replace("-", "_")
        existing = self._mautic_find_one(
            "segments", "lists", [("alias", "eq", alias)]
        )
        if existing is not None:
            external_id = _coerce_id(existing.get("id"))
            if external_id is not None:
                return {
                    "external_id": external_id,
                    "message": f"Mautic segment already exists (id={external_id}).",
                    "details": {"segment_id": segment_id, "alias": alias, "reused": True},
                }
        item = self._post(
            "segments",
            {
                "name": payload.get("segment_name") or title,
                "alias": alias,
                "publicName": payload.get("segment_name") or title,
                "description": (
                    f"[retail-os:{segment_id}] "
                    f"Category: {payload.get('category', '')} · "
                    f"Channel: {payload.get('channel', '')} · "
                    f"Offer: {payload.get('offer', '')}"
                ),
                "isPublished": True,
                "isGlobal": True,
            },
            "list",
        )
        external_id = _coerce_id(item.get("id"))
        if external_id is None:
            return {
                "external_id": None,
                "message": "Mautic segment creation returned no id.",
                "details": item,
            }
        return {
            "external_id": external_id,
            "message": f"Mautic segment created (id={external_id}).",
            "details": {"segment_id": segment_id, "alias": alias},
        }

    def _mautic_find_one(
        self,
        endpoint: str,
        key: str,
        filters: list[tuple[str, str, str]],
    ) -> dict[str, Any] | None:
        """Mautic column filter for idempotency lookups (mirrors seed.py)."""
        params: list[tuple[str, str]] = [("limit", "1")]
        for i, (col, expr, val) in enumerate(filters):
            params.append((f"where[{i}][col]", col))
            params.append((f"where[{i}][expr]", expr))
            params.append((f"where[{i}][val]", val))
        qs = urlencode(params)
        data = self._client().request(f"/api/{endpoint}?{qs}")
        rows = data.get(key) or {}
        if isinstance(rows, dict) and rows:
            return next(iter(rows.values()))
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

