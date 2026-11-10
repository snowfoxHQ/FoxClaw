"""
foxclaw.knowledge —— HKC(Foxclaw Knowledge Core)融入 Foxclaw 的知识子系统。

HKC 是成熟的 9 包子系统(KDE 抽取 / KEE 演化 / SQLite+NetworkX 图存储 / 混合检索
BM25+向量+图 / ACE 能力编译)。按 HMR 同款方式 vendor 在 `vendor/hkc/` 下,同进程
装配、经 hkc_sdk DirectClient 调用——不发 HTTP、不起独立服务、无嵌套工程脚手架。

设计要点:
  · 可选依赖优雅降级:缺 vendor/hkc 或缺 networkx 等依赖时 available=False,不影响本体。
  · 模型无关:LLM 抽取复用 Foxclaw 探测出的 provider/key,也可用 HKC_* 环境变量覆盖。
  · 资源可释放:HKCService.close() 关闭全部 4 个 SQLite 连接(经修好的 HKCContainer.close)。
  · 惰性装配:由 Foxclaw.hkc 属性首次访问时才装配,避免每次构造 OS 都开 db 句柄。
"""
import json
import os
import sys
from pathlib import Path


def _ensure_hkc_importable() -> bool:
    """让内嵌的 hkc_* 包可被导入(与 HMR 的 vendor 机制一致)。

    两种布局:
    - 源码仓: vendor/hkc 在项目根下,需注入 sys.path;
    - wheel 安装: hkc_* 已作为顶层包装进 site-packages,直接可导入,无需注入。
    """
    vendor_hkc = Path(__file__).resolve().parents[2] / "vendor" / "hkc"
    if vendor_hkc.exists():
        p = str(vendor_hkc)
        if p not in sys.path:
            sys.path.insert(0, p)
        return True
    try:
        import hkc_api  # noqa: F401
    except ImportError:
        return False
    return True


def _to_hkc_provider(name: str) -> str:
    """Foxclaw 的 provider 名 → HKC 底层只认的 4 个协议名。

    (anthropic / deepseek / openai / openai-compatible)其余全是 OpenAI 协议兼容。
    """
    n = (name or "").strip().lower()
    if n == "anthropic":
        return "anthropic"
    if n == "deepseek":
        return "deepseek"
    return "openai-compatible"


