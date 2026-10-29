"""Explicitly authorized synthetic-only paid check; never imports a DB writer.

Run from backend: uv run python tools/synthetic_vision_check.py --out /absolute/results.json
Persistent reservations survive interruption. Unknown-charge failures stop the run. No retries.
"""

import argparse
import asyncio
import copy
import fcntl
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from dotenv import dotenv_values

from studgroup.ai import DeepSeekProvider, ProviderFailure
from studgroup.analysis.contracts import AnalysisInput, SourceAttachment, SourceMessage
from studgroup.analysis.extraction_strategy import EXTRACTION_PROMPT_VERSION
from studgroup.analysis.image_contracts import ImageEvidence, VisualReading
from studgroup.analysis.image_evidence import add_image_facts
from studgroup.analysis.image_reading import PASS_A_VERSION, PASS_B_VERSION, read_pass
from studgroup.analysis.shadow import ShadowPassFailure, run_shadow
from studgroup.photo_dedupe import select_evidence_images, visual_fingerprint

LIMIT = Decimal(1)
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/ai_analysis"


def cost(usage):
    prompt = int(usage["prompt_tokens"])
    hit = int(usage.get("prompt_cache_hit_tokens", 0))
    return (
        Decimal(prompt - hit) * Decimal(".30")
        + Decimal(hit) * Decimal(".006")
        + Decimal(usage["completion_tokens"]) * Decimal("1.20")
    ) / 1000000


def reservation(payload):
    # UTF-8 bytes conservatively bound text tokens; image max 1024 per official docs.
    text_bytes = 4096
    images = 0
    for message in payload["messages"]:
        content = message["content"]
        if isinstance(content, str):
            text_bytes += len(content.encode())
        else:
            for item in content:
                if item["type"] == "image_url":
                    images += 1
                elif item["type"] == "text":
                    text_bytes += len(item["text"].encode())
    return (
        Decimal(text_bytes + images * 1024) * Decimal(".30")
        + Decimal(payload["max_tokens"]) * Decimal("1.20")
    ) / 1000000


def selected_card_images(validation, metadata):
    """Same selection and precise required-image filter used by student_cards."""
    result = []
    for card in validation.get("accepted", []):
        cited = {ref["telegram_message_id"] for ref in card["evidence"]}
        required = {
            path.split(":")[1]
            for ref in card["evidence"]
            for path in ref["fact_paths"]
            if path.startswith("image:")
        }
        selected = select_evidence_images(
            [row for row in metadata if row["telegram_message_id"] in cited], required
        )
        result.append([row["image_id"] for row in selected])
    return result


