"""Reading a model's reply.

Models wrap JSON in prose, in code fences, and — on a page holding forty table rows
— simply stop mid-object when they hit the token limit. A truncated reply still
contains most of a page's facts, so the last broken object is dropped rather than
the whole page.
"""

from __future__ import annotations

import pytest

from factlayer.llm import LLMOutputError, _parse_json


def test_plain_json_is_read():
    assert _parse_json('[{"a": 1}]') == [{"a": 1}]


def test_code_fences_are_stripped():
    assert _parse_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert _parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_prose_around_the_json_is_ignored():
    assert _parse_json('Here are the facts:\n[{"a": 1}]\nHope that helps.') == [{"a": 1}]


def test_a_reply_cut_off_mid_object_keeps_the_complete_ones():
    """The token limit lands in the middle of the fortieth table row. The first
    thirty-nine are still good."""
    reply = '[{"a": 1}, {"b": 2}, {"c":'
    assert _parse_json(reply) == [{"a": 1}, {"b": 2}]


def test_a_reply_cut_off_between_objects_keeps_the_complete_ones():
    assert _parse_json('[{"a": 1}, {"b": 2},') == [{"a": 1}, {"b": 2}]


def test_a_truncated_reply_with_nested_objects_is_salvaged():
    reply = '[{"a": 1, "scope": {"x": "y"}}, {"b": 2, "scope": {"z":'
    assert _parse_json(reply) == [{"a": 1, "scope": {"x": "y"}}]


def test_a_truncated_reply_with_no_complete_object_is_an_error():
    with pytest.raises(LLMOutputError):
        _parse_json('[{"a":')


@pytest.mark.parametrize("reply", ["", "   ", "I cannot help with that.", "null-ish"])
def test_unusable_replies_raise_rather_than_returning_nothing(reply):
    """Returning an empty list here would silently record 'this page has no facts'."""
    with pytest.raises(LLMOutputError):
        _parse_json(reply)


def test_an_empty_array_is_a_real_answer():
    assert _parse_json("[]") == []
