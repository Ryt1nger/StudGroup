"""Canonical item snapshots are the audit contract for every resolver action."""

import json


def item_snapshot(item):
    return json.dumps(
        {
            "id": str(item.id),
            "group_id": str(item.group_id),
            "destination": item.destination,
            "semantic_kind": item.semantic_kind,
            "subject": item.subject,
            "title": item.title,
            "description": item.description,
            "deadline_at": item.deadline_at.isoformat() if item.deadline_at else None,
            "deadline_date_only": item.deadline_date_only,
            "deadline_basis": item.deadline_basis,
            "deadline_reason": item.deadline_reason,
            "timezone_confirmed": item.timezone_confirmed,
            "locked_fields": json.loads(item.locked_fields or "[]"),
            "audience_scope": item.audience_scope,
            "audience_label": item.audience_label,
            "state": item.state,
            "attention_reasons": json.loads(item.attention_reasons),
            "manual_lock": item.manual_lock,
            "revision": item.revision,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