class Ledger:
    def __init__(self, path, *, retest_ambiguous=False):
        self.retest_ambiguous = retest_ambiguous
        self.path = path
        self.data = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "limit_usd": str(LIMIT),
                "rate_basis": "peak upper bound; .30/.006/1.20 per million",
                "calls": [],
                "images": {},
                "scenarios": {},
            }
        )
        if self.data["limit_usd"] != str(LIMIT):
            previous = Decimal(self.data["limit_usd"])
            used = sum(
                Decimal(row.get("cost_upper_usd", row["reservation_usd"]))
                for row in self.data["calls"]
            )
            if previous > LIMIT and used <= LIMIT:
                self.data.setdefault("limit_history", []).append(
                    {
                        "previous_usd": str(previous),
                        "new_usd": str(LIMIT),
                        "reason": "Owner lowered the entire Core release allowance to USD 1; all old charges retained",
                    }
                )
                self.data["limit_usd"] = str(LIMIT)
                self.save()
            else:
                raise ValueError("ledger_limit_mismatch")

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def used(self):
        return sum(
            (
                Decimal(row.get("cost_upper_usd", row["reservation_usd"]))
                for row in self.data["calls"]
            ),
            Decimal(0),
        ) + sum(
            (Decimal(row["amount_usd"]) for row in self.data.get("allocations", [])), Decimal(0)
        )

    def allocate_pilot(self, *, group_id, since, until):
        """Caller holds the journal file lock. Funding stays reserved if DB provisioning fails."""
        if any(row["state"] == "reserved" for row in self.data["calls"]):
            raise ValueError("unresolved_paid_attempt_stop")
        baseline = self.data.get("authorized_unknown_baseline", 0)
        if any(row["state"] == "unknown" for row in self.data["calls"][baseline:]):
            raise ValueError("new_unknown_paid_attempt_stop")
        scope = {"group_id": str(group_id), "since": since.isoformat(), "until": until.isoformat()}
        allocations = self.data.setdefault("allocations", [])
        if allocations:
            if len(allocations) == 1 and all(
                allocations[0][key] == value for key, value in scope.items()
            ):
                return allocations[0]
            raise ValueError("pilot_funding_cannot_reset")
        if (
            since.tzinfo is None
            or until.tzinfo is None
            or not 0 < (until - since).total_seconds() <= 172800
        ):
            raise ValueError("invalid_pilot_window")
        amount = (LIMIT - self.used()).quantize(Decimal(".00000001"), rounding="ROUND_FLOOR")
        if amount <= 0:
            raise ValueError("total_budget_exhausted")
        scope["amount_usd"] = str(amount)
        scope["receipt"] = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        allocations.append(scope)
        self.save()
        return scope

    def reserve(self, label, payload):
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if any(row["state"] == "reserved" for row in self.data["calls"]):
            raise ValueError("unresolved_paid_attempt_stop")
        baseline = self.data.get("authorized_unknown_baseline")
        if baseline is not None and any(
            row["state"] == "unknown" for row in self.data["calls"][baseline:]
        ):
            raise ValueError("new_unknown_paid_attempt_stop")
        ambiguous = [
            index
            for index, row in enumerate(self.data["calls"])
            if row["state"] == "unknown" and row.get("request_sha256") == request_hash
        ]
        if ambiguous and not self.retest_ambiguous:
            raise ProviderFailure(
                "synthetic_ambiguous_attempt_held", False, reservation_releasable=True
            )
        # Explicit synthetic-only retests keep every uncertain reservation fully charged.
        ceiling = reservation(payload)
        used = self.used()
        if used + ceiling > LIMIT:
            raise ValueError("total_budget_exceeded_before_request")
        row = {
            "label": label,
            "state": "reserved",
            "reservation_usd": str(ceiling),
            "started_at": datetime.now(UTC).isoformat(),
            "requested_model": payload["model"],
            "request_sha256": request_hash,
            "synthetic_retest_of": ambiguous,
        }
        self.data["calls"].append(row)
        self.save()
        return row


class CheckTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        # Only TCP/TLS connection failures retry (max three), before any HTTP request is sent.
        request.extensions.setdefault("timeout", {})["connect"] = 2
        return await super().handle_async_request(request)


