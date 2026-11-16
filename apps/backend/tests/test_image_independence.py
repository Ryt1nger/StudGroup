"""P1-1: independent academic text does not wait for optional image evidence."""

from studgroup.analysis.image_independence import independently_actionable_text


def test_explicit_full_event_can_be_published_without_photo():
    assert independently_actionable_text(
        "Контрольная по истории состоится в пятницу 20 ноября в 10:00, "
        "аудитория 305. Подготовить лекции 2 и 3."
    )


def test_caption_and_reply_wait_for_image():
    assert not independently_actionable_text("По математике решить задачи 1–3")
    assert not independently_actionable_text(
        "Контрольная в пятницу в 10:00: задания на фото, всё в приложении."
    )
    assert not independently_actionable_text(
        "Контрольная по истории в пятницу в 10:00 в аудитории 305.",
        reply_to=432,
    )
    assert not independently_actionable_text(
        "Сегодня сдать задачи 1–3 по математике, как в прикреплённом файле"
    )


def test_chatter_and_time_without_academic_action_never_release():
    assert not independently_actionable_text("Когда сегодня после обеда встретимся в парке?")
    assert not independently_actionable_text("Пары по истории всё ещё актуальны?")
