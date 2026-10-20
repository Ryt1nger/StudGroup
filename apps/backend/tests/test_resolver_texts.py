"""An update never replaces a real card title by one the editor rebuilt from the update's fragments."""

from types import SimpleNamespace

from studgroup.analysis.card_editor import EditedCard
from studgroup.analysis.resolver import _kept_texts
from studgroup.analysis.title_builder import INSUFFICIENT


def edited(title, *, fallback=("title",), reasons=()):
    return EditedCard(
        title=title,
        description="Описание.",
        visibility="headman_attention",
        attention_reasons=list(reasons),
        fallback_fields=list(fallback),
    )


def card(title):
    return SimpleNamespace(title=title, description="Старое описание.")


def test_a_rebuilt_title_does_not_replace_the_real_title_of_the_card():
    title, _ = _kept_texts(
        card("Эссе по российской идентичности"), edited("Быть минимум 2 источника")
    )
    assert title == "Эссе по российской идентичности"


def test_a_placeholder_title_is_replaced_by_the_rebuilt_one():
    title, _ = _kept_texts(card("Эссе" + INSUFFICIENT), edited("Эссе объёмом 1500 слов"))
    assert title == "Эссе объёмом 1500 слов"


def test_a_title_the_editor_kept_as_it_was_is_applied():
    title, _ = _kept_texts(card("Старое название"), edited("Новое название", fallback=()))
    assert title == "Новое название"
