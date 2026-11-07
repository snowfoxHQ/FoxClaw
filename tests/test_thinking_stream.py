"""推理过程可见:`_ThinkStripper` 不再丢弃思考,原生 reasoning 走独立 thinking 通道。

背景:`core/model_router.py` 的 `_ThinkStripper.feed` 只返回可见正文,think 块内容被整段
淘汰;openai-compatible 的 `delta.reasoning_content`(deepseek-reasoner 等)更是完全没读,
用户只看到结论,看不到推理。现在两者都转成 `on_thinking` 回调 → SSE `event: thinking`。
"""

from __future__ import annotations

from types import SimpleNamespace

from foxclaw.core.model_router import ModelRouter, _ThinkStripper


class _FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


def _chunk(content=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _router(chunks) -> ModelRouter:
    r = ModelRouter(provider="openai", model="gpt-4o-mini")
    r._client = _FakeClient(chunks)
    return r


def test_stripper_returns_think_text_separately() -> None:
    s = _ThinkStripper()
    vis1, th1 = s.feed_events("答案前<thi")
    assert (vis1, th1) == ("答案前", ""), "半个标签不能当正文吐出"
    vis2, th2 = s.feed_events("nk>内部推理</think>答案")
    assert vis2 == "答案"
    assert th2 == "内部推理"


def test_stripper_feed_still_visible_only() -> None:
    s = _ThinkStripper()
    assert s.feed("a<think>b</think>c") == "ac"


def test_reasoning_content_goes_to_thinking_only() -> None:
    r = _router([_chunk(None, "先想一下"), _chunk("你好"), _chunk("世界")])
    think: list[str] = []
    text = "".join(r.stream("sys", [{"role": "user", "content": "hi"}],
                            on_thinking=think.append))
    assert "".join(think) == "先想一下", "原生 reasoning_content 必须进推理通道"
    assert text == "你好世界", "正文不得混入推理"


def test_embedded_think_routed_to_callback() -> None:
    r = _router([_chunk("<think>内嵌思考</think>答案是 42<think>补一句")])
    think: list[str] = []
    text = "".join(r.stream("sys", [{"role": "user", "content": "hi"}],
                            on_thinking=think.append))
    assert text == "答案是 42"
    assert "内嵌思考" in "".join(think)
    assert "补一句" not in text, "未闭合的 think 尾巴不得漏进正文"


def test_no_callback_keeps_old_behavior() -> None:
    r = _router([_chunk("<think>x</think>ok")])
    assert "".join(r.stream("sys", [{"role": "user", "content": "hi"}])) == "ok"
