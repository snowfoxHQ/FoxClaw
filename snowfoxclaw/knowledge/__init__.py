"""
snowfoxclaw.knowledge —— HKC(Snowfoxclaw Knowledge Core)融入 Snowfoxclaw 的知识子系统。

HKC 是成熟的 9 包子系统(KDE 抽取 / KEE 演化 / SQLite+NetworkX 图存储 / 混合检索
BM25+向量+图 / ACE 能力编译)。按 HMR 同款方式 vendor 在 `vendor/hkc/` 下,同进程
装配、经 hkc_sdk DirectClient 调用——不发 HTTP、不起独立服务、无嵌套工程脚手架。

设计要点:
  · 可选依赖优雅降级:缺 vendor/hkc 或缺 networkx 等依赖时 available=False,不影响本体。
  · 模型无关:LLM 抽取复用 Snowfoxclaw 探测出的 provider/key,也可用 HKC_* 环境变量覆盖。
  · 资源可释放:HKCService.close() 关闭全部 4 个 SQLite 连接(经修好的 HKCContainer.close)。
  · 惰性装配:由 Snowfoxclaw.hkc 属性首次访问时才装配,避免每次构造 OS 都开 db 句柄。
"""
import os
import sys
from pathlib import Path


def _ensure_hkc_importable() -> bool:
    """把 vendor/hkc 加入 sys.path,使内嵌的 hkc_* 包可导入(与 HMR 的 vendor 机制一致)。"""
    vendor_hkc = Path(__file__).resolve().parents[2] / "vendor" / "hkc"
    if not vendor_hkc.exists():
        return False
    p = str(vendor_hkc)
    if p not in sys.path:
        sys.path.insert(0, p)
    return True


def _detect_llm(api_key=None):
    """【P2·单 LLM 入口】模型无关地决定 HKC 抽取用的 provider/key/base_url。

    默认从 LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 单入口读,
    与主对话 100% 同源(同 token 同 base_url);HKC 底层只认 4 个 provider 协议名
    (anthropic / deepseek / openai / openai-compatible),做一次协议名映射:
        generic / minimax / qwen / local / openai → openai-compatible
    (它们都是 OpenAI 协议兼容,语义同 HKC 底层认的 'openai-compatible')。

    向后兼容:用户若仍想 HKC 独立配(主对话走云、HKC 走本地),可显式设 HKC_LLM_*
    4 个 env,优先级仍最高。
    """
    # 1. 显式 HKC_LLM_PROVIDER 覆盖(向后兼容「HKC 独立配」场景)
    env_provider = (os.environ.get("HKC_LLM_PROVIDER") or "").strip().lower()
    if env_provider in ("anthropic", "deepseek", "openai", "openai-compatible"):
        key = (os.environ.get("HKC_LLM_API_KEY")
               or os.environ.get("LLM_API_KEY")
               or "")
        base = (os.environ.get("HKC_LLM_BASE_URL")
                or os.environ.get("LLM_BASE_URL")
                or "")
        return env_provider, key or api_key, base

    # 2. 默认单入口:从 LLM_* 读
    provider = (os.environ.get("LLM_PROVIDER") or "generic").strip().lower()
    key = (os.environ.get("LLM_API_KEY") or api_key or "").strip()
    base = os.environ.get("LLM_BASE_URL", "").strip()

    # 3. 协议名映射 → HKC 底层 4 个合法值
    if provider in ("generic", "minimax", "qwen", "local", "openai"):
        hkc_provider = "openai-compatible"
    elif provider == "deepseek":
        hkc_provider = "deepseek"
    elif provider == "anthropic":
        hkc_provider = "anthropic"
    else:
        hkc_provider = "openai-compatible"   # 未知 provider 兜底
    return hkc_provider, key, base


