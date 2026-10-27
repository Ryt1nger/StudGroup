"""Actual pixel proofs, no model calls or disclosure of private images."""

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from studgroup.analysis.image_contracts import ImageBlock, VisualReading
from studgroup.image_display_coverage import build_coverage, coverage_metadata, visually_covers
from studgroup.photo_dedupe import select_evidence_images

FIXTURES = Path(__file__).parent / "fixtures/ai_analysis"


def printed(lines, *, size=28, extra=None):
    image = Image.new("RGB", (850, 400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=size)
    for i, line in enumerate(lines):
        draw.text((20, 25 + i * 65), line, fill="black", font=font)
    if extra:
        extra(draw)
    output = io.BytesIO()
    image.save(output, format="PNG")
    reading = VisualReading(
        summary="Printed text",
        extracted_text="\n".join(lines),
        complete=True,
        blocks=[{"kind": "text", "content": "\n".join(lines)}],
    )
    return output.getvalue(), reading


def candidates(inputs):
    profiles = {str(i): build_coverage(image, reading) for i, (image, reading) in enumerate(inputs)}
    metadata = coverage_metadata(profiles)
    return [
        {
            "attachment_id": ident,
            "image_analysis_state": "ready",
            "semantic_types": ["homework"],
            "content_signature": "deliberately identical OCR signature",
            "visual_hash": "1.0:0000000000000000",
            **metadata[ident],
        }
        for ident in profiles
    ]


def test_recorded_three_crops_choose_complete_third_without_any_new_ai_call():
    data = json.loads((FIXTURES / "image-provider-observations.json").read_text())
    inputs = []
    for i in range(3):
        reading = VisualReading.model_validate_json(
            json.dumps(data["images"][f"three_crops:{i}"]["b"])
        )
        inputs.append(((FIXTURES / f"image-pixels/three_crops-{i}.png").read_bytes(), reading))
    items = candidates(inputs)
    assert [r["attachment_id"] for r in select_evidence_images(items)] == ["2"]
    assert len(items) == 3
    assert "1–5" in inputs[0][1].extracted_text
    assert "6–8" in inputs[1][1].extracted_text
    assert "PDF" in inputs[2][1].extracted_text


def test_four_distinct_recorded_pages_are_preserved():
    data = json.loads((FIXTURES / "image-provider-observations.json").read_text())
    inputs = [
        (
            (FIXTURES / f"image-pixels/four_pages-{i}.png").read_bytes(),
            VisualReading.model_validate_json(json.dumps(data["images"][f"four_pages:{i}"]["b"])),
        )
        for i in range(4)
    ]
    assert len(select_evidence_images(candidates(inputs))) == 4


def test_complementary_halves_both_remain_and_a_complete_page_can_cover_them():
    upper = printed(["Assignment", "Prepare report"])
    lower = printed(["Assignment", "Submit PDF"])
    assert len(select_evidence_images(candidates([upper, lower]))) == 2
    whole = printed(["Assignment", "Prepare report", "Submit PDF"])
    assert [
        r["attachment_id"] for r in select_evidence_images(candidates([upper, lower, whole]))
    ] == ["2"]


def test_wide_view_cannot_replace_more_readable_detail():
    detail = printed(["Important condition", "Solve exercises 1-5"], size=32)
    wide = printed(["Important condition", "Solve exercises 1-5"], size=14)
    # Different rasterizations do not prove equivalence; the legible detail must remain.
    assert "1" in {
        row["attachment_id"] for row in select_evidence_images(candidates([wide, detail]))
    }
    wide_with_unique = printed(
        ["Important condition", "Solve exercises 1-5", "Unique footer"], size=14
    )
    assert len(select_evidence_images(candidates([wide_with_unique, detail]))) == 2


def test_shared_headings_never_cover_different_exercise_numbers():
    inputs = [printed(["Practical work", f"Exercise {i}"]) for i in range(1, 5)]
    assert len(select_evidence_images(candidates(inputs))) == 4


def test_almost_identical_timetables_with_changed_room_remain():
    assert (
        len(
            select_evidence_images(
                candidates(
                    [
                        printed(["Monday Algebra", "Room 201"]),
                        printed(["Monday Algebra", "Room 202"]),
                    ]
                )
            )
        )
        == 2
    )


def test_omitted_formula_or_illustration_invalidates_the_coverage_proof():
    first = printed(
        ["Shared heading"], extra=lambda d: d.line((30, 160, 180, 200), fill="black", width=3)
    )
    second = printed(
        ["Shared heading"], extra=lambda d: d.ellipse((30, 160, 180, 200), outline="black", width=3)
    )
    assert build_coverage(*first) is None
    assert build_coverage(*second) is None
    assert len(select_evidence_images(candidates([first, second]))) == 2


def test_transcribed_formula_requires_matching_pixels_even_when_ocr_is_identical():
    first = printed(["x+1=2"])
    second = printed(["x+1=3"])
    # Incorrect identical OCR cannot make the different visible digit disappear.
    second = (second[0], first[1])
    assert build_coverage(*first) is not None
    assert build_coverage(*second) is not None
    assert len(select_evidence_images(candidates([first, second]))) == 2


def test_real_pixel_crop_is_covered_by_the_readable_full_page():
    full, reading = printed(["Header", "Condition one", "Condition two"])
    image = Image.open(io.BytesIO(full))
    crop = image.crop((0, 80, 850, 210))
    output = io.BytesIO()
    crop.save(output, format="PNG")
    cropped = VisualReading(
        summary="Two lines",
        extracted_text="Condition one\nCondition two",
        complete=True,
        blocks=[{"kind": "text", "content": "Condition one\nCondition two"}],
    )
    assert visually_covers(
        build_coverage(full, reading), build_coverage(output.getvalue(), cropped)
    )


def test_explicit_diagram_block_is_never_hidden_by_text_coverage():
    image, reading = printed(["Shared heading"])
    reading = reading.model_copy(
        update={"blocks": [*reading.blocks, ImageBlock(kind="diagram", content="Unique diagram")]}
    )
    # Use normal model validation for the additional visual block.
    reading = VisualReading.model_validate_json(reading.model_dump_json())
    assert build_coverage(image, reading) is None


def test_visual_display_metadata_stays_in_snapshot_but_is_never_sent_to_ai():
    from test_analysis_shadow import analysis_input

    from studgroup.analysis.context_strategy import prepare_context_request
    from studgroup.analysis.contracts import SourceAttachment
    from studgroup.analysis.extraction_strategy import prepare_extraction_request

    data = analysis_input()
    attachment = SourceAttachment(
        kind="photo", display_covers=["covered-photo"], display_readability=25
    )
    data = data.model_copy(
        update={"messages": [data.messages[0].model_copy(update={"attachments": [attachment]})]}
    )
    assert data.model_dump(mode="json")["messages"][0]["attachments"][0]["display_covers"] == [
        "covered-photo"
    ]
    for request in (prepare_context_request(data, []), prepare_extraction_request(data, {})):
        payload = request.user_payload["messages"][0]["attachments"][0]
        assert "display_covers" not in payload
        assert "display_readability" not in payload


def test_subtraction_is_not_an_expandable_exercise_range():
    first = printed(["Calculate 1-5"])
    second = printed(["Calculate 1-8"])
    assert len(select_evidence_images(candidates([first, second]))) == 2
    first = printed(["Calculate 1–5"])
    second = printed(["Calculate 1–8"])
    assert len(select_evidence_images(candidates([first, second]))) == 2
