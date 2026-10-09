import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from studgroup.ai import DeepSeekProvider, Extraction, ProviderFailure
from studgroup.main import Settings


@pytest.mark.parametrize("signal", [True, False])
def test_semantic_screen_uses_context_without_keyword_gate(signal):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 250
        assert "матан 16" in payload["messages"][1]["content"]
        return httpx.Response(
            200,
            json=response(
                json.dumps({"signal": signal, "source_message_ids": [1] if signal else []})
            ),
        )

    provider = DeepSeekProvider("test-key", transport=httpx.MockTransport(handler))
    result = asyncio.run(
        provider.screen_batch(
            [
                {"message_id": 1, "message_date": "2026-10-09T09:00:00+03:00", "text": "матан 16"},
                {
                    "message_id": 2,
                    "message_date": "2026-10-09T09:01:00+03:00",
                    "text": "Из сборника",
                },
            ],
            "Europe/Moscow",
            1,
        )
    )
    assert result.decision.signal is signal


def facts(**patch):
    return {
        "kind": "homework",
        "subject": "Математика",
        "title": "Задачи",
        "description": "Номера 1–3",
        "deadline_at": "2026-10-06T00:00:00+03:00",
        "deadline_date_only": True,
        "urgency": "normal",
        "confidence": 95,
    } | patch


def parsed(**patch):
    return Extraction.model_validate_json(json.dumps(facts(**patch)))


@pytest.mark.parametrize(
    "confidence,state", [(95, "published"), (84, "needs_context"), (64, "needs_context")]
)
def test_confidence_gates(confidence, state):
    assert parsed(confidence=confidence).publication_state == state


def test_unknown_deadline_is_not_invented_or_published():
    result = parsed(deadline_at=None, deadline_date_only=False)
    assert result.deadline_at is None
    assert result.publication_state == "incomplete_hidden"
    assert (
        parsed(deadline_at=None, deadline_date_only=False, urgency="urgent").publication_state
        == "needs_clarification"
    )


def test_control_point_is_not_homework_and_has_no_next_lesson_fallback():
    result = call(
        lambda request: httpx.Response(
            200,
            json=response(
                json.dumps(
                    facts(
                        kind="control_point",
                        title="КТ 1: эссе",
                        deadline_at=None,
                        deadline_date_only=False,
                    )
                )
            ),
        ),
        "КТ 1 по математике: эссе, срок не указан",
    )
    assert result.extraction.kind == "control_point"
    assert result.extraction.deadline_at is None
    assert result.extraction.publication_state == "needs_clarification"


def test_explicit_control_point_corrects_provider_homework_misclassification():
    result = call(
        lambda request: httpx.Response(200, json=response()),
        "Задание к Контрольной точке 1: написать эссе",
    )
    assert result.extraction.kind == "control_point"


@pytest.mark.parametrize(
    "patch",
    [
        {"confidence": 101},
        {"unexpected": "x"},
        {"deadline_at": "2026-10-06T12:00:00"},
        {"subject": " "},
        {"kind": "irrelevant"},
    ],
)
def test_untrusted_output_rejected(patch):
    with pytest.raises(ValidationError):
        parsed(**patch)


def response(content=None, finish="stop"):
    return {
        "model": "deepseek-flash",
        "choices": [
            {"finish_reason": finish, "message": {"content": content or json.dumps(facts())}}
        ],
        "usage": {
            "prompt_tokens": 300,
            "completion_tokens": 100,
            "prompt_cache_hit_tokens": 20,
            "prompt_cache_miss_tokens": 280,
        },
    }


def call(handler, text="ДЗ по математике: номера 1–3, завтра."):
    provider = DeepSeekProvider("private-test-key", transport=httpx.MockTransport(handler))
    return asyncio.run(
        provider.extract(text, datetime(2026, 10, 5, 9, tzinfo=UTC), "Europe/Moscow")
    )


def test_adapter_is_bounded_and_uses_original_date():
    def handler(request):
        payload = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer private-test-key"
        assert payload["max_tokens"] == 1400
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"}
        assert "2026-10-05T09:00:00+00:00" in payload["messages"][1]["content"]
        assert "untrusted data" in payload["messages"][0]["content"]
        return httpx.Response(200, json=response())

    result = call(handler)
    assert result.usage.prompt_tokens == 300
    assert result.usage.prompt_cache_hit_tokens == 20
    assert result.extraction.publication_state == "published"


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (401, "invalid_api_key", False),
        (402, "insufficient_balance", False),
        (429, "rate_limited", True),
        (503, "provider_error", True),
    ],
)
def test_failures_are_safe_and_never_automatically_retried(status, code, retry):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="private-test-key confidential source")

    with pytest.raises(ProviderFailure) as error:
        call(handler)
    assert error.value.code == code
    assert error.value.retryable == retry
    assert error.value.reservation_releasable == (status in {401, 402, 429})
    assert len(requests) == 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize("status,released", [(400, True), (422, True), (500, False), (503, False)])
def test_rejection_and_ambiguous_server_error_have_distinct_billing(status, released):
    with pytest.raises(ProviderFailure) as error:
        call(lambda request: httpx.Response(status, text="untrusted private body"))
    assert error.value.reservation_releasable is released


@pytest.mark.parametrize(
    "exception,released",
    [
        (httpx.ConnectError, True),
        (httpx.ConnectTimeout, True),
        (httpx.PoolTimeout, True),
        (httpx.ReadTimeout, False),
        (httpx.WriteError, False),
    ],
)
def test_network_failure_only_releases_before_request_delivery(exception, released):
    def handler(request):
        raise exception("private transport details", request=request)

    with pytest.raises(ProviderFailure) as error:
        call(handler)
    assert error.value.reservation_releasable is released
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [
        response("not JSON"),
        response(finish="length"),
        {},
        response(json.dumps(facts(deadline_at="2026-10-06T12:00:00+03:00"))),
    ],
)
def test_malformed_truncated_and_non_midnight_date_only_rejected(body):
    with pytest.raises(ProviderFailure):
        call(lambda request: httpx.Response(200, json=body))


@pytest.mark.parametrize("body", [response("not JSON"), response(finish="length")])
def test_failed_model_output_preserves_reported_usage(body):
    with pytest.raises(ProviderFailure) as error:
        call(lambda request: httpx.Response(200, json=body))
    assert error.value.usage.prompt_tokens == 300
    assert error.value.usage.completion_tokens == 100


def test_large_message_never_reaches_provider():
    def forbidden(request):
        pytest.fail("should not make a paid request")

    with pytest.raises(ProviderFailure, match="context_too_large"):
        call(forbidden, "x" * 12001)


def test_key_is_redacted_and_arbitrary_host_rejected():
    assert "private-test-key" not in repr(Settings(deepseek_api_key="private-test-key"))
    with pytest.raises(ProviderFailure, match="invalid_provider_url"):
        DeepSeekProvider("private-test-key", base_url="https://untrusted.example")
