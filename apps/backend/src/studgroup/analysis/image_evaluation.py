"""Offline-only fixture rendering/comparison. No provider, HTTP, production DB or writer imports."""

import argparse
import hashlib
import json
import re
import textwrap
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    image_states: dict[str, Literal["ready", "failed", "pending", "retry", "budget_deferred"]]
    facts: list[str] | None = None
    semantic_types: list[str] | None = None
    homework_count: int | None = Field(default=None, ge=0)
    display_count: int | None = Field(default=None, ge=0)
    schedule_rows: list[dict] | None = None
    event: dict | None = None
    adjudicated_checks: dict[
        Literal[
            "context_linking",
            "duplicate_suppression",
            "unique_fact_preservation",
            "display_completeness",
            "no_invented_critical_fact",
            "no_cross_group_leak",
        ],
        bool,
    ] = Field(default_factory=dict)
    cost_usd: Decimal | None = Field(default=None, ge=0)
    latency_ms: list[int] = Field(default_factory=list)


class ObservationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_kind: Literal["synthetic_stub", "recorded_provider"]
    cases: list[Observation]


def normalized(text):
    return " ".join(text.casefold().replace("ё", "е").split())


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate(gold, observations=None):
    """Exact annotation matching; absent measurements remain null, never assumed successful."""
    expected = {row["id"]: row for row in gold["scenarios"]}
    if len(expected) != len(gold["scenarios"]):
        raise ValueError("duplicate_gold_case")
    run = ObservationRun.model_validate(observations) if observations is not None else None
    actual = {row.case_id: row for row in run.cases} if run else {}
    if run and (len(actual) != len(run.cases) or set(actual) - set(expected)):
        raise ValueError("observation_case_mismatch")
    account = completed = total_images = tp = fp = fn = classifications = correct = 0
    homework_cases = correct_homework = display_cases = correct_display = 0
    schedule_cases = correct_schedule = event_cases = correct_event = 0
    measured_facts = 0
    judgments = {}
    cost, costs, latencies = Decimal(0), 0, []
    for ident, case in expected.items():
        image_ids = {f"{ident}:{index}" for index in range(len(case["image_text"]))}
        total_images += len(image_ids)
        observed = actual.get(ident)
        if observed is None:
            continue
        if set(observed.image_states) - image_ids:
            raise ValueError("observation_image_mismatch")
        account += len(observed.image_states)
        completed += sum(state == "ready" for state in observed.image_states.values())
        target = case["expected"]
        if "facts" in target and observed.facts is not None:
            wanted, found = (
                set(map(normalized, target["facts"])),
                set(map(normalized, observed.facts)),
            )
            tp, fp, fn = (
                tp + len(wanted & found),
                fp + len(found - wanted),
                fn + len(wanted - found),
            )
            measured_facts += 1
        if "semantic_types" in target and observed.semantic_types is not None:
            classifications += 1
            correct += set(target["semantic_types"]) == set(observed.semantic_types)
        if "homework_count" in target and observed.homework_count is not None:
            homework_cases += 1
            correct_homework += target["homework_count"] == observed.homework_count
        if "display_count" in target and observed.display_count is not None:
            display_cases += 1
            correct_display += target["display_count"] == observed.display_count
        if "schedule_rows" in target and observed.schedule_rows is not None:
            schedule_cases += 1
            correct_schedule += target["schedule_rows"] == observed.schedule_rows
        if "event" in target and observed.event is not None:
            event_cases += 1
            correct_event += target["event"] == observed.event
        for check, passed in observed.adjudicated_checks.items():
            judgments.setdefault(check, []).append(passed)
        if observed.cost_usd is not None:
            cost, costs = cost + observed.cost_usd, costs + 1
        if any(value < 0 for value in observed.latency_ms):
            raise ValueError("negative_latency")
        latencies.extend(observed.latency_ms)
    return {
        "version": "image-evaluation-1",
        "evidence_kind": run.evidence_kind if run else "none",
        "real_model_quality": "UNMEASURED / AWAITING OWNER APPROVAL"
        if not run or run.evidence_kind == "synthetic_stub"
        else "RECORDED DATA ONLY; verify approval and annotation provenance",
        "input_sha256": run.input_sha256 if run else None,
        "case_count": len(expected),
        "observed_cases": len(actual),
        "total_images": total_images,
        "image_accounting_coverage": ratio(account, total_images) if run else None,
        "image_completed_coverage": ratio(completed, total_images) if run else None,
        "fact_matching": "exact normalized annotation text; paraphrases require human adjudication",
        "fact_precision": ratio(tp, tp + fp) if measured_facts else None,
        "fact_recall": ratio(tp, tp + fn) if measured_facts else None,
        "classification_accuracy": ratio(correct, classifications),
        "homework_count_accuracy": ratio(correct_homework, homework_cases),
        "display_count_accuracy": ratio(correct_display, display_cases),
        "schedule_rows_accuracy": ratio(correct_schedule, schedule_cases),
        "event_fields_accuracy": ratio(correct_event, event_cases),
        "reported_cost_usd": str(cost) if costs else None,
        "median_latency_ms": median(latencies) if latencies else None,
        "annotation_cases_measured": {
            "facts": measured_facts,
            "classification": classifications,
            "homework": homework_cases,
            "display": display_cases,
            "schedule": schedule_cases,
            "event": event_cases,
        },
        "adjudicated_metrics": {
            key: ratio(sum(values), len(values)) for key, values in judgments.items()
        },
        "unmeasured_metrics": [
            key
            for key in (
                "context_linking",
                "duplicate_suppression",
                "unique_fact_preservation",
                "display_completeness",
                "no_invented_critical_fact",
                "no_cross_group_leak",
            )
            if key not in judgments
        ]
        + ["human_adjudicated_fact_precision_recall"],
    }


