"""
会话存储回归测试(全离线)。

覆盖:建/列/读/追加/改名/删、标题自动提炼、按更新时间倒序、
列表不含 messages(负载)、路径穿越防护、并发追加(对话跑在后台线程)。
"""
import sys, tempfile, shutil, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.sessions import SessionStore, derive_title
import pytest


@pytest.fixture()
def store():
    d = tempfile.mkdtemp(prefix="sess_")
    try:
        yield SessionStore(Path(d) / "sessions")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_create_list_get(store):
    s = store.create()
    assert s["id"] and s["title"] == "新会话" and s["message_count"] == 0
    assert [x["id"] for x in store.list()] == [s["id"]]
    assert store.get(s["id"])["messages"] == []


def test_append_and_title_derivation(store):
    s = store.create()
    assert store.append(s["id"], "user", "帮我分析本月支出情况。还有别的")
    assert store.append(s["id"], "assistant", "好的,本月支出如下…")
    d = store.get(s["id"])
    assert len(d["messages"]) == 2
    assert d["messages"][0]["role"] == "user"
    # 标题从首条用户消息提炼(截到句读)
    assert d["title"] == "帮我分析本月支出情况"


def test_append_rejects_bad_role_and_missing_session(store):
    s = store.create()
    assert store.append(s["id"], "system", "x") is False   # 非法角色
    assert store.append(s["id"], "user", "") is False      # 空内容
    assert store.append("deadbeef1234", "user", "x") is False   # 会话不存在


def test_list_is_light_and_sorted(store):
    a = store.create(); store.append(a["id"], "user", "第一个会话")
    b = store.create(); store.append(b["id"], "user", "第二个会话")
    lst = store.list()
    assert lst[0]["id"] == b["id"], "应按 updated_at 倒序"
    assert "messages" not in lst[0], "列表不得带 messages(负载)"
    assert lst[0]["message_count"] == 1 and lst[0]["last"] == "第二个会话"


def test_rename_and_delete(store):
    s = store.create()
    assert store.rename(s["id"], "季度复盘")
    assert store.get(s["id"])["title"] == "季度复盘"
    assert store.delete(s["id"])
    assert store.get(s["id"]) is None
    assert store.delete(s["id"]) is False


def test_path_traversal_blocked(store):
    """id 只允许安全字符,杜绝 ../ 路径穿越。"""
    assert store.get("../../etc/passwd") is None
    assert store.delete("../../x") is False
    assert store.append("../../x", "user", "hi") is False


def test_concurrent_append_no_loss(store):
    """对话跑在后台线程,追加必须线程安全、不丢消息。"""
    s = store.create()
    def w(i):
        store.append(s["id"], "user", f"消息{i}")
    ts = [threading.Thread(target=w, args=(i,)) for i in range(20)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(store.get(s["id"])["messages"]) == 20


def test_derive_title():
    assert derive_title("你好呀！今天天气") == "你好呀"
    assert derive_title("") == "新会话"
    assert len(derive_title("长" * 100)) <= 24


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