class HKCService:
    """HKC 在 Snowfoxclaw 内的接入句柄:持有同进程容器 + hkc_sdk DirectClient,统一 close。"""

    def __init__(self, container, client):
        self._container = container
        self.client = client   # DirectClient;供 HSR 检索 / /api/knowledge 路由使用

    @property
    def available(self) -> bool:
        return self.client is not None

    @property
    def event_bus(self):
        """HKC 内部 EventBus(或 None)。供桥接到 Snowfoxclaw 的 EventSpine,使知识生命周期
        事件(created/merged/conflict/ability/…)对统一观测层可见。"""
        return getattr(self._container, "event_bus", None)

    @property
    def container(self):
        """同进程 HKCContainer。供 API 把 HKC 自带 REST 路由(星球用的 /knowledge/graph 等)
        的 get_container 依赖指向这同一个已装配容器(依赖倒置,不另开一套)。"""
        return self._container

    def stats(self) -> dict:
        try:
            return self._container.stats()
        except Exception as e:
            return {"error": str(e)}

    def close(self):
        """释放 HKC 全部原生资源(4 个 SQLite 连接 + 摄入线程池)。幂等。"""
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None
            self.client = None


def init_hkc(data_dir, api_key=None):
    """同进程装配 HKC,返回 HKCService(available=True) 或 None(降级)。
    embedding 默认 auto(本地 sentence-transformers 优先,无库/加载失败自动回退 stub;
    可 HKC_EMBEDDING=stub 强制关闭真语义;HKC_EMBEDDING_MODEL 指定 local/auto 的模型名,
    如 BAAI/bge-m3,留空用内置默认模型)。"""
    if not _ensure_hkc_importable():
        print("[Snowfoxclaw] · HKC 未 vendor(可选):vendor/hkc 缺失,知识库降级")
        return None
    try:
        from hkc_api.container import HKCContainer
        from hkc_sdk import connect
    except ImportError as e:
        print(f"[Snowfoxclaw] · HKC 依赖缺失(可选,pip install 'snowfoxclaw[knowledge]'): {e}")
        return None
    try:
        provider, llm_key, llm_base_url = _detect_llm(api_key)
        # 【P2·单 LLM 入口】embedding 模型名优先 EMBEDDING_MODEL(系统唯一本地入口),
        # 再 HKC_EMBEDDING_MODEL(向后兼容);其他 HKC 行为开关不变。
        _emb_model = (os.environ.get("EMBEDDING_MODEL")
                      or os.environ.get("HKC_EMBEDDING_MODEL")) or None
        container = HKCContainer(
            data_dir=str(Path(data_dir) / "hkc"),
            embedding_kind=os.environ.get("HKC_EMBEDDING", "auto"),   # 默认真语义(本地优先,无 ST 回退 stub)
            embedding_url=os.environ.get("HKC_EMBEDDING_URL", "http://localhost:8080"),
            embedding_model=_emb_model,                                # local/auto 模型名(如 bge-m3)
            llm_api_key=llm_key or None,
            llm_provider=provider,
            llm_model=os.environ.get("LLM_MODEL") or os.environ.get("HKC_LLM_MODEL"),
            llm_base_url=llm_base_url or None,
        ).assemble()
        # 注册为 hkc_api 全局单例:若将来挂 HKC 自身 /hkc REST 路由,
        # 其 Depends(get_container) 能取到这同一个容器,而非"未初始化"。
        from hkc_api import container as _cm
        _cm._container = container
        print(f"[Snowfoxclaw] ✓ HKC 知识核心已进程内融合 (provider={provider}, "
              f"base={'已配置' if llm_base_url else 'provider默认'}, "
              f"key={'已配置' if llm_key else '未配置(抽取将降级)'})")
        return HKCService(container, connect(container=container))
    except Exception as e:
        print(f"[Snowfoxclaw] ⚠ HKC 装配失败,知识库降级为空: {e}")
        return None


from .truth_crystallizer import TruthCrystallizer


# HKC 知识生命周期事件 → 桥接到 EventSpine 的类型。**刻意不含 memory.***(那是 HMR 域,
# Snowfoxclaw 已有自己的记忆事件,转发 HKC 的会重复/混淆)。
_HKC_BRIDGED_EVENTS = (
    "knowledge.created", "knowledge.updated", "knowledge.merged", "knowledge.deleted",
    "conflict.detected", "conflict.resolved", "claim.status_changed",
    "ability.created", "ability.updated", "agent.used_ability",
)


