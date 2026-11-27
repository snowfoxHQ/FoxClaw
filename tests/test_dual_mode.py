"""M3 双模执行路由与 Serve MCP 安全边界测试。"""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from foxclaw.skills import SkillAdapter, SkillRegistry, SkillSpec
from foxclaw.skills.action import browser_act
from foxclaw.skills.desktop import desktop_act
from foxclaw.skills.executor import GovernanceExecutor, ServeCategory


ROOT = Path(__file__).resolve().parents[1]


class FakeServe:
    def __init__(self, health="ok", submit=None, fail_tools=None):
        self.health_status = health
        self.submit_result = submit or {
            "contract_id": "contract-1", "status": "pending",
            "requires_approval": True,
        }
        self.fail_tools = set(fail_tools or ())
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        if name in self.fail_tools:
            raise TimeoutError("Bearer never-print-this-token")
        if name == "serve_health":
            return {"structured": {"status": self.health_status}}
        if name == "serve_submit_action":
            return {"structured": dict(self.submit_result)}
        if name == "serve_query_contract":
            return {"structured": {"contract_id": args["contract_id"], "status": "pending"}}
        if name == "serve_query_audit":
            return {"structured": {"contract_id": args["contract_id"], "status": "ok",
                                   "evidence": []}}
        if name == "serve_confirm_action":
            return {"structured": {"contract_id": args["contract_id"], "status": "succeeded"}}
        raise AssertionError(name)


class FakeGate:
    auditor = None

    def __init__(self, decision="allow"):
        self.decision = decision
        self.calls = 0

    def check_action(self, *_args, **_kwargs):
        self.calls += 1
        raw = SimpleNamespace(decision=self.decision, stage=1, reason="test policy",
                              trust_downgrade=False)
        return SimpleNamespace(raw=raw, level={"allow": "allow", "ask": "confirm",
                                               "deny": "deny"}[self.decision],
                               reason="test policy",
                               allowed=self.decision == "allow", decision_id="decision-1")


def _executor(mode, health="ok", client=None):
    client = client or FakeServe(health=health)
    executor = GovernanceExecutor(
        backend=mode, serve_url="https://serve.example/mcp", token="test-secret",
        client=client, start_monitor=False)
    executor.probe()
    return executor, client


def _adapter(executor, *, decision="allow", handler=None, side_effect="write",
             requires_audit=False):
    registry = SkillRegistry()
    skill = SkillSpec(
        id="send_report", name="send report", description="", side_effect=side_effect,
        handler=handler or (lambda **_kwargs: {"sent": True}),
        params={"recipient": "str", "body": "str"}, requires_audit=requires_audit)
    registry.register(skill)
    gate = FakeGate(decision)
    return SkillAdapter(registry, policy_gate=gate, executor=executor), skill, gate


@pytest.mark.parametrize(
    "mode,health,expected",
    [
        ("local", "ok", "local"),
        ("local", "degraded", "local"),
        ("local", "unreachable", "local"),
        ("serve", "ok", "serve"),
        ("serve", "degraded", "blocked"),
        ("serve", "unreachable", "blocked"),
        ("auto", "ok", "serve"),
        ("auto", "degraded", "local"),
        ("auto", "unreachable", "local"),
    ],
)
def test_write_routing_modes_and_health(mode, health, expected):
    executor, fake = _executor(mode, health=health)
    adapter, skill, _ = _adapter(executor)
    result = adapter.invoke(skill, {"recipient": "a", "body": "hello"})
    assert result.execution_backend == expected
    if expected == "serve":
        assert result.status == "needs_approval"
        assert result.message == "已提交审批（契约号：contract-1）"
        assert result.idempotency_key
        assert [name for name, _ in fake.calls].count("serve_submit_action") == 1
    elif expected == "blocked":
        assert not result.success
        assert not any(name == "serve_submit_action" for name, _ in fake.calls)
    else:
        assert result.success
        assert result.output["sent"] is True
        assert (result.execution_backend == "local")
        if mode == "auto" and health != "ok":
            assert executor.recent_degradation["status"] == health


@pytest.mark.parametrize("mode,health,expected", [
    ("local", "ok", "blocked"),
    ("serve", "ok", "serve"),
    ("serve", "degraded", "blocked"),
    ("auto", "ok", "serve"),
    ("auto", "unreachable", "blocked"),
])
def test_requires_audit_never_falls_back(mode, health, expected):
    executor, fake = _executor(mode, health=health)
    adapter, skill, _ = _adapter(executor, requires_audit=True)
    result = adapter.invoke(skill, {"recipient": "a", "body": "hello"})
    assert result.execution_backend == expected
    assert not (result.execution_backend == "local")
    assert (any(name == "serve_submit_action" for name, _ in fake.calls)
            is (expected == "serve"))


