from pathlib import Path

import pytest
from ai_corpus import CorpusEntry, load_corpus
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures" / "ai_analysis"


def test_synthetic_ai_corpus_is_valid():
    entries = load_corpus(FIXTURES / "synthetic.jsonl")
    assert len(entries) == 1
    assert [item.destination for item in entries[0].expected_items] == ["task", "deadline"]


def test_ai_corpus_rejects_missing_evidence_source():
    with pytest.raises(ValidationError, match="missing source revision"):
        CorpusEntry.model_validate(
            {
                "case_id": "missing-evidence",
                "split": "development",
                "discussion_id": "discussion-1",
                "sources": [
                    {
                        "source_id": 1,
                        "revision": 1,
                        "message_time": "2026-10-12T09:00:00Z",
                        "text": "Решить задачу 1",
                    }
                ],
                "expected_items": [
                    {
                        "entity_id": "task-1",
                        "destination": "task",
                        "action": "create",
                        "subject": None,
                        "content": "Решить задачу 1",
                        "date_basis": "unknown",
                        "evidence": [{"field": "content", "source_id": 2, "revision": 1}],
                    }
                ],
            }
        )


def test_ai_corpus_rejects_unexplained_negative_case():
    with pytest.raises(ValidationError, match="negative_reason"):
        CorpusEntry.model_validate(
            {
                "case_id": "negative",
                "split": "evaluation",
                "discussion_id": "discussion-2",
                "sources": [
                    {
                        "source_id": 1,
                        "revision": 1,
                        "message_time": "2026-10-12T09:00:00Z",
                        "text": "Привет",
                    }
                ],
                "expected_items": [],
            }
        )


def test_ai_corpus_rejects_discussion_split_leakage(tmp_path):
    fixture = FIXTURES / "synthetic.jsonl"
    development = fixture.read_text(encoding="utf-8").strip()
    evaluation = development.replace(
        '"case_id":"synthetic-multiple-items","split":"development"',
        '"case_id":"synthetic-evaluation","split":"evaluation"',
    ).replace("synthetic-task-1", "synthetic-task-2").replace(
        "synthetic-deadline-1", "synthetic-deadline-2"
    )
    path = tmp_path / "leaked.jsonl"
    path.write_text(f"{development}\n{evaluation}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="discussion split leakage"):
        load_corpus(path)
