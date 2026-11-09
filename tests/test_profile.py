"""称呼(user profile)存取:round-trip、改一次不留旧条目、可清空、不误删别的偏好。

用 stub 宿主直接测 mixin,不初始化 Agent / API(那条路径由 test_api 覆盖)。
"""

from __future__ import annotations

from foxclaw.agents.mixins.preference import PreferenceMixin


class _Stub(PreferenceMixin):
    AGENT_ID = "stub"

    def __init__(self):
        self.items: list[dict] = []
        self._seq = 0

    def _store_memory(self, content, mem_type="concept", **kwargs):
        self._seq += 1
        self.items.insert(0, {"id": f"m{self._seq}", "content": content})

    def list_preferences(self, limit=50):
        return self.items[:limit]

    def delete_preference(self, pref_id):
        before = len(self.items)
        self.items = [p for p in self.items if p["id"] != pref_id]
        return len(self.items) < before


def test_nickname_roundtrip():
    s = _Stub()
    assert s.get_nickname() == ""
    assert s.set_nickname("  snow  ") == "snow"
    assert s.get_nickname() == "snow"


def test_set_nickname_replaces_old():
    """改称呼不能留下两条互相矛盾的偏好。"""
    s = _Stub()
    s.set_nickname("snow")
    s.set_nickname("snow2")
    assert [s._nick_of(p["content"]) for p in s.items] == ["snow2"]
    assert s.get_nickname() == "snow2"


def test_clear_nickname():
    s = _Stub()
    s.set_nickname("snow")
    assert s.set_nickname("") == ""
    assert s.get_nickname() == ""
    assert s.items == []


def test_other_preferences_untouched():
    s = _Stub()
    s.remember_preference("回答尽量简短")
    s.set_nickname("snow")
    contents = [p["content"] for p in s.items]
    assert "用户偏好:回答尽量简短" in contents
    assert "用户偏好:称呼我为 snow" in contents


def test_nickname_truncated():
    s = _Stub()
    long = "x" * 60
    assert s.set_nickname(long) == long[:24]
