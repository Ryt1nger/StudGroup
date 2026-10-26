import io

from PIL import Image, ImageDraw

from studgroup.photo_dedupe import select_evidence_images, visual_fingerprint


def item(ident, *, pixel=None, facts="same", visual="1.0:0000000000000000", types=None):
    return {"attachment_id": ident, "image_analysis_state": "ready", "content_signature": facts,
            "pixel_sha256": pixel, "visual_hash": visual, "semantic_types": types or ["homework"]}


def test_different_pages_and_unique_crops_are_never_hidden_by_template_similarity():
    pages = [item(str(i), facts=f"exercise {i}") for i in range(1, 5)]
    assert select_evidence_images(pages) == pages
    crops = [item("a", facts="1–5"), item("b", facts="6–8"), item("c", facts="format PDF")]
    assert select_evidence_images(crops) == crops


def test_exact_duplicate_pixels_are_hidden_after_evidence_is_preserved():
    a, b, c = item("a", pixel="equal"), item("b", pixel="equal"), item("c", pixel="different", facts="other")
    b.update(width=2000, height=1000)
    assert [i["attachment_id"] for i in select_evidence_images([a, b, c])] == ["b", "c"]
    assert len([a, b, c]) == 3  # input/semantic evidence unchanged


def test_unrelated_material_is_excluded_by_exact_card_image_references():
    photos = [item("homework"), item("other-task", facts="other"), item("poster", types=["event"])]
    assert select_evidence_images(photos, {"homework"}) == photos[:1]


def test_local_visual_hash_uses_real_pixels():
    picture = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(picture).rectangle((20, 30, 100, 200), fill="black")
    stream = io.BytesIO()
    picture.save(stream, format="PNG")
    first = visual_fingerprint(stream.getvalue())
    assert first is not None
    assert first == visual_fingerprint(stream.getvalue())
    assert visual_fingerprint(b"not an image") is None
