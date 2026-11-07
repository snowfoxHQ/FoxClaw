"""
中文语义漂移检测 · embedding 模型对比（收尾 M1 的证据型发现）

M1 评测发现:默认英文 all-MiniLM-L6-v2 压缩中文语义距离,跑题够得着 WARN、够不着 BLOCK
（semantic.block_recall≈0）。这印证"模型无关"原则——换中文/多语 embedding 应更准。
本脚本在**同一中文语义场景**上跑两个 embedding,给出 block_recall/flag_recall 对比,
把"发现"变成"换模型后好多少"的可复现数字。

用法(需先联网下过多语模型):python evals/governance/compare_embeddings.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from foxclaw.core.thought_chain import ThoughtChain, ThoughtNode
from foxclaw.harness.harness import Harness
from evals.governance import cases

MODELS = [
    ("英文 all-MiniLM-L6-v2 (默认)", "all-MiniLM-L6-v2"),
    ("多语 paraphrase-multilingual-MiniLM-L12-v2", "paraphrase-multilingual-MiniLM-L12-v2"),
]


def eval_semantic(model_name: str) -> dict:
    scn = cases.SEMANTIC
    tc = ThoughtChain(db_path=tempfile.mktemp(suffix=".db"))
    h = Harness(agent_id="eval", thought_chain=tc,
                policy_path=tempfile.mktemp(suffix=".yaml"),
                shadow_rounds=len(scn["warmup"]), embedding_model=model_name)
    h.register_goal(scn["goal"], allowed_tool_categories=set(scn["allowed_tools"]),
                    baseline_entities=set(scn["baseline_entities"]))
    for w in scn["warmup"]:
        n = ThoughtNode.create(session_id="e", agent_id="eval", type="reasoning",
                               content=w, conf=0.9)
        tc.add_node(n); h.check(n)

    n_block = n_adv = n_benign = 0
    blk = det = warn_b = blk_b = 0
    for content, exp in scn["probes"]:
        n = ThoughtNode.create(session_id="e", agent_id="eval", type="reasoning",
                               content=content, conf=0.9)
        tc.add_node(n)
        sw, sr = dict(h.weights), list(h._policy.get("rules", []))
        r = h.check(n)
        h.weights = sw; h._policy["rules"] = sr; h._reindex_learned_actions()
        detected = r.status in ("WARN", "BLOCK")
        blocked = r.status == "BLOCK"
        if exp == "block":
            n_block += 1; n_adv += 1; blk += int(blocked); det += int(detected)
        else:
            n_benign += 1; warn_b += int(r.status == "WARN"); blk_b += int(blocked)
    return {
        "mode": h.sem_drift.mode,
        "block_recall": round(blk / n_block, 3) if n_block else None,
        "flag_recall": round(det / n_adv, 3) if n_adv else None,
        "hard_fpr": round(blk_b / n_benign, 3) if n_benign else 0.0,
        "soft_fpr": round((warn_b + blk_b) / n_benign, 3) if n_benign else 0.0,
    }


def main():
    print("=" * 66)
    print("中文语义漂移检测 · embedding 模型对比（M1 发现收尾）")
    print("=" * 66)
    rows = []
    for label, model in MODELS:
        try:
            r = eval_semantic(model)
        except Exception as e:
            print(f"\n[{label}] 加载失败: {e}")
            print("  (多语模型需先联网下载:python -c \"from sentence_transformers import "
                  "SentenceTransformer as S; S('paraphrase-multilingual-MiniLM-L12-v2')\")")
            continue
        rows.append((label, r))
        print(f"\n[{label}]  ({r['mode']})")
        print(f"   跑题硬拦率 block_recall = {r['block_recall']}")
        print(f"   跑题标记率 flag_recall  = {r['flag_recall']}")
        print(f"   正常硬误报 hard_fpr     = {r['hard_fpr']}   软误报 soft_fpr = {r['soft_fpr']}")

    if len(rows) == 2:
        b0 = rows[0][1]["block_recall"] or 0
        b1 = rows[1][1]["block_recall"] or 0
        print("\n" + "=" * 66)
        print(f"结论: 中文跑题的**硬拦率** block_recall  {b0} → {b1}  "
              f"({'↑ 多语模型确实更准,发现被兑现' if b1 > b0 else '未见提升'})")
        print("  含义: 语义维度对中文的敏感度取决于 embedding 与语言匹配——正是'模型无关'原则的实证。")
        print("=" * 66)


if __name__ == "__main__":
    main()
