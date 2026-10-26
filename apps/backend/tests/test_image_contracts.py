import pytest
from pydantic import ValidationError

from studgroup.analysis.image_contracts import ImageEvent, ScheduleRow, VisualReading


def test_reading_keeps_long_text_and_rejects_unknown_fields():
    value = {"summary": "Лист", "extracted_text": "я" * 10000, "complete": True}
    assert len(VisualReading.model_validate(value).extracted_text) == 10000
    with pytest.raises(ValidationError):
        VisualReading.model_validate(value | {"invented": "ignored?"})
    with pytest.raises(ValidationError):
        VisualReading.model_validate(value | {"semantic_types": ["new_category"]})


def test_schedule_and_event_mutations_are_strict():
    with pytest.raises(ValidationError):
        ScheduleRow.model_validate_json(
            '{"weekday":0,"starts":"12:00","ends":"11:00","subject":"Алгебра"}'
        )
    with pytest.raises(ValidationError):
        ImageEvent(title="Афиша", action="cancel")