def test_policy_deny_precedes_backend_and_serve():
    executor, fake = _executor("serve")
    adapter, skill, gate = _adapter(executor, decision="deny")
    result = adapter.invoke(skill, {"recipient": "a", "body": "hello"})
    assert gate.calls == 1
    assert result.execution_backend == "blocked"
    assert not fake.calls or all(name != "serve_submit_action" for name, _ in fake.calls)


def test_skill_backend_precedence_and_audit_priority():
    executor, _ = _executor("auto")
    skill = SkillSpec(id="x", name="", description="", execution_backend="local")
    assert executor.route(skill).backend == "local"
    skill.execution_backend = "serve"
    assert executor.route(skill).backend == "serve"
    skill.execution_backend = "local"
    skill.requires_audit = True
    assert executor.route(skill).backend == "serve"


@pytest.mark.parametrize("mode,expected", [
    ("local", "local"), ("serve", "blocked"), ("auto", "local"),
])
def test_health_timeout_routes_fail_closed_or_auto_fallback(mode, expected):
    fake = FakeServe(fail_tools={"serve_health"})
    executor, _ = _executor(mode, client=fake)
    adapter, skill, _ = _adapter(executor)
    result = adapter.invoke(skill, {"recipient": "a", "body": "hello"})
    assert executor.health["status"] == "unreachable"
    assert result.execution_backend == expected


def test_serve_ask_is_delegated_and_contract_is_reported():
    executor, _ = _executor("serve")
    adapter, skill, _ = _adapter(executor, decision="ask")
    result = adapter.invoke(skill, {"recipient": "a", "body": "hello"})
    assert result.status == "needs_approval"
    assert result.contract_id == "contract-1"
    assert "已提交审批（契约号：contract-1）" == result.message


def test_read_only_skill_is_always_local():
    executor, fake = _executor("serve", health="degraded")
    called = []
    adapter, skill, _ = _adapter(
        executor, handler=lambda **_kwargs: called.append("local") or {"read": True},
        side_effect="read")
    result = adapter.invoke(skill, {"recipient": "a"})
    assert called == ["local"] and result.success
    assert not any(name != "serve_health" for name, _ in fake.calls)


@pytest.mark.parametrize("status,category", [
    ("pending", ServeCategory.ACCEPTED), ("deduped", ServeCategory.ACCEPTED),
    ("rejected", ServeCategory.REJECTED), ("not_found", ServeCategory.NOT_FOUND),
    ("expired", ServeCategory.EXPIRED),
    ("idempotency_conflict", ServeCategory.CONFLICT),
    ("args_conflict", ServeCategory.CONFLICT), ("unknown_arg", ServeCategory.INVALID),
    ("args_invalid", ServeCategory.INVALID),
    ("integrity_error", ServeCategory.INTEGRITY_FAILURE),
    ("executed_unaudited", ServeCategory.INTEGRITY_FAILURE),
    ("executed_state_unpersisted", ServeCategory.INTEGRITY_FAILURE),
    ("execution_state_unpersisted", ServeCategory.INTEGRITY_FAILURE),
    ("execution_unknown", ServeCategory.EXECUTION_UNCERTAIN),
    ("succeeded", ServeCategory.EXECUTED), ("verified", ServeCategory.EXECUTED),
    ("failed", ServeCategory.REJECTED), ("rolled_back", ServeCategory.REJECTED),
    ("aborted", ServeCategory.REJECTED), ("unauthenticated", ServeCategory.UNAUTHENTICATED),
])
def test_all_serve_statuses_have_categories(status, category):
    assert GovernanceExecutor._category(status) == category


def test_idempotent_retry_queries_known_contract_before_submit_again():
    executor, fake = _executor("serve")
    skill = SkillSpec(id="send_report", name="", description="", side_effect="write",
                      params={"recipient": "str"})
    first = executor.submit(skill, {"recipient": "a"}, "same-key")
    second = executor.submit(skill, {"recipient": "a"}, "same-key")
    assert first.contract_id == second.contract_id == "contract-1"
    assert second.idempotency_key == "same-key"
    assert [name for name, _ in fake.calls] == [
        "serve_health", "serve_submit_action", "serve_query_contract"]
    assert fake.calls[1][1]["idempotency_key"] == "same-key"