class RecordedProvider(DeepSeekProvider):
    def __init__(self, key, ledger):
        super().__init__(
            key,
            model="deepseek-flash",
            base_url="https://api.deepseek.com/v1",
            transport=CheckTransport(retries=3, http2=True),
        )
        self.ledger = ledger
        self.label = ""

    async def _complete(self, payload):
        row = self.ledger.reserve(self.label, payload)
        row["request_sha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        row["transport_connect_retries_max"] = 3
        row["prompt_versions"] = [
            PASS_A_VERSION,
            PASS_B_VERSION,
            EXTRACTION_PROMPT_VERSION,
        ]
        started = time.monotonic()
        self._transport = CheckTransport(retries=3, http2=True)
        try:
            body = await super()._complete(payload)
        except ProviderFailure as error:
            row.update(
                state="failed" if error.usage or error.reservation_releasable else "unknown",
                error=error.code,
                error_detail=error.detail,
            )
            if error.usage:
                row["usage"] = error.usage.model_dump()
                row["cost_upper_usd"] = str(cost(row["usage"]))
            elif error.reservation_releasable:
                row["cost_upper_usd"] = "0"
            raise
        else:
            row.update(
                state="completed",
                usage=body["usage"],
                model=body["model"],
                cost_upper_usd=str(cost(body["usage"])),
                response=body["choices"],
            )
            if Decimal(row["cost_upper_usd"]) > Decimal(row["reservation_usd"]):
                raise RuntimeError("provider_exceeded_conservative_reservation")
            return body
        finally:
            row["latency_ms"] = round((time.monotonic() - started) * 1000)
            self.ledger.save()
            print(
                json.dumps(
                    {k: row[k] for k in ("label", "state", "latency_ms")}, ensure_ascii=False
                ),
                flush=True,
            )


async def main(path, *, retest_ambiguous=False):
    manifest = json.loads((FIXTURES / "image-pixels/manifest.json").read_text())
    gold = json.loads((FIXTURES / "image-scenarios.json").read_text())
    assert len(manifest["images"]) == 16
    for item in manifest["images"]:
        assert (
            hashlib.sha256((FIXTURES / "image-pixels" / item["path"]).read_bytes()).hexdigest()
            == item["sha256"]
        )
    ledger = Ledger(path, retest_ambiguous=retest_ambiguous)
    if ledger.data.get("input_sha256", manifest["input_sha256"]) != manifest["input_sha256"]:
        raise ValueError("fixture_snapshot_changed")
    if ledger.data.get("run_revision", 1) != 4:
        old_images = copy.deepcopy(ledger.data["images"])
        ledger.data.setdefault("rounds", []).append(
            {
                "run_revision": ledger.data.get("run_revision", 1),
                "images": old_images,
                "scenarios": copy.deepcopy(ledger.data["scenarios"]),
                "note": "Replaced contextual pass: image facts must be pixel-supported; chat meaning stays separate.",
            }
        )
        ledger.data["images"] = {
            ident: {"a": value["a"]} for ident, value in old_images.items() if "a" in value
        }
        ledger.data["scenarios"] = {}
    ledger.data["run_revision"] = 4
    ledger.data["input_sha256"] = manifest["input_sha256"]
    key = os.environ.get("DEEPSEEK_API_KEY") or dotenv_values(
        os.environ.get("STUDGROUP_SYNTHETIC_ENV_FILE", str(ROOT / ".env"))
    ).get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("provider_not_configured")
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=8), trust_env=False, follow_redirects=False
            ) as client:
                response = await client.get(
                    "https://api.deepseek.com/v1/models", headers={"Authorization": f"Bearer {key}"}
                )
                response.raise_for_status()
                models = [row["id"] for row in response.json()["data"]]
            break
        except httpx.ConnectTimeout:
            if attempt == 2:
                raise
    if "deepseek-flash" not in models:
        raise ValueError("approved_model_not_available")
    ledger.data["preflight"] = {
        "at": datetime.now(UTC).isoformat(),
        "model_listed": True,
        "vision_documentation": "https://api-docs.deepseek.com/guides/vision/",
        "pricing_documentation": "https://api-docs.deepseek.com/quick_start/pricing/",
    }
    ledger.save()
    provider = RecordedProvider(key, ledger)
    for scenario in gold["scenarios"]:
        ident = scenario["id"]
        context = {
            "timezone": "Europe/Moscow",
            "schedule": [],
            "existing_items": [],
            "fixture_context_note": "Synthetic chat annotations are explicit replies to the first image in this album.",
            "messages": [
                {
                    "id": i + 1,
                    "text": "",
                    "revision": 1,
                    "reply_to": None,
                    "media_group_ids": [ident],
                    "sent_at": "2026-10-27T06:00:00Z",
                }
                for i in range(len(scenario["image_text"]))
            ]
            + [
                {
                    "id": 100 + i,
                    "text": text,
                    "revision": 1,
                    "reply_to": 1,
                    "media_group_ids": [],
                    "sent_at": "2026-10-27T06:01:00Z",
                }
                for i, text in enumerate(scenario["messages"])
            ],
        }
        evidence, messages, metadata = [], [], []
        for i, _ in enumerate(scenario["image_text"]):
            image_id = f"{ident}:{i}"
            item = next(item for item in manifest["images"] if item["image_id"] == image_id)
            pixels = (FIXTURES / "image-pixels" / item["path"]).read_bytes()
            record = ledger.data["images"].setdefault(image_id, {})
            attachment = uuid5(NAMESPACE_URL, "studgroup-synthetic/" + image_id)
            messages.append(
                SourceMessage(
                    telegram_message_id=i + 1,
                    revision=1,
                    message_date=datetime(2026, 10, 27, 6, tzinfo=UTC),
                    text="",
                    attachments=[
                        SourceAttachment(
                            attachment_id=attachment,
                            kind="photo",
                            mime_type="image/png",
                            processing_state="ready",
                            media_group_id=ident,
                        )
                    ],
                )
            )
            context["image_source_message_id"] = i + 1
            try:
                if "a" in record:
                    first = VisualReading.model_validate_json(json.dumps(record["a"]))
                else:
                    provider.label = "r4/" + image_id + "/a"
                    first, _ = await read_pass(provider, pixels, "image/png", 1, context={})
                    record["a"] = first.model_dump(mode="json")
                    ledger.save()
                if "b" in record:
                    second = VisualReading.model_validate_json(json.dumps(record["b"]))
                else:
                    provider.label = "r4/" + image_id + "/b"
                    second, _ = await read_pass(
                        provider, pixels, "image/png", 2, context=context, first=first
                    )
                    record["b"] = second.model_dump(mode="json")
                    ledger.save()
            except ProviderFailure as error:
                record["error"] = error.code
                ledger.save()
                evidence.append(
                    ImageEvidence(
                        attachment_id=attachment,
                        telegram_message_id=i + 1,
                        source_revision=1,
                        context_sha256="0" * 64,
                        state="failed",
                        error=error.code,
                    )
                )
                continue
            record.pop("error", None)
            attachment = uuid5(NAMESPACE_URL, "studgroup-synthetic/" + image_id)
            ready = second.complete
            evidence.append(
                ImageEvidence(
                    attachment_id=attachment,
                    telegram_message_id=i + 1,
                    source_revision=1,
                    context_sha256="0" * 64,
                    state="ready" if ready else "failed",
                    reading=second if ready else None,
                    error=None if ready else "incomplete_reading",
                )
            )
            fingerprint = visual_fingerprint(pixels)
            meta = {
                "attachment_id": str(attachment),
                "image_id": image_id,
                "telegram_message_id": i + 1,
                "image_analysis_state": "ready" if ready else "failed",
                "semantic_types": second.semantic_types,
                "content_signature": hashlib.sha256(
                    json.dumps(
                        second.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
                    ).encode()
                ).hexdigest(),
                "visual_hash": fingerprint,
                "pixel_sha256": item["sha256"],
                "width": 1100,
                "height": 900,
            }
            metadata.append(meta)
        evidence_hash = hashlib.sha256(
            json.dumps([item.model_dump(mode="json") for item in evidence], sort_keys=True).encode()
        ).hexdigest()
        previous = ledger.data["scenarios"].get(ident)
        if (
            previous
            and "error" not in previous
            and previous.get("evidence_sha256") == evidence_hash
            and previous.get("core_prompt_version") == EXTRACTION_PROMPT_VERSION
            and previous.get("validation_revision") == "image-final-evidence-2"
        ):
            continue
        messages.extend(
            SourceMessage(
                telegram_message_id=100 + i,
                revision=1,
                message_date=datetime(2026, 10, 27, 6, tzinfo=UTC),
                text=text,
                reply_to_message_id=1,
            )
            for i, text in enumerate(scenario["messages"])
        )
        if not messages:
            ledger.data["scenarios"][ident] = {"error": "no_valid_images"}
            ledger.save()
            continue
        data = AnalysisInput(
            group_id=uuid5(NAMESPACE_URL, "studgroup-synthetic/" + ident),
            group_timezone="Europe/Moscow",
            source_revision=1,
            messages=add_image_facts(messages, evidence),
            target_message_ids=[m.telegram_message_id for m in messages],
            image_evidence=evidence,
        )
        provider.label = "r4/" + ident + "/cplus"
        try:
            result = await run_shadow(provider, data, [], repair_enabled=False)
            output = {
                "context": result.context.model_dump(mode="json"),
                "validation": result.validation.model_dump(mode="json"),
            }
        except (ShadowPassFailure, ProviderFailure) as error:
            output = {"error": getattr(error, "code", type(error).__name__)}
        output["evidence_sha256"] = evidence_hash
        output["core_prompt_version"] = EXTRACTION_PROMPT_VERSION
        output["validation_revision"] = "image-final-evidence-2"
        if previous:
            ledger.data.setdefault("scenario_attempts", {}).setdefault(ident, []).append(previous)
        output["display_preview_pool"] = [m["image_id"] for m in select_evidence_images(metadata)]
        output["selected_by_card"] = selected_card_images(output.get("validation", {}), metadata)
        output["selected_images"] = list(
            dict.fromkeys(image for card in output["selected_by_card"] for image in card)
        )
        ledger.data["scenarios"][ident] = output
        ledger.save()
    print("Synthetic run finished; ledger:", path, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--retest-ambiguous",
        action="store_true",
        help="Explicit synthetic-only retest; keep unknown reservations fully charged within same $1 ledger",
    )
    args = parser.parse_args()
    with args.out.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(main(args.out.resolve(), retest_ambiguous=args.retest_ambiguous))
