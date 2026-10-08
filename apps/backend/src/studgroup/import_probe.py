"""Bounded explicit paid preview of a Telegram export; never publishes production data."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from studgroup.ai import SYSTEM_PROMPT, DeepSeekProvider, ProviderFailure
from studgroup.chat_export import read_html_export
from studgroup.main import Settings


def chunks(messages):
    batch = []
    for message in messages:
        row = {
            "message_id": message.message_id,
            "message_date": message.message_date.isoformat(),
            "reply_to": message.reply_to_message_id,
            "text": message.text,
        }
        if len(json.dumps([row], ensure_ascii=False).encode()) > 14000:
            raise ValueError("oversized_message_requires_separate_processing")
        if batch and (
            len(batch) >= 70 or len(json.dumps(batch + [row], ensure_ascii=False).encode()) > 14000
        ):
            yield batch
            batch = []
        batch.append(row)
    if batch:
        yield batch


async def run(path: Path, output: Path, timezone: str, budget: float):
    if not 0 < budget <= 0.10:
        raise ValueError("preview_budget_must_be_between_zero_and_ten_cents")
    settings = Settings()
    if settings.deepseek_model != "deepseek-flash":
        raise ValueError("preview_requires_flash_pricing")
    provider = DeepSeekProvider(
        settings.deepseek_api_key.get_secret_value(),
        settings.deepseek_model,
        settings.deepseek_base_url,
    )
    messages = read_html_export(path, timezone=timezone)
    now = datetime.now(UTC)
    recent = [m for m in messages if m.text and m.message_date >= now - timedelta(days=7)]
    older = [m for m in messages if m.text and m.message_date < now - timedelta(days=7)]
    # Recent history first; all older text also receives a cheap model pass if budget permits.
    batches = list(chunks(recent)) + list(chunks(older))
    report = {
        "mode": "local_test_only",
        "timezone_assumption": timezone,
        "generated_at": now.isoformat(),
        "total_messages": len(messages),
        "total_text_messages": len(recent) + len(older),
        "processed_ids": [],
        "assignments": [],
        "calls": [],
        "estimated_peak_cost_usd": 0.0,
        "reserved_cost_usd": 0.0,
        "complete": False,
    }
    if output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        if report.get("mode") != "local_test_only" or report.get("timezone_assumption") != timezone:
            raise ValueError("incompatible_import_report")
        if report.get("stop_reason") not in {None, "budget_limit"}:
            raise ValueError("failed_import_requires_manual_review_before_retry")
        done = set(report["processed_ids"])
        batches = [[row for row in batch if row["message_id"] not in done] for batch in batches]
        batches = [batch for batch in batches if batch]
        report.pop("stop_reason", None)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        # Exclusive private report path; raw chat and credentials are never written to the report.
        with output.open("w", encoding="utf-8") as file:
            os.chmod(output, 0o600)
            json.dump(report, file, ensure_ascii=False, indent=2)

    for index, batch in enumerate(batches):
        # Conservative token bound: UTF-8 bytes >= tokens, plus prompt/format allowance.
        bound = (
            len(json.dumps(batch, ensure_ascii=False).encode()) + len(SYSTEM_PROMPT.encode()) + 6000
        ) * 0.3 / 1_000_000 + 3000 * 1.2 / 1_000_000
        if report["reserved_cost_usd"] + bound > budget:
            report["stop_reason"] = "budget_limit"
            break
        report["reserved_cost_usd"] += bound
        save()  # Reserve before sending; failed requests keep their reservation.
        try:
            result = await provider.extract_batch(batch, timezone)
        except ProviderFailure as error:
            if error.reservation_releasable:
                report["reserved_cost_usd"] -= bound
            report["calls"].append({"batch": index, "error": error.code})
            report["stop_reason"] = error.code
            save()
            print("Import stopped:", error.code, flush=True)
            return
        usage = result.usage
        cost = (usage.prompt_tokens * 0.3 + usage.completion_tokens * 1.2) / 1_000_000
        report["estimated_peak_cost_usd"] += cost
        report["calls"].append(
            {
                "batch": index,
                "usage": usage.model_dump(),
                "model": result.model,
                "prompt_version": result.prompt_version,
                "estimated_peak_cost_usd": cost,
            }
        )
        report["processed_ids"].extend(row["message_id"] for row in batch)
        report["assignments"].extend(a.model_dump(mode="json") for a in result.batch.assignments)
        save()
        print(
            "Batch",
            index + 1,
            "/",
            len(batches),
            "messages",
            len(batch),
            "candidates",
            len(result.batch.assignments),
            flush=True,
        )
    else:
        report["complete"] = True
    save()
    print(
        "Processed:",
        len(report["processed_ids"]),
        "candidates:",
        len(report["assignments"]),
        "estimated peak USD:",
        round(report["estimated_peak_cost_usd"], 6),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--budget", type=float, default=0.05)
    args = parser.parse_args()
    asyncio.run(run(args.archive, args.output, args.timezone, args.budget))