def test_expired_contract_gets_new_idempotency_key():
    class ExpiredThenPending(FakeServe):
        def __init__(self):
            super().__init__()
            self.submit_count = 0

        def call_tool(self, name, args):
            self.calls.append((name, dict(args)))
            if name == "serve_submit_action":
                self.submit_count += 1
                status = "expired" if self.submit_count == 1 else "pending"
                return {"structured": {"status": status, "contract_id": f"c{self.submit_count}",
                                       "requires_approval": True}}
            if name == "serve_health":
                return {"structured": {"status": "ok"}}
            raise AssertionError(name)

    fake = ExpiredThenPending()
    executor, _ = _executor("serve", client=fake)
    skill = SkillSpec(id="x", name="", description="", side_effect="write")
    result = executor.submit(skill, {})
    keys = [args["idempotency_key"] for name, args in fake.calls
            if name == "serve_submit_action"]
    assert result.status == "pending" and len(keys) == 2 and keys[0] != keys[1]


def test_timeout_is_not_blindly_retried_and_token_is_never_logged(caplog):
    fake = FakeServe(fail_tools={"serve_submit_action"})
    executor, _ = _executor("serve", client=fake)
    skill = SkillSpec(id="x", name="", description="", side_effect="write")
    with caplog.at_level(logging.DEBUG):
        result = executor.submit(skill, {})
        retry = executor.submit(skill, {}, idempotency_key=result.idempotency_key)
    assert result.category == ServeCategory.UNAVAILABLE
    assert retry.category == ServeCategory.UNAVAILABLE
    assert [name for name, _ in fake.calls].count("serve_submit_action") == 1
    assert "never-print-this-token" not in caplog.text
    assert "test-secret" not in caplog.text


def test_five_mcp_tools_use_bearer_and_never_send_client_identity(monkeypatch):
    import foxclaw.skills.mcp_client as mcp_client

    calls = []
    def fake_call(url, headers, name, args, *, timeout):
        calls.append((url, dict(headers), name, dict(args)))
        return {"structured": {"status": "ok"}}
    monkeypatch.setattr(mcp_client, "call_streamable_http_tool", fake_call)
    executor = GovernanceExecutor(
        backend="serve", serve_url="https://serve.example/mcp",
        token="secret-token", start_monitor=False)
    executor.probe()
    executor.submit(SkillSpec(
        id="send_report", name="", description="", side_effect="write",
        params={"body": "str"}), {"body": "safe"})
    executor.query_contract("c-1")
    executor.confirm_action("c-1", "approve")
    executor.query_audit("c-1")
    assert {call[2] for call in calls} == {
        "serve_health", "serve_submit_action", "serve_query_contract",
        "serve_confirm_action", "serve_query_audit"}
    for _, headers, _, args in calls:
        assert headers == {"Authorization": "Bearer secret-token"}
        assert not {"tenant_id", "principal", "approver"}.intersection(args)
    assert calls[1][3].keys() == {"action_intent", "args", "idempotency_key"}
    assert calls[2][3] == {"contract_id": "c-1"}
    assert calls[3][3] == {"contract_id": "c-1", "decision": "approve"}
    assert calls[4][3] == {"contract_id": "c-1"}


def test_query_response_preserves_server_redaction():
    class RedactedServe(FakeServe):
        def call_tool(self, name, args):
            self.calls.append((name, dict(args)))
            if name == "serve_query_contract":
                return {"structured": {
                    "contract_id": args["contract_id"], "status": "pending",
                    "steps": [{"args": {"password": "***"},
                               "redacted_fields": ["args.password"]}],
                    "redacted_fields": ["steps[0].args.password"],
                }}
            return super().call_tool(name, args)

    executor, _ = _executor("serve", client=RedactedServe())
    response = executor.query_contract("c-1").data
    assert response["steps"][0]["args"]["password"] == "***"
    assert response["steps"][0]["redacted_fields"] == ["args.password"]
    assert response["redacted_fields"] == ["steps[0].args.password"]


def test_args_sent_to_serve_are_only_declared_operation_parameters():
    executor, fake = _executor("serve")
    skill = SkillSpec(id="send_report", name="", description="", side_effect="write",
                      params={"properties": {"recipient": {"type": "string"},
                                             "body": {"type": "string"}}})
    executor.submit(skill, {
        "recipient": "a", "body": "report", "memory": ["private memory"],
        "context": "conversation", "upstream_outputs": {"prior": "context"},
    })
    submitted = next(args for name, args in fake.calls if name == "serve_submit_action")
    assert submitted["args"] == {"recipient": "a", "body": "report"}
    assert set(submitted) == {"action_intent", "args", "idempotency_key"}


