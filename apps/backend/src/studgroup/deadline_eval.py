"""Bounded real-provider evaluation. Gold labels never enter the provider payload."""

import argparse
import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from studgroup.academic_context import build_context
from studgroup.ai import SYSTEM_PROMPT, DeepSeekProvider, ProviderFailure
from studgroup.chat_export import read_html_export
from studgroup.main import Settings


def score(case, assignments, timezone):
    if case.get("no_event"):
        return len(assignments) == 0
    for item in assignments:
        if item["kind"] not in case["kinds"]:
            continue
        if case.get("unknown"):
            if item["deadline_at"] is None:
                return True
            continue
        if not item["deadline_at"]:
            continue
        local = datetime.fromisoformat(item["deadline_at"]).astimezone(ZoneInfo(timezone))
        if (
            local.date().isoformat() == case["date"]
            and item["deadline_date_only"] == case["date_only"]
            and (not case.get("time") or local.strftime("%H:%M") == case["time"])
        ):
            return True
    return False


async def run(directory: Path, archive: Path, budget=0.06):
    if not 0 < budget <= 0.06:
        raise ValueError("evaluation_budget_exceeds_six_cents")
    settings = Settings()
    if settings.deepseek_model != "deepseek-flash":
        raise ValueError("evaluation_requires_flash")
    provider = DeepSeekProvider(
        settings.deepseek_api_key.get_secret_value(),
        settings.deepseek_model,
        settings.deepseek_base_url,
    )
    timezone = "Europe/Moscow"
    messages = read_html_export(archive, timezone=timezone)
    cases = json.loads((directory / "deadline-eval-cases.json").read_text())
    output = directory / "deadline-eval-results.json"
    if output.exists():
        raise ValueError("evaluation_already_exists_review_before_repeating_paid_calls")
    report = {
        "mode": "real_provider_evaluation",
        "cases": [],
        "reserved_usd": 0.0,
        "estimated_peak_usd": 0.0,
        "complete": False,
    }

    def save():
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, delete=False
        ) as file:
            os.chmod(file.name, 0o600)
            json.dump(report, file, ensure_ascii=False, indent=2)
        os.replace(file.name, output)

    for case in cases:
        context = build_context(messages, case["target"])
        bound = (
            len(json.dumps(context, ensure_ascii=False).encode())
            + len(SYSTEM_PROMPT.encode())
            + 6500
        ) * 0.3 / 1_000_000 + 3000 * 1.2 / 1_000_000
        if report["reserved_usd"] + bound > budget:
            report["stop_reason"] = "budget_limit"
            break
        report["reserved_usd"] += bound
        save()
        try:
            result = await provider.extract_batch(
                context, timezone, target_message_id=case["target"]
            )
        except ProviderFailure as error:
            report["cases"].append({"target": case["target"], "error": error.code})
            report["stop_reason"] = error.code
            save()
            print("Evaluation stopped:", error.code, flush=True)
            return
        assignments = [a.model_dump(mode="json") for a in result.batch.assignments]
        passed = score(case, assignments, timezone)
        cost = (result.usage.prompt_tokens * 0.3 + result.usage.completion_tokens * 1.2) / 1_000_000
        report["estimated_peak_usd"] += cost
        report["cases"].append(
            {
                "target": case["target"],
                "passed": passed,
                "context_ids": [m["message_id"] for m in context],
                "assignments": assignments,
                "usage": result.usage.model_dump(),
                "prompt_version": result.prompt_version,
            }
        )
        save()
        print(
            "Target", case["target"], "passed", passed, "context messages", len(context), flush=True
        )
    else:
        report["complete"] = True
    report["passed"] = sum(c.get("passed", False) for c in report["cases"])
    report["total"] = len(cases)
    save()
    print(
        "Evaluation:",
        report["passed"],
        "/",
        report["total"],
        "complete",
        report["complete"],
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.directory, args.archive))
