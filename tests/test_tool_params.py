"""M4 · 工具入参校验 + permission Stage 1 真正生效(回归)。

病根(三条断链):
  1. tools.py 构造了完整 JSON Schema,但**只发给模型**,执行侧从不校验;
  2. base._dispatch_tool 构造 permission spec 时传 `params={}`(恒空)
     → permission 管线 Stage 1 的必填校验**永不触发**;
  3. model_router 解析 tool_calls 参数失败时静默替换为 `{}`
     → 模型给了非法 JSON 也不报错,它以为工具已执行。

本文件锁定:声明的 schema 在执行侧真正生效,且失败对模型可见。
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.tools import default_registry, tool_capability
from foxclaw.skills.permission import evaluate


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 60)
    print("M4 · 工具入参校验")
    print("=" * 60)
    r = default_registry()

    print("\n【1】schema 可从注册表取到(执行侧与模型侧同一份真源)")
    sch = r.param_schema("web_search")
    check(bool(sch), "web_search 有 parameters schema")
    check(sch.get("required") == ["query"], f"required 声明正确({sch.get('required')})")
    check(bool(r.param_schema("不存在的工具") == {}), "未知工具返回空 schema(不抛异常)")

    print("\n【2】必填参数缺失被检出")
    errs = r.validate_args("web_search", {})
    check(any("query" in e for e in errs), f"缺 query 被检出({errs})")
    errs = r.validate_args("web_search", {"query": ""})
    check(any("query" in e for e in errs), f"空字符串 query 也算缺失({errs})")

    print("\n【3】类型不符被检出(C 类:静默失败改为可见)")
    errs = r.validate_args("web_search", {"query": {"a": 1}})
    check(any("类型" in e for e in errs), f"dict 传 string 位被检出({errs})")
    errs = r.validate_args("recall_memory", {"query": "x", "top_k": "ten"})
    check(any("类型" in e for e in errs), f"字符串传 integer 被检出({errs})")
    errs = r.validate_args("recall_memory", {"query": "x", "top_k": True})
    check(not any("top_k" in e for e in errs),
          f"bool 不被误判为 integer({errs})")
    check(r.validate_args("web_search", {"query": "ok", "未声明字段": 1}) == [],
          "未声明的额外字段不报错(避免过度约束模型)")

    print("\n【4】合法入参通过(不误拦)")
    check(r.validate_args("web_search", {"query": "今天天气"}) == [],
          "合法 query 无错误")
    check(r.validate_args("recall_memory", {"query": "x", "top_k": 8}) == [],
          "合法 top_k 无错误")

    print("\n【5】permission Stage 1 真正生效(此前 params={} 恒不触发)")
    for tool, args, want in [("web_search", {}, "ask"),
                             ("recall_memory", {"query": "x"}, "allow")]:
        sch_t = r.param_schema(tool)
        req = set(sch_t.get("required") or [])
        params = {k: {"required": k in req, "type": v.get("type")}
                  for k, v in (sch_t.get("properties") or {}).items()
                  if isinstance(v, dict)}
        spec = types.SimpleNamespace(id=f"tool:{tool}", params=params,
                                     **tool_capability(tool))
        d = evaluate(spec, args, confirmed=False)
        check(d.decision == want,
              f"{tool} args={args} → {d.decision}(期望 {want})")
        if args == {}:
            check("缺少必填参数" in d.reason,
                  f"Stage 1 检出缺参:{d.reason}")

    print("\n【6】参数解析失败不再静默(args_parse_failed 标记存在)")
    import inspect
    from foxclaw.core.model_router import ModelRouter
    src = inspect.getsource(ModelRouter)
    check("args_parse_failed" in src,
          "call_tools 标记解析失败(可观测)")
    check("llm.tool_args_unparsable" in src,
          "解析失败进 tracing(可归因)")

    print("\n" + "=" * 60)
    print("M4 全部通过")
    print("=" * 60)


def test_tool_params():
    main()


if __name__ == "__main__":
    main()