def test_browser_and_desktop_write_submit_to_serve_and_reads_stay_local():
    executor, fake = _executor("serve")
    gate = FakeGate()
    act_calls = []
    async def browser_impl(args):
        act_calls.append(("browser", args))
        return {"ok": True}
    async def desktop_impl(action, args):
        act_calls.append(("desktop", action, args))
        return {"ok": True}

    served_browser = asyncio.run(browser_act(
        gate, browser_impl, "click", {"target": "5"}, confirmed=True, executor=executor))
    served_desktop = asyncio.run(desktop_act(
        gate, desktop_impl, "mouse:click", {"x": 1}, confirmed=True, executor=executor))
    assert served_browser["execution_backend"] == served_desktop["execution_backend"] == "serve"
    assert served_browser["status"] == served_desktop["status"] == "needs_approval"
    assert served_browser["contract_id"] == served_desktop["contract_id"] == "contract-1"
    assert served_browser["denied"] is False and served_desktop["denied"] is False
    assert served_browser["decision_id"] and served_desktop["decision_id"]
    # 本地通道未执行
    assert not act_calls
    submitted = [args for name, args in fake.calls if name == "serve_submit_action"]
    assert len(submitted) == 2
    # 边界动作参数整体透传(授权已按完整参数完成);意图用规范化 id
    assert {"target": "5"} in [s["args"] for s in submitted]
    assert {"x": 1} in [s["args"] for s in submitted]
    intents = {s["action_intent"] for s in submitted}
    assert intents == {"browser:click", "desktop:mouse:click"}

    # read 动作不路由,继续本地执行
    read_browser = asyncio.run(browser_act(
        gate, browser_impl, "scroll", {"type": "scroll"}, executor=executor))
    read_desktop = asyncio.run(desktop_act(
        gate, desktop_impl, "clipboard:read", {}, executor=executor))
    assert read_browser["execution_backend"] == read_desktop["execution_backend"] == "local"
    assert len(act_calls) == 2
    assert [name for name, _ in fake.calls].count("serve_submit_action") == 2


def test_desktop_browser_serve_unhealthy_fail_closed():
    executor, fake = _executor("serve", health="degraded")
    gate = FakeGate()
    async def browser_impl(args):
        raise AssertionError("Serve 不健康时不应本地执行")
    async def desktop_impl(action, args):
        raise AssertionError("Serve 不健康时不应本地执行")
    br = asyncio.run(browser_act(
        gate, browser_impl, "click", {"target": "5"}, confirmed=True, executor=executor))
    dt = asyncio.run(desktop_act(
        gate, desktop_impl, "mouse:click", {"x": 1}, confirmed=True, executor=executor))
    assert br["execution_backend"] == dt["execution_backend"] == "blocked"
    assert "fail-closed" in br["reason"] and "fail-closed" in dt["reason"]
    assert not any(name == "serve_submit_action" for name, _ in fake.calls)


def test_policy_deny_precedes_serve_for_desktop_browser():
    executor, fake = _executor("serve")
    async def impl(*_a, **_kw):
        raise AssertionError("deny 不应执行")
    br = asyncio.run(browser_act(FakeGate("deny"), impl, "click", {"target": "5"},
                                 confirmed=True, executor=executor))
    dt = asyncio.run(desktop_act(FakeGate("deny"), impl, "mouse:click", {"x": 1},
                                 confirmed=True, executor=executor))
    assert br["denied"] is True and dt["denied"] is True
    assert br["level"] == dt["level"] == "deny"
    assert not any(name == "serve_submit_action" for name, _ in fake.calls)


def test_serve_path_confirmation_is_the_serve_contract_not_local_nonce():
    """ask/confirm 语义在 serve 路径由 Serve 契约审批承担,不走本地 nonce。"""
    executor, fake = _executor("serve")
    async def impl(*_a, **_kw):
        raise AssertionError("serve 路径不应本地执行")
    r = asyncio.run(browser_act(FakeGate("ask"), impl, "click", {"target": "5"},
                                executor=executor))
    assert r["execution_backend"] == "serve" and r["status"] == "needs_approval"
    assert "confirm_nonce" not in r
    assert any(name == "serve_submit_action" for name, _ in fake.calls)


def test_desktop_browser_auto_fallback_only_when_serve_unavailable():
    act_calls = []
    async def browser_impl(args):
        act_calls.append(("browser", args))
        return {"ok": True}
    async def desktop_impl(action, args):
        act_calls.append(("desktop", action, args))
        return {"ok": True}
    for health, expected in (("ok", "serve"), ("degraded", "local")):
        executor, fake = _executor("auto", health=health)
        br = asyncio.run(browser_act(
            FakeGate(), browser_impl, "click", {"target": "5"},
            confirmed=True, executor=executor))
        dt = asyncio.run(desktop_act(
            FakeGate(), desktop_impl, "mouse:click", {"x": 1},
            confirmed=True, executor=executor))
        assert br["execution_backend"] == dt["execution_backend"] == expected
    # ok→serve(不落地),degraded→local(落地两笔)
    assert len(act_calls) == 2