def bridge_hkc_events(hkc_event_bus, spine) -> bool:
    """把 HKC 内部 EventBus 的知识生命周期事件桥接到 Snowfoxclaw 的 **EventSpine**——
    消除审计发现的"HKC 事件孤岛"(P0-2 只桥了 harness+system,漏了 HKC)。桥接后知识
    演化(去重/合并/冲突/能力编译)对统一观测(WS/robot/history)可见,source="hkc"。

    转发在 HKC 摄入线程执行(EventBus handler 同步调用);spine 线程安全;转发失败吞掉、
    绝不影响 HKC 内部流转。返回是否接线成功。高频的 knowledge.created/updated 仍进脊柱
    history,但由 WS 投影过滤(见 api._register_spine_projection),避免批量摄入泛洪前端。"""
    if hkc_event_bus is None or spine is None:
        return False

    def _forward(evt: dict):
        try:
            spine.publish(evt.get("event", "knowledge.unknown"),
                          evt.get("payload") or {}, source="hkc",
                          correlation_id=evt.get("event_id"))
        except Exception:
            pass

    ok = False
    for ev in _HKC_BRIDGED_EVENTS:
        try:
            hkc_event_bus.subscribe(ev, _forward)
            ok = True
        except Exception:
            pass
    return ok


def _ability_triggers(ab) -> list:
    """从能力生成触发词:display_name + domain + coverage 概念键(供 router 匹配)。"""
    terms = [ab.display_name, ab.domain]
    terms += [str(k) for k in (getattr(ab, "coverage", {}) or {}).keys()]
    return [t for t in terms if t]


def _make_ability_handler(client, ab):
    """能力 skill 的 handler(read、确定性、无 LLM):在能力 domain 内检索任务相关知识并返回。
    只做知识综合(无外部副作用);LLM 综述可后续叠加。检索失败 → 返回空知识,不崩。"""
    def _handler(query: str = "", **_kw) -> dict:
        try:
            hits = client.search(query, mode="hybrid", top_k=5, domain=ab.domain)
        except Exception:
            hits = []
        return {"ability": ab.ability_key, "domain": ab.domain,
                "knowledge": [{"name": h.name, "summary": h.summary} for h in hits[:5]]}
    return _handler


def register_abilities_as_skills(registry, hkc) -> list:
    """【HKC 第2档 Step4·能力→SkillRegistry(最深融合)】把 HKC 中 **coverage 达标**的 ACE 能力
    注册成 SkillRegistry 里的 skill——**证据 gate**:覆盖不足的**不注册**(能力不予授予)。

    注册的 skill 为 `side_effect=read`(知识综合、无外部副作用)→ 经 SkillAdapter/PolicyGate
    统一治理、且可被认知管线 `stage_route` 路由(复用 A 方案)。返回注册的 skill id 列表。
    HKC 未启用/无达标能力 → 不注册、返回 []。幂等(同 id 覆盖注册)。"""
    if registry is None or hkc is None or not getattr(hkc, "available", False):
        return []
    from ..skills import SkillSpec
    client = hkc.client
    try:
        keys = list(client.list_abilities())
    except Exception:
        return []
    registered = []
    for key in keys:
        try:
            ab = client.compile_ability(key)   # 覆盖不足抛异常 → 证据 gate 跳过、不注册
        except Exception:
            continue
        sid = f"ability:{key}"
        registry.register(SkillSpec(
            id=sid, name=ab.display_name or key,
            description=f"知识编译能力:{ab.display_name}(domain={ab.domain},coverage 达标)",
            kind="local", side_effect="read",
            triggers=_ability_triggers(ab),
            handler=_make_ability_handler(client, ab),
            params={"query": "str"}))
        registered.append(sid)
    # 【深审修复·陈旧清理】重新同步时,移除此前注册但**本轮不再达标**的 ability skill
    # (coverage 掉了/能力定义删了)——register-only 会残留陈旧项;prune 使 registry 反映当前覆盖。
    fresh = set(registered)
    for spec in registry.all():
        if spec.id.startswith("ability:") and spec.id not in fresh:
            registry.unregister(spec.id)
    return registered


__all__ = ["init_hkc", "HKCService", "TruthCrystallizer", "bridge_hkc_events",
           "register_abilities_as_skills"]
