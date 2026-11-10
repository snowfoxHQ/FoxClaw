"""
Foxclaw — 全量回归测试运行器
一次跑完 tests/ 下所有测试,汇总通过/失败,确认整个系统端到端健康。
用法:python tests/run_all.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

TESTS = [
    ("test_e2e.py",          "认知运行时端到端"),
    ("test_api.py",          "API 端点"),
    ("test_integration.py",  "A · 前后端实时链路"),
    ("test_dispatch.py",     "D · 主管调度 + 共享记忆"),
    ("test_finance.py",      "B · 财务内核"),
    ("test_learning.py",     "B · 学习内核"),
    ("test_itinerary.py",    "B · 行程内核"),
    ("test_orchestration.py","主管编排 + 跨 Agent"),
    ("test_skills.py",       "助手 Skill 接入层"),
    ("test_skills_fetch.py", "技能安装(ClawHub/GitHub/URL/本地包)"),
    ("test_kernel.py",       "内核新模块回归"),
    ("test_governance.py",   "运行时治理(幂等/恢复/队列/压缩/循环/钩子)"),
    ("test_compaction_toolpair.py", "M5·压缩器工具消息配对(原子组切分+畸形输入安全降级)"),
    ("test_tool_params.py",  "M4·工具入参校验(执行侧 schema/Stage1/解析失败可见)"),
    ("test_shared_write.py", "P0-3 · 共享层写入策略(audit/warn/enforce)"),
    ("test_audit_sink.py",   "P0-3 · 审计落盘 sink(环形缓冲外的持久样本)"),
    ("test_governance_audit.py", "批次3 · 治理审计链(P1-2/P1-3/P2-0/P3-4 decision_id)"),
    ("test_shadow_learning.py", "批次4 · P0-1 影子学习(shadow→promote/discard+回滚+边界)"),
    ("test_channels.py",     "消息渠道(四适配器+闭环+幂等去重)"),
    ("test_evals_smoke.py",  "M1 · 治理评测冒烟(拦截率/误报/收敛)"),
    ("test_parallel_dispatch.py", "M2 · 并行子 Agent(fork-join/隔离/并发安全)"),
    ("test_governance_middleware.py", "M3 · 治理中间件(框架无关/装饰器/审计)"),
    ("test_memory_recall.py", "M4 · 记忆隔离召回完整性(根治)"),
    ("test_toplevel_concurrency.py", "顶层任务并发隔离(contextvars)"),
    ("test_api_auth.py",     "API 可选鉴权(X-API-Key)"),
    ("test_supervisor_triage.py", "主管分诊与自答策略(关键词直路 / 自答 / stub 兜底)"),
    ("test_supervisor_pipeline.py", "批次5 · P1-1 主管 pipeline 化(八 stage/统一 emit/流式/恢复记账)"),
    ("test_ahs_p2.py",       "批次6 · P2层(compose pending/run_async/idempotency日志/recovery计划)+P3-2"),
    ("test_p3_p5_hardening.py", "批次7 · P3收尾(isinstance/常量时间/exact/blocked短路/system归因)+P5-1/P5-3"),
    ("test_browser.py",      "浏览器子系统(HIR-Browser:核心逻辑/线程包装/降级)"),
    ("test_hkc.py",          "知识核心(HKC融入:装配/检索/H1资源修复/惰性teardown)"),
    ("test_hkc_manifest_reuse.py",        "P1·Stage A — manifest v2 复用(roundtrip/8步校验/bm25 0-corpus)"),
    ("test_hkc_search_event_sync.py",     "P1·Stage B — 事件→索引 C/U/D 五分支同步(text_hash 跳过/立即落盘)"),
    ("test_hkc_index_build_manager.py",   "P1·Stage C — 后台冷构建(状态机/检查点/锁/batch/取消/重启续传)"),
    ("test_search.py",       "认知搜索(HSR融入:编排/证据融合/迭代上限/端口降级)"),
    ("test_embedding_singleton.py", "P0·EmbeddingService单例(同名模型进程内只加载一次)"),
    ("test_event_spine.py",  "P0·统一事件脊柱(EventSpine/SystemBus门面/harness转发/统一投影)"),
    ("test_agent_lifecycle.py", "P1·任务生命周期收敛(5 run_task→1 模板 + _execute 钩子)"),
    ("test_planner.py",      "P1-2·规划原语统一(主管/AHS/search 三处委托一个引擎)"),
    ("test_capabilities.py", "P1-3·能力中枢(Composition Root/打断两处对象环)"),
    ("test_truth_flow.py",   "P2·真值流全路径触发(直连任务也进真值链 + _no_emit 防双发)"),
    ("test_orchestration_decoupled.py", "P3·编排层去领域硬编码(通用聚合/路由,领域归属Agent)"),
    ("test_pipeline.py",     "v2②·认知管线(perceive/recall/plan/route/act/verify/reflect)"),
    ("test_scheduler.py",    "v2①·微内核调度器(并发配额/背压/生命周期/记账)+Lifecycle统一"),
    ("test_policy_gate.py",  "v2①Step4·统一准入门(Harness+skill-guard→一套Verdict+审计)"),
    ("test_truth_crystallizer.py", "v2④Step1·真值走HKC/KEE(默认关双写+后台结晶+订阅接线)"),
    ("test_reasoner.py",     "v2③Step1·领域认知化(观察→假设→证据→裁决;财务守门人形式化)"),
    ("test_domain_reasoners.py", "v2③Step4·认知化推广(itinerary冲突/learning复习;规则权威+LLM只增强)"),
    ("test_tool_center.py",  "工具中心①·技能台真实化(安装/启停/删除/测试/持久化/SSRF审核门/治理)"),
    ("test_mcp_connectors.py", "工具中心②·MCP 连接器(接入/工具成技能/审核/调用/移除/持久化)"),
    ("test_markdown_skills.py", "工具中心③·SKILL.md 指令技能(上传/解析/审核/注入/持久化)"),
    ("test_markdown_skills_openclaw.py", "工具中心③·OpenClaw 兼容(zip包/描述触发/编辑触发词·进程隔离防原生库崩溃)"),
    ("test_mcp_server.py",   "内置 MCP 服务器(Foxclaw 作提供者:只读知识+记忆暴露成 MCP 工具)"),
    ("test_mcp_no_memory.py", "MCP ask_foxclaw 不污染记忆(外部任务不写 HMR,正常任务照写)"),
    ("test_streaming.py",    "LLM 流式输出(router.stream 分块 + /api/chat/stream SSE 逐字)"),
    ("test_task_stream.py",  "D1 · /api/task/stream 任务流式化(逐 stage 进度 + act token + 结果)"),
    ("test_config_layering.py", "E1 · 分层配置(LayeredConfig 三层 + policy 层 deny 不可覆盖)"),
    ("test_semantic.py",     "真语义检索 + KEE 语义化(共享ST单例/余弦/矛盾方向;无缓存优雅跳过)"),
    ("test_metrics.py",      "ops 监控(请求/LLM 延迟分位+错误率 · 深度健康 · Prometheus)"),
    ("test_routing.py",      "任务感知模型路由(fast/smart 分档 + 跨 provider fallback 链)"),
    ("test_file_ingest.py",  "文件摄入打通交互层(docx/xlsx 加载器 + KDE 分派 + 上传格式门)"),
    ("test_multimodal.py",   "多模态对话(图片随问题发视觉模型 · 按 provider 组装 · 护栏 · stub 降级)"),
    ("test_attachments.py",  "多模态渲染·聊天附件(上传/下载/正文提取/护栏/LLM 注入/会话落盘)"),
    ("test_gateway.py",      "统一 API 网关(per-client 令牌桶+配额+空闲淘汰+429/Retry-After+启用哲学)"),
    ("test_kee_semantic.py", "KEE 身份/去重(别名确定性匹配·类型隔离·Fact 完整 statement 守 exact)"),
    ("test_domain_import.py", "OS 领域导入超越 csv(finance/learning/itinerary 上传 csv/xlsx/json)"),
    ("test_tracing.py",      "请求链路追踪(trace_id 贯穿 请求→Agent→LLM·跨线程 span·有界环存)"),
    ("test_emb_cache.py",    "检索/embedding 共享向量缓存(批量只编未命中·跨层共享·LRU 有界·并发)"),
    ("test_multi_chain.py",  "智能体层·多步跨 Agent 链式协作(后一步看得到前序结果·上下文封顶)"),
    ("test_web_trigger.py",  "联网触发判定(个人数据不误搜·外部话题照搜·纯时间词不触发)"),
    ("test_evidence_semantic.py", "证据融合语义化(否定词边界·幻影矛盾·语义支持边·降级)"),
    ("test_domain_evals.py", "领域推理评测(漏报/误报率+误报归因到规则+规则实例隔离)"),
    ("test_negotiation.py",  "智能体层·跨域协商闭环(被拒→让步→再裁决·有界·拒绝不降级)"),
    ("test_tool_use.py",        "P2·function-calling 工具化(多轮工具循环/注册表/降级)"),
    ("test_injection_guard.py", "提示词注入纵深防御(检出/wrap_untrusted/内核前缀/隔离)"),
    ("test_sessions.py",        "会话存储(增删改查/标题提炼/路径穿越/并发追加)"),
    ("test_stats_and_events.py","记忆统计 + 活动事件(hmr.stats 计数修复/chat.completed)"),
    ("test_llm_planner.py",            "P0-1·LLM 驱动规划器(llm_plan/replan·opt-in+fail-safe+sv_replan)"),
    ("test_anthropic_tools.py",        "P0-2·Anthropic tool_use 原生接入(格式转换/解析/回灌/降级)"),
    ("test_taint.py",                  "数据污点追踪+动态审批门槛(污点分级/传播/TAINT_CONFIRM)"),
    ("test_mcp_guardian_discovery.py", "MCP 发现进守门人(凭证不出主进程·代发现·序列化)"),
    ("test_verify_evidence.py",        "三个薄点·失败策略表/执行后核验/执行证据(重试只限只读)"),
    ("test_evidence_sse.py",           "执行凭据 SSE 命名事件(与正文分离·纯对话不发)"),
    ("test_profile.py",                "称呼(user profile)存取:写入/替换/清空/不误删偏好"),
    ("test_update_check.py",           "新版本检查:默认关/版本比较/24h 缓存/失败静默"),
]


def main():
    here = Path(__file__).parent
    _argv = sys.argv[1:]
    global TESTS
    if "--list" in _argv:
        print(f"共 {len(TESTS)} 项:")
        for _f, _d in TESTS:
            print(f"  {_f:<40} {_d}")
        sys.exit(0)
    _only = None
    if "--only" in _argv:
        _i = _argv.index("--only")
        _only = _argv[_i + 1] if _i + 1 < len(_argv) else None
        if not _only:
            print("--only 需要一个子串参数,例:python tests/run_all.py --only governance")
            sys.exit(2)
        _before = len(TESTS)
        TESTS = [(f, d) for f, d in TESTS if _only in f or _only in d]
        print(f"[filter] '{_only}' 匹配 {len(TESTS)}/{_before} 项\n")
    env = dict(os.environ, FOXCLAW_SHADOW_ROUNDS="50",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               FOXCLAW_SKIP_DOTENV="1",
               DEEPSEEK_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
               GOOGLE_API_KEY="", DASHSCOPE_API_KEY="", FOXCLAW_API_KEY="",
               FOXCLAW_MCP_SERVER="0",
               FOXCLAW_PROVIDER="", FOXCLAW_MODEL="",
               HKC_EMBEDDING="stub")
    print("=" * 64)
    print("Foxclaw · 全量回归测试")
    print("=" * 64)
    results = []
    try:
        import pytest  # noqa: F401
        have_pytest = True
    except Exception:
        have_pytest = False
    if not have_pytest:
        print("⚠ 未安装 pytest —— 依赖它的测试将标记为「跳过」。"
              "完整回归请先 `pip install -e \".[dev]\"` 或 `pip install pytest`。\n")
    for fn, desc in TESTS:
        if not have_pytest:
            results.append((desc, "跳过", 0.0))
            print(f"  ○ {desc:<28} [跳过:需 pytest]")
            continue
        path = here / fn
        if not path.exists():
            results.append((desc, "缺失", 0)); continue
        t0 = time.time()
        _bt = Path(tempfile.mkdtemp(prefix="foxclaw-pt-"))
        try:
            _cmd = [sys.executable, "-m", "pytest", str(path), "-q",
                    f"--basetemp={_bt}"]
            r = subprocess.run(_cmd,
                               capture_output=True, text=True, timeout=300, env=env,
                               encoding="utf-8", errors="replace")
            ok = r.returncode == 0
            if "Traceback" in (r.stdout + r.stderr):
                ok = False
            _ACCESS_VIOLATION = (3221225477, -1073741819)
            if not ok and r.returncode in _ACCESS_VIOLATION and \
                    "Traceback" not in (r.stdout + r.stderr):
                r = subprocess.run(_cmd,
                                   capture_output=True, text=True, timeout=300, env=env,
                                   encoding="utf-8", errors="replace")
                ok = r.returncode == 0 and "Traceback" not in (r.stdout + r.stderr)
                if ok:
                    print(f"  ↻ {desc} 首次 0xC0000005 原生崩溃,重试通过(已知环境性偶发)")
            results.append((desc, "通过" if ok else "失败", time.time() - t0))
            if not ok:
                tail = (r.stdout + r.stderr).strip().splitlines()[-15:]
                print(f"  ✗ {desc} 失败:")
                for line in tail: print(f"      {line}")
                (here / f"_fail_{fn}.txt").write_text(
                    r.stdout + r.stderr, encoding="utf-8")
        except subprocess.TimeoutExpired:
            results.append((desc, "超时", 300))
        finally:
            shutil.rmtree(_bt, ignore_errors=True)
        print(f"  {'✓' if results[-1][1]=='通过' else '✗'} {desc:<28} [{results[-1][1]}] {results[-1][2]:.0f}s")

    print("\n" + "=" * 64)
    passed = sum(1 for _, s, _ in results if s == "通过")
    skipped = sum(1 for _, s, _ in results if s == "跳过")
    total = len(results) - skipped
    suffix = f"（另有 {skipped} 项因缺 pytest 跳过）" if skipped else ""
    print(f"结果:{passed}/{total} 通过{suffix}")
    print("=" * 64)
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