def _read_custom_model(data_dir, name):
    """从 ``custom_models.json`` 取一项,解出 ``(key, base_url, model)``;取不到或 key 为空 → None。

    base_url 必须和主对话走同一个规范化(缺 path 补 /v1),否则 SDK 拼出
    https://api.deepseek.com/chat/completions 而主对话用 /v1/chat/completions。
    """
    try:
        models = json.loads((data_dir / "custom_models.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    match = next((m for m in models if m.get("name") == name), None)
    if not match:
        return None
    key = ""
    from ..core.secrets import load as _load_secret
    key = _load_secret(name, match.get("api_key_obf") or "")
    key = key or (match.get("api_key") or "")
    if not key.strip():
        return None
    base = match.get("base_url") or ""
    try:
        from ..core.model_router import normalize_base_url
        base = normalize_base_url(base)
    except Exception:
        pass
    return key.strip(), base, (match.get("model") or "").strip()


def _selected_llm():
    """读前端「LLM 管理」里当前选中的模型,返回 ``(provider, key, base_url, model)`` 或 None。

    这是**用户在界面上显式做过的选择**(FOXCLAW_DATA_DIR/.current_model),比任何
    env 默认值都更能代表"现在真实在用的那个模型",所以排在 LLM_* 之前。

    两个踩过的坑:
      1. preset 写的字段是 ``provider``,不是 ``name`` —— 只读 name 会让 preset 选择被
         整个忽略,HKC 于是退回 .env(里面可能躺着一份早就换掉的旧 key)。
      2. preset 的 key 名义上来自 .env,但用户往往同时在「自定义模型」里配过同一个
         provider(名字相同、key 更新)。界面里填的比 .env 默认新,所以优先它。
    """
    try:
        data_dir = Path(os.environ.get("FOXCLAW_DATA_DIR", "./foxclaw_data"))
        sel_file = data_dir / ".current_model"
        if not sel_file.exists():
            return None
        sel = json.loads(sel_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    source = (sel.get("source") or "").strip().lower()
    name = (sel.get("name") or sel.get("provider") or "").strip()
    model = (sel.get("model_id") or sel.get("model") or "").strip()

    if source == "custom" and name:
        got = _read_custom_model(data_dir, name)
        if not got:
            return None
        key, base, custom_model = got
        return _to_hkc_provider("generic"), key, base, model or custom_model

    if source == "preset" and name:
        got = _read_custom_model(data_dir, name)
        if got:
            key, base, custom_model = got
            return _to_hkc_provider("generic"), key, base, model or custom_model
        env_name = ""
        try:
            from ..core.model_router import PROVIDER_ENV
            env_name = PROVIDER_ENV.get(name, "")
        except Exception:
            pass
        key = (os.environ.get(env_name) or "").strip() if env_name else ""
        return (_to_hkc_provider(name), key, "", model)

    return None


def _detect_llm(api_key=None):
    """【P2·单 LLM 入口】模型无关地决定 HKC 抽取用的 provider/key/base_url/model。

    优先级(由高到低),整体目标是「和主对话用同一个模型」:
      1. ``HKC_LLM_*`` —— 显式让 HKC 独立配(主对话走云、抽取走本地)
      2. **前端 LLM 管理当前选中项** —— 用户在界面上做的显式选择
      3. ``LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`` 单入口 env
      4. 主对话的自动探测(``ModelRouter._auto_detect``)

    HKC 底层只认 4 个协议名(anthropic / deepseek / openai / openai-compatible),
    其余按 OpenAI 兼容协议映射过去。

    返回 ``(provider, key, base_url, model)``。
    """
    env_provider = (os.environ.get("HKC_LLM_PROVIDER") or "").strip().lower()
    if env_provider in ("anthropic", "deepseek", "openai", "openai-compatible"):
        key = (os.environ.get("HKC_LLM_API_KEY")
               or os.environ.get("LLM_API_KEY")
               or "")
        base = (os.environ.get("HKC_LLM_BASE_URL")
                or os.environ.get("LLM_BASE_URL")
                or "")
        model = (os.environ.get("HKC_LLM_MODEL")
                 or os.environ.get("LLM_MODEL")
                 or "")
        return env_provider, key or api_key, base, model

    selected = _selected_llm()
    if selected:
        provider, key, base, model = selected
        return provider, (key or api_key or "").strip(), (base or "").strip(), model

    provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    key = (os.environ.get("LLM_API_KEY") or api_key or "").strip()
    base = os.environ.get("LLM_BASE_URL", "").strip()
    model = (os.environ.get("LLM_MODEL") or os.environ.get("HKC_LLM_MODEL") or "").strip()

    provider_env = {}
    if not provider:
        try:
            from ..core.model_router import ModelRouter, PROVIDER_ENV
            provider_env = PROVIDER_ENV
            detected = ModelRouter._auto_detect()
        except Exception:
            detected = ""
        provider = detected if detected and detected not in ("stub", "local") else "generic"
    if not key:
        env_name = provider_env.get(provider) or ""
        if env_name:
            key = (os.environ.get(env_name) or "").strip()

    return _to_hkc_provider(provider), key, base, model


class HKCService:
    """HKC 在 Foxclaw 内的接入句柄:持有同进程容器 + hkc_sdk DirectClient,统一 close。"""

    def __init__(self, container, client):
        self._container = container
        self.client = client

    @property
    def available(self) -> bool:
        return self.client is not None

    @property
    def event_bus(self):
        """HKC 内部 EventBus(或 None)。供桥接到 Foxclaw 的 EventSpine,使知识生命周期
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
        print("[Foxclaw] · HKC 未 vendor(可选):vendor/hkc 缺失,知识库降级")
        return None
    try:
        from hkc_api.container import HKCContainer
        from hkc_sdk import connect
    except ImportError as e:
        print(f"[Foxclaw] · HKC 依赖缺失(可选,pip install 'foxclaw[knowledge]'): {e}")
        return None
    try:
        provider, llm_key, llm_base_url, llm_model = _detect_llm(api_key)
        _emb_model = (os.environ.get("EMBEDDING_MODEL")
                      or os.environ.get("HKC_EMBEDDING_MODEL")) or None
        container = HKCContainer(
            data_dir=str(Path(data_dir) / "hkc"),
            embedding_kind=os.environ.get("HKC_EMBEDDING", "auto"),
            embedding_url=os.environ.get("HKC_EMBEDDING_URL", "http://localhost:8080"),
            embedding_model=_emb_model,
            llm_api_key=llm_key or None,
            llm_provider=provider,
            llm_model=llm_model or None,
            llm_base_url=llm_base_url or None,
        ).assemble()
        from hkc_api import container as _cm
        _cm._container = container
        print(f"[Foxclaw] ✓ HKC 知识核心已进程内融合 (provider={provider}, "
              f"base={'已配置' if llm_base_url else 'provider默认'}, "
              f"key={'已配置' if llm_key else '未配置(抽取将降级)'})")
        return HKCService(container, connect(container=container))
    except Exception as e:
        print(f"[Foxclaw] ⚠ HKC 装配失败,知识库降级为空: {e}")
        return None


from .truth_crystallizer import TruthCrystallizer


_HKC_BRIDGED_EVENTS = (
    "knowledge.created", "knowledge.updated", "knowledge.merged", "knowledge.deleted",
    "conflict.detected", "conflict.resolved", "claim.status_changed",
    "ability.created", "ability.updated", "agent.used_ability",
)


def bridge_hkc_events(hkc_event_bus, spine) -> bool:
    """把 HKC 内部 EventBus 的知识生命周期事件桥接到 Foxclaw 的 **EventSpine**——
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
            ab = client.compile_ability(key)
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
    fresh = set(registered)
    for spec in registry.all():
        if spec.id.startswith("ability:") and spec.id not in fresh:
            registry.unregister(spec.id)
    return registered


__all__ = ["init_hkc", "HKCService", "TruthCrystallizer", "bridge_hkc_events",
           "register_abilities_as_skills"]
