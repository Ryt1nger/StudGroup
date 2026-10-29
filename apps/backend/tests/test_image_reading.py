import asyncio
import json

import pytest
from test_image_jobs import reading
from test_photo_reading import JPEG, VisionProvider

from studgroup.ai import ProviderFailure
from studgroup.analysis.image_reading import read_pass


def test_block_kind_alias_only_normalizes_unambiguous_values():
    from pydantic import ValidationError

    from studgroup.analysis.image_contracts import ImageBlock

    assert (
        ImageBlock.model_validate({"kind": "text", "type": "text", "content": "17"}).content == "17"
    )
    assert (
        ImageBlock.model_validate({"type": "table", "content": "", "rows": [["205"]]}).kind
        == "table"
    )
    with pytest.raises(ValidationError):
        ImageBlock.model_validate({"kind": "text", "type": "table", "content": "17"})
    with pytest.raises(ValidationError):
        ImageBlock.model_validate({"kind": "text", "extra": "invented", "content": "17"})


def test_descriptive_block_label_is_preserved_without_becoming_a_confirmed_fact():
    async def run():
        payload = json.loads(reading())
        payload["blocks"] = [
            {
                "kind": "text",
                "title": "Верхний левый угол",
                "content": payload["extracted_text"],
                "rows": [],
            }
        ]
        provider = VisionProvider(json.dumps(payload))
        result, _ = await read_pass(provider, JPEG, "image/jpeg", 1, context={})
        assert result.blocks[0].title == "Верхний левый угол"
        assert all(fact.text != "Верхний левый угол" for fact in result.facts)
        assert result.extracted_text == payload["extracted_text"]

    asyncio.run(run())


def test_both_passes_get_pixels_and_b_gets_context_and_saved_a():
    async def run():
        provider = VisionProvider(reading())
        a, _ = await read_pass(provider, JPEG, "image/jpeg", 1, context={})
        b, _ = await read_pass(
            provider, JPEG, "image/jpeg", 2, context={"messages": ["дома"]}, first=a
        )
        assert a.extracted_text == b.extracted_text
        assert len(provider.vision) == 2
        assert provider.vision[0]["image_data_url"] == provider.vision[1]["image_data_url"]
        body = json.loads(provider.vision[1]["user_text"])
        assert body["pass_a"]["extracted_text"] == a.extracted_text
        assert body["chat_context"]["messages"] == ["дома"]

    asyncio.run(run())


def test_truncated_json_is_not_a_success_even_when_it_parses():
    class Truncated(VisionProvider):
        async def complete_vision(self, **kwargs):
            body = await super().complete_vision(**kwargs)
            body["choices"][0]["finish_reason"] = "length"
            return body

    async def run():
        with pytest.raises(ProviderFailure) as captured:
            await read_pass(Truncated(reading()), JPEG, "image/jpeg", 1, context={})
        assert captured.value.code == "image_output_incomplete"
        assert captured.value.usage.prompt_tokens == 900

    asyncio.run(run())


def test_pass_b_cannot_drop_unique_confirmed_facts_from_pass_a():
    async def run():
        payload = json.loads(reading()) | {
            "facts": [{"text": "задачи 6–8", "certainty": "confirmed"}]
        }
        a, _ = await read_pass(
            VisionProvider(json.dumps(payload)), JPEG, "image/jpeg", 1, context={}
        )
        b, _ = await read_pass(
            VisionProvider(reading()), JPEG, "image/jpeg", 2, context={}, first=a
        )
        assert b.facts[0].text == "задачи 6–8"

    asyncio.run(run())
