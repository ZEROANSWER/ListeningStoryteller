import pytest

from core import (
    _normalize_speaker_type,
    _parse_json_object,
    find_best_story,
)


def test_parse_json_object_accepts_markdown_fence():
    result = _parse_json_object('```json\n{"intent":"stop"}\n```')
    assert result == {"intent": "stop"}


def test_parse_json_object_rejects_non_json():
    with pytest.raises(ValueError):
        _parse_json_object("没有 JSON")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("儿童", "儿童"), ("'成人'", "成人"), ("判断结果：儿童。", "儿童")],
)
def test_normalize_speaker_type(raw, expected):
    assert _normalize_speaker_type(raw) == expected


def test_normalize_speaker_type_rejects_unknown_value():
    with pytest.raises(ValueError):
        _normalize_speaker_type("无法判断")


def test_find_best_story_uses_highest_overlap():
    stories = [
        {"title": "A", "tags": ["勇气"]},
        {"title": "B", "tags": ["勇气", "冒险"]},
    ]
    assert find_best_story(stories, ["勇气", "冒险"])["title"] == "B"


def test_find_best_story_has_safe_generic_fallback():
    stories = [
        {"title": "普通", "tags": ["规则"]},
        {"title": "通用", "tags": ["成长"]},
    ]
    assert find_best_story(stories, ["不存在的标签"])["title"] == "通用"
