"""本地密钥存储:keyring 优先、base64 回退、改名/删除不留孤儿。

不碰真 OS 凭据库(测试机器上可能没有后端,也可能有用户真实数据),
用 fake 替换 `secrets._keyring`。
"""

import pytest

from foxclaw.core import secrets


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise KeyError(user)
        del self.store[(service, user)]


@pytest.fixture
def fake_kr(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: kr)
    return kr


@pytest.fixture
def no_kr(monkeypatch):
    monkeypatch.setattr(secrets, "_keyring", lambda: None)


def test_store_takes_over_when_keyring_available(fake_kr):
    assert secrets.store("m1", "sk-abc") is True
    assert fake_kr.store[(secrets.SERVICE, "m1")] == "sk-abc"


def test_store_reports_failure_without_keyring(no_kr):
    assert secrets.store("m1", "sk-abc") is False


def test_load_prefers_keyring_over_legacy_ciphertext(fake_kr):
    fake_kr.store[(secrets.SERVICE, "m1")] = "sk-new"
    assert secrets.load("m1", secrets.obf("sk-old")) == "sk-new"


def test_load_falls_back_to_legacy_ciphertext(no_kr):
    assert secrets.load("m1", secrets.obf("sk-old")) == "sk-old"


def test_load_returns_empty_when_nothing_stored(no_kr):
    assert secrets.load("m1") == ""


def test_rename_moves_credential(fake_kr):
    """改名不搬迁 → load(新名) 取不到,key 静默丢失。"""
    secrets.store("old", "sk-abc")
    assert secrets.rename("old", "new") is True
    assert secrets.load("new") == "sk-abc"
    assert fake_kr.get_password(secrets.SERVICE, "old") is None


def test_rename_without_source_is_noop(fake_kr):
    assert secrets.rename("missing", "new") is False


def test_delete_is_idempotent(fake_kr):
    secrets.store("m1", "sk-abc")
    secrets.delete("m1")
    secrets.delete("m1")
    assert secrets.load("m1") == ""


def test_delete_without_keyring_does_not_raise(no_kr):
    secrets.delete("m1")


def test_save_key_keeps_only_marker_when_keyring_available(fake_kr):
    """有 OS 凭据库时,JSON 里不能再有任何可还原的密文。"""
    from foxclaw.api.routes.llm_admin import _save_key

    fields = _save_key("m1", "  sk-abc  ")
    assert fields == {"api_key_kr": True, "api_key_obf": ""}
    assert fake_kr.store[(secrets.SERVICE, "m1")] == "sk-abc"


def test_save_key_falls_back_to_ciphertext(no_kr):
    from foxclaw.api.routes.llm_admin import _save_key

    fields = _save_key("m1", "sk-abc")
    assert fields["api_key_kr"] is False
    assert secrets.deobf(fields["api_key_obf"]) == "sk-abc"


def test_save_key_empty_input_clears_both(no_kr):
    from foxclaw.api.routes.llm_admin import _save_key

    assert _save_key("m1", "") == {"api_key_kr": False, "api_key_obf": ""}


@pytest.mark.parametrize("record,expected", [
    ({"api_key_kr": True, "api_key_obf": ""}, True),
    ({"api_key_kr": False, "api_key_obf": "c2stYWJj"}, True),
    ({"api_key": "sk-legacy"}, True),
    ({"api_key_kr": False, "api_key_obf": ""}, False),
    ({}, False),
])
def test_has_key(record, expected):
    from foxclaw.api.routes.llm_admin import _has_key

    assert _has_key(record) is expected


def test_obf_is_reversible(no_kr):
    """base64 只是混淆,不要在任何地方当加密用 —— 这条测试的作用是钉住这一点。"""
    import base64

    assert secrets.deobf(secrets.obf("sk-secret")) == "sk-secret"
    assert base64.b64decode(secrets.obf("sk-secret")).decode() == "sk-secret"


def test_backend_probe_is_cached(monkeypatch):
    import sys
    import types

    calls = []

    class _Fail:
        pass

    fake = types.ModuleType("keyring")
    fake.get_keyring = lambda: calls.append(1) or _Fail()
    backends = types.ModuleType("keyring.backends")
    fail_mod = types.ModuleType("keyring.backends.fail")
    fail_mod.Keyring = _Fail
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_mod)

    secrets.reset_probe()
    try:
        assert secrets.available() is False
        secrets.available()
        assert len(calls) == 1
    finally:
        secrets.reset_probe()
