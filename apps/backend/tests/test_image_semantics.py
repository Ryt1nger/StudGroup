"""Deterministic publication guards; not a measurement of model classification accuracy."""

import json
import uuid

import pytest
from test_analysis_shadow import analysis_input, proposal
from test_image_jobs import cfg, download_ok, reading, tick
from test_package_worker import make_new, step
from test_photo_reading import VisionProvider, photo_message

from studgroup.analysis.context_strategy import prepare_context_request
from studgroup.analysis.extraction_pipeline import adapt_and_validate
from studgroup.analysis.extraction_strategy import prepare_extraction_request
from studgroup.analysis.image_contracts import ImageEvidence, ImageFact, VisualReading
from studgroup.analysis.image_evidence import add_image_facts

pytest_plugins = ["test_schedule_api"]


def data_with_image(types, *, state="ready", text="", certainty="confirmed"):
    data = analysis_input()
    message = data.messages[0].model_copy(update={"text": text})
    image = ImageEvidence(
        attachment_id=uuid.uuid4(),
        telegram_message_id=message.telegram_message_id,
        source_revision=message.revision,
        context_sha256="0" * 64,
        state=state,
        reading=VisualReading(
            summary="Упражнения",
            extracted_text="Упражнения 1–8",
            complete=True,
            semantic_types=types,
            facts=[ImageFact(text="Упражнения 1–8", certainty=certainty)],
        )
        if state == "ready"
        else None,
    )
    return data.model_copy(
        update={
            "messages": add_image_facts([message], [image]),
            "target_message_ids": [message.telegram_message_id],
            "image_evidence": [image],
        }
    )


def validate(data, *, image_path=False):
    item = proposal(source_id=data.messages[0].telegram_message_id).model_dump(mode="json")
    if image_path:
        item["evidence"][0]["fact_paths"] = [f"image:{data.image_evidence[0].attachment_id}:0"]
    return adapt_and_validate(json.dumps({"proposals": [item]}), data).result


@pytest.mark.parametrize(
    "types",
    [
        ["in_class_task"],
        ["unknown"],
        ["schedule"],
        ["event"],
        ["organizational"],
        ["material"],
        ["schedule_change", "assessment"],
    ],
)
def test_non_homework_image_cannot_be_published_as_homework(types):
    result = validate(data_with_image(types))
    assert not result.accepted
    assert result.rejected[0].reason_code == "image_purpose_unconfirmed"
    assert result.candidates  # held for review rather than silently discarded


def test_confirmed_homework_can_use_precise_image_facts_and_both_prompts_see_them():
    data = data_with_image(["homework"])
    assert validate(data, image_path=True).accepted
    for request in (prepare_context_request(data, []), prepare_extraction_request(data, {})):
        assert (
            request.user_payload["image_evidence"][0]["reading"]["facts"][0]["text"]
            == "Упражнения 1–8"
        )


def test_unreadable_or_uncertain_images_never_confirm_a_fact():
    assert not validate(data_with_image(["homework"], state="failed"), image_path=True).accepted
    assert not validate(
        data_with_image(["homework"], certainty="uncertain"), image_path=True
    ).accepted
    # Independent text remains eligible under C+'s existing validation/publication policy.
    assert validate(data_with_image([], state="failed", text="ДЗ: упражнения 1–8")).accepted


def test_all_crop_readings_reach_both_cplus_passes(client, monkeypatch):
    download_ok(monkeypatch)
    for mid in (31, 32, 33):
        photo_message(client, mid=mid)
    make_new(client)
    config = cfg(client, image_analysis_mode="live")
    provider = VisionProvider(reading(), propose=False)
    for _ in range(6):
        assert tick(client, provider, config) == "ready"
    assert step(client, provider, config) == "completed"
    assert len(provider.requests) == 2
    assert all(len(request.user_payload["image_evidence"]) == 3 for request in provider.requests)


def test_classwork_caption_cannot_bypass_image_purpose_guard():
    result = validate(data_with_image(["in_class_task"], text="Решаем сейчас на паре"))
    assert not result.accepted and result.rejected[0].reason_code == "image_purpose_unconfirmed"


def test_bare_image_requires_exact_fact_reference_before_publication():
    result = validate(data_with_image(["homework"]))
    assert not result.accepted
    assert result.rejected[0].reason_code == "image_fact_reference_required"
    assert result.candidates
    assert validate(data_with_image(["homework"]), image_path=True).accepted


@pytest.mark.parametrize(
    "second_state,reason",
    [("ready", "image_fact_reference_required"), ("failed", "image_evidence_unavailable")],
)
def test_album_reconciliation_cannot_add_unchecked_image_sources(second_state, reason):
    from studgroup.analysis.contracts import SourceAttachment

    data = data_with_image(["homework"])
    first = data.messages[0]
    second_id = first.telegram_message_id + 1
    second_attachment = uuid.uuid4()
    first = first.model_copy(
        update={"attachments": [SourceAttachment(kind="photo", media_group_id="album")]}
    )
    second = first.model_copy(
        update={
            "telegram_message_id": second_id,
            "attachments": [
                SourceAttachment(
                    kind="photo", attachment_id=second_attachment, media_group_id="album"
                )
            ],
        }
    )
    second_evidence = data.image_evidence[0].model_copy(
        update={
            "attachment_id": second_attachment,
            "telegram_message_id": second_id,
            "state": second_state,
            "reading": data.image_evidence[0].reading if second_state == "ready" else None,
        }
    )
    data = data.model_copy(
        update={
            "messages": [first, second],
            "image_evidence": [*data.image_evidence, second_evidence],
        }
    )
    result = validate(data, image_path=True)
    assert not result.accepted
    assert result.rejected[0].reason_code == reason
    assert result.candidates
