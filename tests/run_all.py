"""
Hestia OS — 全量回归测试运行器
一次跑完 tests/ 下所有测试,汇总通过/失败,确认整个系统端到端健康。
用法:python tests/run_all.py
"""
import sys, os, subprocess, time
from pathlib import Path

# Windows GBK 控制台无法编码 ✓/✗(✓/✗)等字符 → 运行器自己把 stdout/stderr 切到
# utf-8,不依赖调用者预先 set PYTHONUTF8。否则打印结果时 UnicodeEncodeError 直接崩掉汇总。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
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
    ("test_kernel.py",       "内核新模块回归"),
    ("test_governance.py",   "运行时治理(幂等/恢复/队列/压缩/循环/钩子)"),
    ("test_channels.py",     "消息渠道(四适配器+闭环+幂等去重)"),
    ("test_evals_smoke.py",  "M1 · 治理评测冒烟(拦截率/误报/收敛)"),
    ("test_parallel_dispatch.py", "M2 · 并行子 Agent(fork-join/隔离/并发安全)"),
    ("test_governance_middleware.py", "M3 · 治理中间件(框架无关/装饰器/审计)"),
    ("test_memory_recall.py", "M4 · 记忆隔离召回完整性(根治)"),
    ("test_toplevel_concurrency.py", "顶层任务并发隔离(contextvars)"),
    ("test_api_auth.py",     "API 可选鉴权(X-API-Key)"),
    ("test_llm_routing.py",  "LLM 辅助路由(关键词未命中精修)"),
    ("test_browser.py",      "浏览器子系统(HIR-Browser:核心逻辑/线程包装/降级)"),
    ("test_hkc.py",          "知识核心(HKC融入:装配/检索/H1资源修复/惰性teardown)"),
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
    ("test_mcp_server.py",   "内置 MCP 服务器(HestiaOS 作提供者:只读知识+记忆暴露成 MCP 工具)"),
    ("test_mcp_no_memory.py", "MCP ask_hestia 不污染记忆(外部任务不写 HMR,正常任务照写)"),
    ("test_streaming.py",    "LLM 流式输出(router.stream 分块 + /api/chat/stream SSE 逐字)"),
    ("test_task_stream.py",  "D1 · /api/task/stream 任务流式化(逐 stage 进度 + act token + 结果)"),
    ("test_config_layering.py", "E1 · 分层配置(LayeredConfig 三层 + policy 层 deny 不可覆盖)"),
    ("test_semantic.py",     "真语义检索 + KEE 语义化(共享ST单例/余弦/矛盾方向;无缓存优雅跳过)"),
    ("test_metrics.py",      "ops 监控(请求/LLM 延迟分位+错误率 · 深度健康 · Prometheus)"),
    ("test_routing.py",      "任务感知模型路由(fast/smart 分档 + 跨 provider fallback 链)"),
    ("test_file_ingest.py",  "文件摄入打通交互层(docx/xlsx 加载器 + KDE 分派 + 上传格式门)"),
    ("test_multimodal.py",   "多模态对话(图片随问题发视觉模型 · 按 provider 组装 · 护栏 · stub 降级)"),
    ("test_gateway.py",      "统一 API 网关(per-client 令牌桶+配额+空闲淘汰+429/Retry-After+启用哲学)"),
    ("test_kee_semantic.py", "KEE 身份/去重向量化(语义近重归并·仅身份型·别名学习·默认关 opt-in)"),
    ("test_domain_import.py", "OS 领域导入超越 csv(finance/learning/itinerary 上传 csv/xlsx/json)"),
    ("test_tracing.py",      "请求链路追踪(trace_id 贯穿 请求→Agent→LLM·跨线程 span·有界环存)"),
    ("test_emb_cache.py",    "检索/embedding 共享向量缓存(批量只编未命中·跨层共享·LRU 有界·并发)"),
    ("test_multi_chain.py",  "智能体层·多步跨 Agent 链式协作(后一步看得到前序结果·上下文封顶)"),
    ("test_web_trigger.py",  "联网触发判定(个人数据不误搜·外部话题照搜·纯时间词不触发)"),
    ("test_evidence_semantic.py", "证据融合语义化(否定词边界·幻影矛盾·语义支持边·降级)"),
    ("test_domain_evals.py", "领域推理评测(漏报/误报率+误报归因到规则+规则实例隔离)"),
    ("test_negotiation.py",  "智能体层·跨域协商闭环(被拒→让步→再裁决·有界·拒绝不降级)"),
]

def main():
    here = Path(__file__).parent
    # HF_HUB_OFFLINE:装了 sentence-transformers 时,每个 HestiaOS() 会载入 all-MiniLM
    # 模型 6 次(5 Harness + 1 HMR embedding),且每次都向 HF Hub 发未认证网络请求——
    # 即便模型已缓存。实测让单次 init 从 ~44s 降到 ~7s,并去除网络抖动(否则被 HF 限流时
    # 某个子测试会顶穿 300s 超时)。模型已本地缓存,离线读取即可;未缓存时 Harness 会自动
    # 降级 n-gram 哈希嵌入,不影响测试判定。PYTHONUTF8:防 Windows GBK 控制台的 ✓/✗ 编码错。
    env = dict(os.environ, HESTIA_SHADOW_ROUNDS="50",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               # 强制 stub:即使项目 .env 里有真实 LLM key(api.py 会 load_dotenv),回归也
               # 保持离线/免费/确定性。load_dotenv 默认 override=False,故这里的空值优先,
               # .env 的 key 被忽略,ModelRouter 降级 stub。也清掉 HESTIA_API_KEY(鉴权由
               # test_api_auth 自己控制)。
               DEEPSEEK_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
               GOOGLE_API_KEY="", DASHSCOPE_API_KEY="", HESTIA_API_KEY="",
               # 产品默认 HKC_EMBEDDING=auto(真语义),但回归锁 stub 保确定+快;
               # 真语义单独由 test_semantic.py 显式验证。
               HKC_EMBEDDING="stub")
    print("=" * 64)
    print("Hestia OS · 全量回归测试")
    print("=" * 64)
    results = []
    for fn, desc in TESTS:
        path = here / fn
        if not path.exists():
            results.append((desc, "缺失", 0)); continue
        t0 = time.time()
        try:
            # 显式 utf-8 解码子进程输出:子进程已被 PYTHONUTF8=1 强制输出 utf-8,
            # 父进程若用 Windows 默认 GBK 解码会 UnicodeDecodeError(模型加载进度条等
            # 含非 GBK 字节)→ r.stdout 变 None → 后续拼接 TypeError。errors=replace 兜底。
            r = subprocess.run([sys.executable, str(path)],
                               capture_output=True, text=True, timeout=300, env=env,
                               encoding="utf-8", errors="replace")
            ok = r.returncode == 0
            # 简单判定:退出码 0 且输出里没有 Traceback
            if "Traceback" in (r.stdout + r.stderr):
                ok = False
            results.append((desc, "通过" if ok else "失败", time.time() - t0))
            if not ok:
                tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
                print(f"  ✗ {desc} 失败:")
                for line in tail: print(f"      {line}")
        except subprocess.TimeoutExpired:
            results.append((desc, "超时", 300))
        print(f"  {'✓' if results[-1][1]=='通过' else '✗'} {desc:<28} [{results[-1][1]}] {results[-1][2]:.0f}s")

    print("\n" + "=" * 64)
    passed = sum(1 for _, s, _ in results if s == "通过")
    print(f"结果:{passed}/{len(results)} 通过")
    print("=" * 64)
    return 0 if passed == len(results) else 1

if __name__ == "__main__":
    sys.exit(main())
