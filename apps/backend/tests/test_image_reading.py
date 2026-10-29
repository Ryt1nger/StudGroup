import asyncio
import json

import pytest
from test_image_jobs import reading
from test_photo_reading import JPEG, VisionProvider

from studgroup.ai import ProviderFailure
from studgroup.analysis.image_reading import read_pass


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
