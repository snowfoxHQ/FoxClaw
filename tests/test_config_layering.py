"""E1:分层配置 —— LayeredConfig 三层合并 + SkillPolicy policy 层 deny(管理员锁定,不可覆盖)。"""
import os, sys, tempfile, shutil, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_config_layering():
    print("=" * 60); print("E1 分层配置验证"); print("=" * 60)

    from foxclaw.core.config import LayeredConfig, load_policy_layer

    # 1) LayeredConfig:标量高层覆盖;集合三层并集(只增不减);policy_locked
    cfg = LayeredConfig(policy={"model": "P", "deny": ["a"]},
                        user={"model": "U", "deny": ["b"]},
                        runtime={"model": "R", "deny": ["c"], "x": 1})
    assert cfg.get("model") == "P", "标量:policy 覆盖 user/runtime"
    assert cfg.get("x") == 1, "低层独有键仍可读"
    assert cfg.merged_set("deny") == {"a", "b", "c"}, "集合:三层并集(只增不减)"
    assert cfg.policy_locked("deny") == {"a"}, "policy 层锁定项"
    print("[1] LayeredConfig 标量覆盖 + 集合并集 + policy_locked OK")

    # 2) load_policy_layer:env + policy.json 合并;默认空
    d = tempfile.mkdtemp(prefix="e1_")
    try:
        os.environ["FOXCLAW_POLICY_DENY"] = "gmail_send, danger_tool"
        (Path(d) / "policy.json").write_text(json.dumps({"deny": ["file_delete"]}), encoding="utf-8")
        pl = load_policy_layer(d)
        assert set(pl["deny"]) == {"gmail_send", "danger_tool", "file_delete"}, pl
        print(f"[2] load_policy_layer(env + policy.json) = {pl['deny']} OK")

        # 3) SkillPolicy:policy 层 deny 生效 + undeny 撤不掉 + 从盘 user deny 生效(引用未断) + user 可撤
        (Path(d) / "skill_policy.json").write_text(
            json.dumps({"deny": ["user_denied"], "allow": []}), encoding="utf-8")
        from foxclaw.skills.policy import SkillPolicy
        pol = SkillPolicy(d)
        assert pol.is_blocked("gmail_send") is True, "policy 层 deny 生效"
        assert pol.is_blocked("user_denied") is True, "从盘加载的 user deny 生效(_load 就地更新、引用未断)"
        assert pol.undeny("gmail_send") is False, "policy 层 deny 不可被 undeny 撤销(管理员锁定)"
        assert pol.is_blocked("gmail_send") is True, "undeny 后 policy 层 deny 仍在"
        assert pol.undeny("user_denied") is True and pol.is_blocked("user_denied") is False, "user 层 deny 可撤"
        snap = pol.snapshot()
        assert set(snap["policy_deny"]) == {"gmail_send", "danger_tool", "file_delete"}, snap
        print("[3] SkillPolicy:policy 层不可 undeny + user 层可撤 + 从盘 user deny 生效 OK")
    finally:
        os.environ.pop("FOXCLAW_POLICY_DENY", None)
        shutil.rmtree(d, ignore_errors=True)

    # 4) behavior-preserving:无 policy 层(无 env、无 policy.json)→ SkillPolicy 行为 = 现状
    d2 = tempfile.mkdtemp(prefix="e1b_")
    try:
        from foxclaw.skills.policy import SkillPolicy
        pol2 = SkillPolicy(d2)
        assert pol2.is_blocked("anything") is False, "无 policy 层 + 无 user deny → 不拦"
        pol2.deny("x"); assert pol2.is_blocked("x") is True
        assert pol2.undeny("x") is True and pol2.is_blocked("x") is False, "user deny/undeny 照常(行为不变)"
        assert pol2.snapshot()["policy_deny"] == [], "无 policy 层 → policy_deny 空"
        print("[4] 无 policy 层 → SkillPolicy 行为 = 现状(behavior-preserving) OK")
    finally:
        shutil.rmtree(d2, ignore_errors=True)

    print("\nE1 分层配置验证通过")


if __name__ == "__main__":
    test_config_layering()