def test_write_entrypoints_are_routed_and_api_paths_use_the_wrappers():
    skill_source = (ROOT / "foxclaw" / "skills" / "__init__.py").read_text(encoding="utf-8")
    action_source = (ROOT / "foxclaw" / "skills" / "action.py").read_text(encoding="utf-8")
    desktop_source = (ROOT / "foxclaw" / "skills" / "desktop.py").read_text(encoding="utf-8")
    agent_source = (ROOT / "foxclaw" / "agents" / "mixins" / "tool_exec.py").read_text(
        encoding="utf-8")
    api_browser = (ROOT / "foxclaw" / "api" / "routes" / "browser.py").read_text(
        encoding="utf-8")
    api_desktop = (ROOT / "foxclaw" / "api" / "routes" / "desktop.py").read_text(
        encoding="utf-8")
    tree = ast.parse(skill_source)
    dispatch_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_dispatch"
    ]
    assert len(dispatch_calls) == 1
    assert "self.executor.route(skill)" in skill_source
    assert "executor.route(spec)" in action_source
    assert "executor.route(spec)" in desktop_source
    assert "_invoke_desktop_act" in agent_source and "cap(action=action" in agent_source
    assert "os_instance().browser_act" in api_browser
    assert "os_instance().desktop_act" in api_desktop
    assert "browser_service.act(" not in api_browser
    assert "desktop_service.act(" not in api_desktop


def test_optional_snowserve_streamable_http_integration():
    pytest.importorskip("snowserve")
    import json
    import httpx
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server.auth.settings import AuthSettings
    from snowserve.contracts import (
        AuthenticatedIdentity, FakeLightWorld, Grant, StaticTokenIdentityResolver,
        Tenant, TenancyRegistry,
    )
    from snowserve.contracts.governance_mcp import build_governance_mcp

    token = "foxclaw-dual-mode-integration-token"
    tenant_id = "tenant-integration"
    world = FakeLightWorld({
        "grp:hall-east-spots": {"brightness_pct": 20.0, "color_temp_k": 3000.0,
                                "state": "online"},
    })
    grant = Grant(
        principal="service:foxclaw", intents=frozenset({"device.light.set_state"}),
        targets=frozenset({"grp:hall-east-spots"}), max_write_steps=1)
    tenant = Tenant(tenant_id=tenant_id, principal="service:foxclaw", world=world,
                    grant=grant)
    resolver = StaticTokenIdentityResolver({
        token: AuthenticatedIdentity(tenant_id, "service:foxclaw")})
    server = build_governance_mcp(
        registry=TenancyRegistry({tenant_id: tenant}),
        identity_resolver=resolver,
        auth_settings=AuthSettings(
            issuer_url="https://issuer.example",
            resource_server_url="http://localhost:8000",
            required_scopes=["governance"]),
        sleeper=lambda _seconds: None,
    )
    app = server.streamable_http_app()

    def httpx_factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000",
            headers=headers, timeout=timeout, auth=auth)

    async def run():
        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                "http://localhost:8000/mcp",
                headers={"Authorization": f"Bearer {token}"},
                httpx_client_factory=httpx_factory,
            ) as (read, write, _session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    health = await session.call_tool("serve_health", {})
                    health_data = health.structuredContent or json.loads(health.content[0].text)
                    assert health_data["status"] == "ok"
                    submitted = await session.call_tool(
                        "serve_submit_action",
                        {"action_intent": "把东侧灯调到 80%",
                         "args": {}, "idempotency_key": "integration-key"})
                    data = submitted.structuredContent or json.loads(submitted.content[0].text)
                    assert data["status"] == "pending" and data["contract_id"]
                    contract_id = data["contract_id"]
                    queried = await session.call_tool(
                        "serve_query_contract", {"contract_id": contract_id})
                    assert (queried.structuredContent or
                            json.loads(queried.content[0].text))["status"] == "pending"
                    confirmed = await session.call_tool(
                        "serve_confirm_action",
                        {"contract_id": contract_id, "decision": "approve"})
                    assert (confirmed.structuredContent or
                            json.loads(confirmed.content[0].text))["status"] == "succeeded"
                    audit = await session.call_tool(
                        "serve_query_audit", {"contract_id": contract_id})
                    audit_data = audit.structuredContent or json.loads(audit.content[0].text)
                    assert audit_data["status"] == "ok"

    asyncio.run(run())
