"""Independent current-series evidence, never peer propagation or stale links."""

from types import SimpleNamespace

from studgroup.ranepa_group_links import agreed_links, lesson_key


def row(**updates):
    result = {
        "day": "2026-11-24",
        "starts": "09:00:00",
        "ends": "10:20:00",
        "subject": "История России",
        "teacher": "Иванов Иван Иванович",
        "room": "СДО",
        "lesson_type": "Лекционные занятия",
        "subgroup_hint": "Э-ОБ26-СГЯ-лек(43,44,45)",
        "online_url": "https://my.mts-link.ru/j/Ranepa/123",
        "link_state": "matched",
        "direct_link_run": "series",
    }
    result.update(updates)
    return result


def test_two_distinct_fresh_accounts_agree_on_exact_occurrence():
    target = row()
    sources = [("a", [row()]), ("b", [row()])]
    assert agreed_links([target], sources, [], "series") == {
        lesson_key(target): target["online_url"]
    }
    assert not agreed_links([target], sources[:1], [], "series")
    assert not agreed_links([target], [sources[0], sources[0]], [], "series")
    assert not agreed_links([target], sources, [], "old-series")
    assert not agreed_links([row(teacher="Петров Петр Петрович")], sources, [], "series")
    assert not agreed_links([row(subgroup_hint="другой поток")], sources, [], "series")
    assert not agreed_links(
        [target], sources, [SimpleNamespace(subject_name="История России")], "series"
    )


def test_inherited_or_disagreeing_links_cannot_vote():
    target = row()
    assert not agreed_links(
        [target], [("a", [row()]), ("b", [row(source_scope="common")])], [], "series"
    )
    assert not agreed_links(
        [target],
        [("a", [row()]), ("b", [row(online_url="https://my.mts-link.ru/j/Ranepa/456")])],
        [],
        "series",
    )
    assert not agreed_links(
        [target],
        [("a", [row()]), ("b", [row()]), ("c", [row(direct_link_run="old")])],
        [],
        "new",
    )
