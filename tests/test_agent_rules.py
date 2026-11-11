"""
Agent 规则文件加载测试 —— 全离线。

覆盖:
  1. agents/rules/<AGENT_ID>/*.md 在组装系统提示词时被读入(启动即生效,不靠代码常驻)。
  2. 叠加顺序:内核前缀 → 角色职责 → 本 Agent 规则;规则位于最后。
  3. 回退开关 FOXCLAW_AGENT_RULES=0 可整体关闭;无 AGENT_ID / 无目录时不注入。
  4. 规则目录必须落在 foxclaw 包内(随 wheel 分发,不能像仓库根 skills/ 那样漏包)。
"""
import os, sys, types
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.agents.base import KERNEL_PROMPT
from foxclaw.agents.mixins.prompt import PromptMixin, RULES_DIR, load_agent_rules


def _fake(role="我是财务角色ROLEXYZ。", agent_id="finance"):
    return types.SimpleNamespace(_role_prompt=role, AGENT_ID=agent_id)


def test_finance_rules_file_is_loaded():
    text = load_agent_rules("finance")
    assert "Personal Finance Agent Rules v1.0" in text
    assert "不得编造" in text
    assert "储蓄率" in text


def test_unknown_agent_has_no_rules():
    assert load_agent_rules("no_such_agent") == ""
    assert load_agent_rules("") == ""


def test_system_prompt_order_kernel_role_rules():
    out = PromptMixin.system_prompt.fget(_fake())
    assert KERNEL_PROMPT in out
    assert out.index("认知操作系统") < out.index("ROLEXYZ") < out.index("Personal Finance Agent Rules")


def test_rules_follow_kernel_when_role_is_short():
    out = PromptMixin.system_prompt.fget(_fake(role="ROLEXYZ"))
    assert "本 Agent 专业规则(必须遵守)" in out
    assert "月度结余" in out


def test_switch_disables_rules(monkeypatch):
    monkeypatch.setenv("FOXCLAW_AGENT_RULES", "0")
    out = PromptMixin.system_prompt.fget(_fake())
    assert "ROLEXYZ" in out and "Personal Finance Agent Rules" not in out


def test_switch_disables_rules_even_without_kernel(monkeypatch):
    monkeypatch.setenv("FOXCLAW_KERNEL_PROMPT", "0")
    monkeypatch.setenv("FOXCLAW_AGENT_RULES", "0")
    monkeypatch.setenv("FOXCLAW_BEHAVIOR", "0")
    assert PromptMixin.system_prompt.fget(_fake(role="ROLEXYZ")) == "ROLEXYZ"


def test_rules_survive_kernel_off(monkeypatch):
    monkeypatch.setenv("FOXCLAW_KERNEL_PROMPT", "0")
    out = PromptMixin.system_prompt.fget(_fake(role="ROLEXYZ"))
    assert KERNEL_PROMPT not in out
    assert "Personal Finance Agent Rules" in out
    # 行为规范是硬边界,不随内核前缀一起关掉
    assert out.index("[行为规范") < out.index("ROLEXYZ")


def test_agent_without_id_gets_no_rules():
    fake = types.SimpleNamespace(_role_prompt="ROLEXYZ")
    out = PromptMixin.system_prompt.fget(fake)
    assert "ROLEXYZ" in out and "Personal Finance Agent Rules" not in out


def test_rules_dir_lives_inside_package():
    """规则文件必须在 foxclaw 包内:仓库根的 skills/ 就没被打进 wheel,别重演。"""
    pkg = Path(RULES_DIR).resolve()
    assert pkg.name == "rules" and pkg.parent.name == "agents"
    assert (pkg / "finance" / "RULES.md").is_file()