def compare(gold, old, new):
    before, after = evaluate(gold, old), evaluate(gold, new)
    if before["input_sha256"] != after["input_sha256"]:
        raise ValueError("comparison_requires_identical_input_snapshot")
    metrics = (
        "image_accounting_coverage",
        "image_completed_coverage",
        "fact_precision",
        "fact_recall",
        "classification_accuracy",
        "homework_count_accuracy",
        "display_count_accuracy",
        "schedule_rows_accuracy",
        "event_fields_accuracy",
        "median_latency_ms",
    )
    return {
        "before": before,
        "after": after,
        "delta": {
            key: after[key] - before[key]
            if after[key] is not None and before[key] is not None
            else None
            for key in metrics
        },
    }


def render(gold, output, font_path):
    """Create actual synthetic pixels; never copy real student pictures or private exports."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(font_path), 28)
    manifest = {
        "version": "image-pixels-1",
        "provenance": "synthetic printed fixtures; not handwriting or real chat",
        "images": [],
    }
    for case in gold["scenarios"]:
        if not re.fullmatch(r"[a-z0-9_]+", case["id"]):
            raise ValueError("invalid_fixture_id")
        for index, text in enumerate(case["image_text"]):
            image = Image.new("RGB", (1100, 900), "white")
            draw = ImageDraw.Draw(image)
            draw.text((40, 30), "SYNTHETIC FIXTURE / " + case["id"], fill="gray", font=font)
            if text is None:
                for y in range(140, 550, 28):
                    draw.line((50, y, 1000, y + 8), fill="lightgray", width=20)
            else:
                wrapped = "\n".join(textwrap.wrap(text, width=58))
                draw.multiline_text((40, 120), wrapped, fill="black", font=font, spacing=14)
            name = f"{case['id']}-{index}.png"
            path = output / name
            image.save(path)
            manifest["images"].append(
                {
                    "image_id": f"{case['id']}:{index}",
                    "path": name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    manifest["input_sha256"] = hashlib.sha256(
        json.dumps(
            {"images": manifest["images"], "gold_and_context": gold}, sort_keys=True
        ).encode()
    ).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["render", "evaluate", "compare"])
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text())
    if args.command == "render":
        if args.out is None or args.font is None:
            parser.error("render requires --out and an installed --font (no fonts are downloaded)")
        result = render(gold, args.out, args.font)
    else:
        observations = json.loads(args.observations.read_text()) if args.observations else None
        if args.command == "compare":
            if args.baseline is None or observations is None:
                parser.error("compare requires --baseline and --observations")
            result = compare(gold, json.loads(args.baseline.read_text()), observations)
        else:
            result = evaluate(gold, observations)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
