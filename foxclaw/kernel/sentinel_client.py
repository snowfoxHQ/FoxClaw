"""SentinelClient —— 主进程侧与守门人进程通信的 IPC 客户端。

用 stdio JSON-lines 与 foxclaw.kernel.sentinel 子进程通信。线程安全(串行锁),超时 fail-closed。
evaluate 的签名与 skills.permission.evaluate 对齐,返回重建的 PermissionDecision(而非原始 dict),
因此可直接注入 kernel.policy_gate 的 action_evaluator,PolicyGate / SkillAdapter 零改动。

fail-closed 语义:守门人进程不可达(启动失败/超时/崩溃)时,evaluate 返回 deny —— 宁可拦错
不漏放,绝不静默降级回「进程内放行」。mint 失败返回 None,execute_web 失败返回 blocked。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path


class SentinelUnavailable(RuntimeError):
    pass


def _project_root() -> str:
    # foxclaw/kernel/sentinel_client.py → parents[0]=kernel, [1]=foxclaw, [2]=Foxclaw
    return str(Path(__file__).resolve().parents[2])


class SentinelClient:
    def __init__(self, timeout: float = 5.0, cmd=None, data_dir: str = None):
        self._timeout = timeout
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._req_id = 0
        self._proc = None
        self._spawn(cmd)

    # ── 进程生命周期 ─────────────────────────────────────────
    def _spawn(self, cmd) -> None:
        if cmd is None:
            cmd = [sys.executable, "-m", "foxclaw.kernel.sentinel"]
        try:
            env = dict(os.environ)
            if self._data_dir:
                env["FOXCLAW_GUARDIAN_DATA_DIR"] = str(self._data_dir)
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", bufsize=1, cwd=_project_root(), env=env)
        except Exception as e:
            raise RuntimeError(f"Sentinel 启动失败: {e}")
        try:
            self._request("ping", {})  # 握手:启动失败/坏路径在此显式报错,不静默降级
        except Exception as e:
            self._kill()
            raise RuntimeError(f"Sentinel 握手失败: {e}")

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._request("shutdown", {})
            except Exception:
                pass
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ── IPC 核心 ─────────────────────────────────────────────
    def _request(self, op: str, payload: dict):
        with self._lock:
            self._req_id += 1
            mid = self._req_id
            msg = json.dumps({"id": mid, "op": op, **payload}, ensure_ascii=False)
            try:
                self._proc.stdin.write(msg + "\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
            except Exception as e:
                raise SentinelUnavailable(f"IPC 通信失败: {e}")
            if not line:
                raise SentinelUnavailable("守门人进程已退出")
            try:
                resp = json.loads(line)
            except Exception:
                raise SentinelUnavailable("守门人返回非法 JSON")
            if not resp.get("ok"):
                raise SentinelUnavailable(resp.get("error", "unknown error"))
            return resp.get("result")

    # ── 序列化(只传 evaluate 需要的字段,不含 handler/凭证)────
    @staticmethod
    def _serialize_skill(skill) -> dict:
        _tl = getattr(skill, "trust_level", None)
        try:
            trust = _tl() if callable(_tl) else "low"
        except Exception:
            trust = "low"
        return {
            "id": getattr(skill, "id", ""),
            "kind": getattr(skill, "kind", "local"),
            "side_effect": getattr(skill, "side_effect", "read"),
            "destructive": bool(getattr(skill, "destructive", False)),
            "external": bool(getattr(skill, "external", False)),
            "read_only": getattr(skill, "read_only", None),
            "params": getattr(skill, "params", {}) or {},
            "trust": trust,
        }

    @staticmethod
    def _serialize_rules(rules) -> dict:
        if rules is None:
            return {}
        return {
            "deny": list(getattr(rules, "deny", []) or []),
            "ask": list(getattr(rules, "ask", []) or []),
            "allow": list(getattr(rules, "allow", []) or []),
        }

    # ── 对外接口 ─────────────────────────────────────────────
    def evaluate(self, skill, args, rules=None, confirmed=False, mode="strict",
                 taint_level=0, taint_threshold=0):
        """动作裁决(跨进程)。签名对齐 skills.permission.evaluate,返回 PermissionDecision。"""
        from foxclaw.skills.permission import PermissionDecision
        payload = {
            "skill": self._serialize_skill(skill),
            "args": args or {},
            "rules": self._serialize_rules(rules),
            "confirmed": bool(confirmed),
            "mode": mode,
            "taint_level": taint_level,
            "taint_threshold": taint_threshold,
        }
        try:
            d = self._request("evaluate", payload)
        except SentinelUnavailable as e:
            # fail-closed:守门人不可达 → deny,绝不降级放行
            return PermissionDecision("deny", 0,
                                      reason=f"守门人不可达(fail-closed): {e}",
                                      reason_code="SENTINEL_UNAVAILABLE")
        return PermissionDecision(
            decision=d.get("decision", "deny"),
            stage=int(d.get("stage", 0)),
            reason=d.get("reason", ""),
            trust_downgrade=bool(d.get("trust_downgrade", False)),
            requires_confirmation=bool(d.get("requires_confirmation", False)),
            reason_code=d.get("reason_code", ""),
        )

    def mint(self, ref: str, ttl: float = None):
        """为 ref 签发一次性能力令牌。返回 token(绝不返回真值);失败返回 None。"""
        payload = {"ref": ref}
        if ttl is not None:
            payload["ttl"] = ttl
        try:
            return self._request("mint", payload).get("token")
        except SentinelUnavailable:
            return None

    def revoke(self, token: str) -> bool:
        """主动撤销令牌。返回是否真的撤销了一个活跃令牌;失败返回 False。"""
        try:
            return bool(self._request("revoke", {"token": token}).get("revoked"))
        except SentinelUnavailable:
            return False

    def mcp_register(self, servers: list) -> bool:
        """把 MCP server 配置(含 headers 凭证)推送给守门人,覆盖式更新。失败返回 False。"""
        try:
            d = self._request("mcp_register", {"servers": list(servers or [])})
            return isinstance(d, dict) and int(d.get("count", 0)) >= 0
        except SentinelUnavailable:
            return False

    def mcp_call(self, skill, args) -> dict:
        """mcp 代执行:主进程只传意图(server_id + tool + args),守门人持有凭证并调用。
        返回 SkillResult.to_dict()。fail-closed:守门人不可达 → blocked(绝不落到主进程直连外部)。"""
        from foxclaw.skills import SkillResult
        skill_id = getattr(skill, "id", "")
        payload = {
            "skill_id": skill_id,
            "server_id": getattr(skill, "mcp_server", "") or "",
            "tool": getattr(skill, "mcp_tool", "") or "",
            "args": args or {},
        }
        try:
            d = self._request("mcp_call", payload)
        except SentinelUnavailable as e:
            return SkillResult(skill_id, False, status="blocked",
                               message=f"守门人不可达(fail-closed): {e}").to_dict()
        if not isinstance(d, dict):
            return SkillResult(skill_id, False, status="blocked",
                               message="守门人返回异常").to_dict()
        return d

    def mcp_list_tools(self, server_id: str):
        """发现阶段代执行:守门人用自己持有的 headers 连外部 MCP 拉工具,返回序列化后的
        dict 列表(每项含 name/description/inputSchema/annotations)。失败抛 SentinelUnavailable(带具体错误)。"""
        d = self._request("mcp_list_tools", {"server_id": server_id})
        if not isinstance(d, dict) or "tools" not in d:
            err = d.get("error") if isinstance(d, dict) else "list_tools 失败"
            raise SentinelUnavailable(f"守门人 list_tools 失败: {err}")
        return list(d["tools"])

    def execute_web(self, skill, args) -> dict:
        """web_api 代执行:先 mint 一次性令牌,守门人 redeem 后注入真凭证发请求。
        返回 SkillResult.to_dict()(同状态语义),供 SkillAdapter._invoke_web_via_sentinel 重建。
        fail-closed:守门人不可达/令牌签发失败 → blocked(绝不落到主进程直发,否则凭证/SSRF 双失守)。"""
        from foxclaw.skills import SkillResult
        skill_id = getattr(skill, "id", "")
        secret_ref = getattr(skill, "secret_ref", "") or ""

        token = None
        if secret_ref:
            token = self.mint(secret_ref)
            if not token:
                return SkillResult(skill_id, False, status="blocked",
                                   message="签发凭证能力令牌失败(fail-closed)").to_dict()

        payload = {
            "skill_id": skill_id,
            "endpoint": getattr(skill, "endpoint", ""),
            "method": getattr(skill, "http_method", "GET"),
            "token": token or "",
            "auth_scheme": getattr(skill, "auth_scheme", "bearer") or "bearer",
            "param_location": getattr(skill, "param_location", "auto"),
            "timeout": float(getattr(skill, "timeout", 10.0)),
            "allow_local": bool(getattr(skill, "allow_local", False)),
            "args": args or {},
        }
        try:
            d = self._request("execute_web", payload)
        except SentinelUnavailable as e:
            return SkillResult(skill_id, False, status="blocked",
                               message=f"守门人不可达(fail-closed): {e}").to_dict()
        if not isinstance(d, dict):
            return SkillResult(skill_id, False, status="blocked",
                               message="守门人返回异常").to_dict()
        return d
