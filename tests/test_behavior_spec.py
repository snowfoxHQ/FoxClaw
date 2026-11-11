"""
行为规范(Behavior Spec)测试 —— 全离线。

覆盖:
  1. 内置 behavior.yaml 可加载,id 唯一,每个 code_check 都解析到真实可调用对象。
  2. 检查点不是装饰:policy_gate.check_action / confirms.consume / verify.verify 在业务代码里真有调用。
  3. prompt 注入:行为规范在最前段(Markdown 内核之前、persona 之前),带 spec_version,可整体关闭。
  4. 本地覆盖:可新增 / 可收紧;放宽或省略删除严格层规则 → BehaviorLoadError。
  5. 本地覆盖的语法错、版本过期 → 降级为 warning 并忽略,不把应用锁死(内置文件的问题仍然硬失败)。
"""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.agents.mixins.prompt import PromptMixin
from foxclaw.behavior import BehaviorLoadError, load, local_path, prompt_block

ROOT = Path(__file__).parent.parent

BASE_YAML = """
version: 1
spec_id: test-behavior
prohibitions:
  - id: no_x
    rule: 不做 X
    enforce: both
    code_check: "foxclaw.core.verify:verify"
confirmations:
  - id: confirm_y
    rule: 做 Y 前先确认
    enforce: prompt
uncertainty:
  - id: zzz
    rule: 不确定就问
    enforce: prompt
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _loaded(tmp_path, local_text):
    return load(_write(tmp_path, "behavior.yaml", BASE_YAML),
                _write(tmp_path, "behavior.local.yaml", local_text))


def test_builtin_spec_loads_with_unique_ids():
    spec = load()
    ids = [r.id for r in spec.rules]
    assert len(ids) == len(set(ids))
    assert spec.version == 1 and spec.spec_version
    assert {"confirm_side_effect", "no_silent_failure"} <= set(ids)


def test_every_code_check_resolves_to_callable():
    spec = load()
    checks = spec.checkpoints()
    assert checks, "内置规范至少要登记一个真实检查点"
    assert all(callable(fn) for fn in checks.values())


def test_checkpoints_are_really_wired_in_business_code():
    """反漂移:登记了检查点,业务代码里就必须真有调用,否则清单会退化成装饰。"""
    blob = "\n".join(p.read_text(encoding="utf-8")
                     for p in (ROOT / "foxclaw").rglob("*.py")
                     if p.parts[len(ROOT.parts)] != "behavior")
    assert "policy_gate.check_action(" in blob
    assert "confirms.consume(" in blob
    assert "verify.verify(" in blob


def test_prompt_block_leads_with_spec_and_version():
    spec = load()
    block = prompt_block(spec)
    assert block.startswith("[行为规范")
    assert spec.spec_version in block
    for rule in spec.injectable():
        assert f"[{rule.id}]" in block
    assert "不可协商" in block


def test_behavior_precedes_kernel_and_role():
    import types
    fake = types.SimpleNamespace(_role_prompt="我是角色ROLEXYZ。", AGENT_ID="finance")
    out = PromptMixin.system_prompt.fget(fake)
    assert out.index("[行为规范") < out.index("认知操作系统") < out.index("ROLEXYZ")


def test_switch_disables_behavior_block(monkeypatch):
    monkeypatch.setenv("FOXCLAW_BEHAVIOR", "0")
    assert prompt_block() == ""


def test_local_can_add_and_tighten(tmp_path):
    spec = _loaded(tmp_path, """
version: 1
confirmations:
  - id: confirm_y
    rule: 做 Y 前先确认(收紧)
    enforce: both
    code_check: "foxclaw.core.verify:verify"
uncertainty:
  - id: new_rule
    rule: 新加的更严规则
    enforce: prompt
""")
    assert spec.by_id("confirm_y").enforce == "both"
    assert spec.by_id("new_rule") is not None
    assert spec.by_id("no_x").enforce == "both"  # 未提及的保持不变


def test_local_cannot_weaken_enforce(tmp_path):
    with pytest.raises(BehaviorLoadError, match="不能放宽"):
        _loaded(tmp_path, """
prohibitions:
  - id: no_x
    rule: 不做 X
    enforce: prompt
""")


def test_local_cannot_drop_strict_layer_rule(tmp_path):
    with pytest.raises(BehaviorLoadError, match="不能通过省略删除"):
        _loaded(tmp_path, "confirmations: []\n")


def test_local_syntax_error_is_warning_not_fatal(tmp_path):
    spec = _loaded(tmp_path, "prohibitions: [oops\n")
    assert spec.warnings and "已忽略" in spec.warnings[0]
    assert spec.by_id("no_x") is not None


def test_local_stale_version_is_ignored(tmp_path):
    spec = _loaded(tmp_path, "version: 99\nuncertainty: []\n")
    assert any("已过期" in w for w in spec.warnings)
    assert spec.by_id("zzz") is not None


def test_builtin_version_mismatch_is_fatal(tmp_path):
    p = _write(tmp_path, "behavior.yaml", "version: 99\nprohibitions: []\n")
    with pytest.raises(BehaviorLoadError, match="version"):
        load(p, tmp_path / "none.yaml")


def test_enforce_without_code_check_fails(tmp_path):
    p = _write(tmp_path, "behavior.yaml", """
version: 1
prohibitions:
  - id: no_x
    rule: 不做 X
    enforce: both
""")
    with pytest.raises(BehaviorLoadError, match="没有 code_check"):
        load(p, tmp_path / "none.yaml")


def test_unknown_checkpoint_fails(tmp_path):
    p = _write(tmp_path, "behavior.yaml", """
version: 1
prohibitions:
  - id: no_x
    rule: 不做 X
    enforce: code
    code_check: "foxclaw.core.verify:no_such_function"
""")
    with pytest.raises(BehaviorLoadError, match="不存在"):
        load(p, tmp_path / "none.yaml")


def test_duplicate_id_fails(tmp_path):
    p = _write(tmp_path, "behavior.yaml", """
version: 1
prohibitions:
  - id: no_x
    rule: A
    enforce: prompt
uncertainty:
  - id: no_x
    rule: B
    enforce: prompt
""")
    with pytest.raises(BehaviorLoadError, match="重复"):
        load(p, tmp_path / "none.yaml")


def test_local_path_follows_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path))
    assert local_path() == tmp_path / "behavior.local.yaml"
