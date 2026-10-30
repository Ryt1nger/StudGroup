"""Authorized complex-image benchmark, with the ORIGINAL USD-1 journal and frozen gold.

No database imports/writes. Unknown paid outcomes stop all further requests. Successful passes
are checkpointed. Existing old synthetic evidence is preserved in its original namespaces.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import dotenv_values
from synthetic_vision_check import FIXTURES, Ledger, RecordedProvider, selected_card_images

from studgroup.ai import ProviderFailure
from studgroup.analysis.contracts import AnalysisInput, SourceAttachment, SourceMessage
from studgroup.analysis.extraction_strategy import EXTRACTION_PROMPT_VERSION
from studgroup.analysis.image_contracts import ImageEvidence, VisualReading
from studgroup.analysis.image_evidence import add_image_facts
from studgroup.analysis.image_reading import PASS_A_VERSION, PASS_B_VERSION, read_pass
from studgroup.analysis.shadow import ShadowPassFailure, run_shadow
from studgroup.image_display_coverage import build_coverage, coverage_metadata
from studgroup.photo_dedupe import visual_fingerprint


def snapshot():
    manifest = json.loads((FIXTURES / "image-complex-manifest.json").read_text())
    claimed = manifest.pop("snapshot_sha256")
    assert (
        hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        == claimed
    )
    for item in manifest["images"]:
        assert hashlib.sha256((FIXTURES / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    manifest["snapshot_sha256"] = claimed
    return manifest


class ReplayRecordedProvider(RecordedProvider):
    offline = False

    async def _complete(self, payload):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        for index in range(len(self.ledger.data["calls"]) - 1, -1, -1):
            call = self.ledger.data["calls"][index]
            if (
                call["state"] == "completed"
                and call.get("request_sha256") == fingerprint
                and call.get("label") == self.label
                and call.get("response")
            ):
                self.ledger.data.setdefault("complex_local_replays", []).append(
                    {
                        "call_index": index,
                        "reason": "same raw response, current local parser; no HTTP",
                    }
                )
                self.ledger.save()
                return {"choices": call["response"], "usage": call["usage"], "model": call["model"]}
        if self.offline:
            raise ProviderFailure(
                "offline_provider_response_missing", False, reservation_releasable=True
            )
        return await super()._complete(payload)


async def main(path, *, offline=False, validation_revision="local-2", retry_known=False):
    manifest = snapshot()
    if not path.is_file():
        raise ValueError("original_release_journal_required")
    ledger = Ledger(path)
    if "authorized_unknown_baseline" not in ledger.data:
        if len(ledger.data["calls"]) != 278 or ledger.used() != Decimal(".181507560"):
            raise ValueError("original_release_baseline_mismatch")
        # Old unknown reservations remain fully charged; the owner explicitly authorized this
        # next phase with the reduced total allowance. Never extend this boundary automatically.
        ledger.data["authorized_unknown_baseline"] = 278
    versions = [PASS_A_VERSION, PASS_B_VERSION, EXTRACTION_PROMPT_VERSION]
    run = ledger.data.setdefault("complex_runs", {}).setdefault(
        "/".join(versions),
        {
            "snapshot_sha256": manifest["snapshot_sha256"],
            "versions": [PASS_A_VERSION, PASS_B_VERSION, EXTRACTION_PROMPT_VERSION],
            "images": {},
            "scenarios": {},
        },
    )
    if run["snapshot_sha256"] != manifest["snapshot_sha256"] or run["versions"] != [
        PASS_A_VERSION,
        PASS_B_VERSION,
        EXTRACTION_PROMPT_VERSION,
    ]:
        raise ValueError("frozen_benchmark_changed")
    if offline:
        run = ledger.data.setdefault("complex_revalidations", {}).setdefault(
            "/".join(versions) + "/" + validation_revision,
            {
                "snapshot_sha256": manifest["snapshot_sha256"],
                "versions": versions,
                "images": run["images"],
                "scenarios": {},
                "mode": "offline; no HTTP permitted",
            },
        )
    ledger.save()
    key = os.environ.get("DEEPSEEK_API_KEY") or dotenv_values(
        os.environ.get("STUDGROUP_SYNTHETIC_ENV_FILE", ".env")
    ).get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("provider_not_configured")
    provider = ReplayRecordedProvider(key, ledger, connect_timeout=8)
    provider.offline = offline
    stamp = datetime(2026, 10, 29, 6, tzinfo=UTC)
    images = {item["id"]: item for item in manifest["images"]}
    for scenario in manifest["scenarios"]:
        ident = scenario["id"]
        if ident in run["scenarios"]:
            previous = run["scenarios"][ident]
            attempts = run.setdefault("scenario_attempts", {}).setdefault(ident, [])
            calls = [
                call
                for call in ledger.data["calls"]
                if call.get("label") == f"complex/{ident}/cplus"
            ]
            known_connect = (
                calls and calls[-1]["state"] == "failed" and calls[-1].get("cost_upper_usd") == "0"
            )
            if not (retry_known and previous.get("error") and known_connect and len(attempts) < 2):
                continue
            attempts.append(previous)
            del run["scenarios"][ident]
            ledger.save()
        messages, evidence, metadata, profiles = [], [], [], {}
        context = {
            "timezone": "Europe/Moscow",
            "schedule": [],
            "existing_items": [],
            "messages": [
                {
                    "id": index + 1,
                    "text": "",
                    "revision": 1,
                    "reply_to": None,
                    "media_group_ids": [ident],
                    "sent_at": stamp.isoformat(),
                }
                for index in range(len(scenario["images"]))
            ],
        }
        for index, text in enumerate(scenario["messages"]):
            context["messages"].append(
                {
                    "id": 100 + index,
                    "text": text,
                    "revision": 1,
                    "reply_to": 1,
                    "media_group_ids": [],
                    "sent_at": stamp.isoformat(),
                }
            )
            messages.append(
                SourceMessage(
                    telegram_message_id=100 + index,
                    revision=1,
                    message_date=stamp,
                    text=text,
                    reply_to_message_id=1,
                )
            )
        for index, image_id in enumerate(scenario["images"]):
            item = images[image_id]
            pixels = (FIXTURES / item["file"]).read_bytes()
            attachment = uuid5(NAMESPACE_URL, "studgroup-complex/" + image_id)
            messages.append(
                SourceMessage(
                    telegram_message_id=index + 1,
                    revision=1,
                    message_date=stamp,
                    text="",
                    attachments=[
                        SourceAttachment(
                            attachment_id=attachment,
                            kind="photo",
                            mime_type="image/jpeg",
                            processing_state="ready",
                            media_group_id=ident,
                        )
                    ],
                )
            )
            record = run["images"].setdefault(image_id, {})
            context["image_source_message_id"] = index + 1
            for number, name in [(1, "a"), (2, "b")]:
                if name in record:
                    continue
                provider.label = f"complex/{image_id}/{name}"
                first = (
                    VisualReading.model_validate_json(json.dumps(record["a"]))
                    if number == 2
                    else None
                )
                reading, _ = await read_pass(
                    provider,
                    pixels,
                    "image/jpeg",
                    number,
                    context=context if number == 2 else {},
                    first=first,
                )
                record[name] = reading.model_dump(mode="json")
                ledger.save()
            reading = VisualReading.model_validate_json(json.dumps(record["b"]))
            ready = reading.complete
            evidence.append(
                ImageEvidence(
                    attachment_id=attachment,
                    telegram_message_id=index + 1,
                    source_revision=1,
                    context_sha256="0" * 64,
                    state="ready" if ready else "failed",
                    reading=reading if ready else None,
                    error=None if ready else "incomplete_reading",
                )
            )
            profiles[str(attachment)] = build_coverage(pixels, reading) if ready else None
            metadata.append(
                {
                    "attachment_id": str(attachment),
                    "image_id": image_id,
                    "telegram_message_id": index + 1,
                    "image_analysis_state": "ready" if ready else "failed",
                    "semantic_types": reading.semantic_types,
                    "content_signature": hashlib.sha256(
                        json.dumps(record["b"], sort_keys=True).encode()
                    ).hexdigest(),
                    "pixel_sha256": item["sha256"],
                    "visual_hash": visual_fingerprint(pixels),
                    "width": item["width"],
                    "height": item["height"],
                }
            )
        cover = coverage_metadata(profiles)
        for item in metadata:
            item.update(cover.get(item["attachment_id"], {}))
        data = AnalysisInput(
            group_id=uuid5(NAMESPACE_URL, "studgroup-complex/" + ident),
            group_timezone="Europe/Moscow",
            source_revision=1,
            messages=add_image_facts(messages, evidence),
            target_message_ids=[message.telegram_message_id for message in messages],
            image_evidence=evidence,
        )
        provider.label = f"complex/{ident}/cplus"
        try:
            result = await run_shadow(provider, data, [], repair_enabled=False)
            output = {
                "context": result.context.model_dump(mode="json"),
                "validation": result.validation.model_dump(mode="json"),
            }
        except (ShadowPassFailure, ProviderFailure) as error:
            output = {"error": error.code}
        output["selected_by_card"] = selected_card_images(output.get("validation", {}), metadata)
        output["display_metadata"] = metadata
        run["scenarios"][ident] = output
        ledger.save()
    print(json.dumps({"finished": True, "total_conservative_usd": str(ledger.used())}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--validation-revision", default="local-2")
    parser.add_argument(
        "--retry-known",
        action="store_true",
        help="Bounded retry of proven zero-charge pre-connection errors only",
    )
    args = parser.parse_args()
    with args.journal.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(
            main(
                args.journal.resolve(),
                offline=args.offline,
                validation_revision=args.validation_revision,
                retry_known=args.retry_known,
            )
        )
