/* ════════════════════════════════════════════════════════════
   FOXCLAW DECK — Widget Definitions
   每个 widget 是一个 registerWidget() 注册。
   新增模块只需在这里加一个 register block。
   ════════════════════════════════════════════════════════════ */

/* 共享工具(由主文件注入到 ctx) */
function _esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');}
function _statusColor(s){return{PASS:'#1dffa6',WARN:'#ffb347',BLOCK:'#ff4060',BASELINE:'#b066ff',PENDING:'#3a4d68'}[s]||'#1de9ff';}

/* ─────────────────────────────────────────────
   WIDGET 1: AGENT 编队 (roster)
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'roster',
  title: 'AGENT 编队',
  icon: '◈',
  defaultColumn: 'left',
  size: { flex: true },
  render(el, ctx) {
    el.innerHTML = `<div class="roster" data-role="roster-list"></div>`;
    const list = el.querySelector('[data-role="roster-list"]');
    list.innerHTML = ctx.AGENTS.map(a => `
      <div class="agent-pod ${a.id===ctx.activeAgent?'active':''}" data-pod="${a.id}"
           style="--apc:${a.color}">
        <div class="pod-avatar"><div class="ring-pulse"></div>${a.emoji}</div>
        <div class="pod-info">
          <div class="pod-name">${a.name}</div>
          <div class="pod-role">${a.code} · ${a.role}</div>
          <div class="pod-state"><span class="s-dot"></span><span data-podstate="${a.id}">IDLE</span></div>
        </div>
        <div class="pod-metric">
          <div class="pm-val" data-podtask="${a.id}" style="color:${a.color}">${a.tasks}</div>
          <div class="pm-lbl">TASK</div>
        </div>
      </div>`).join('');
    list.querySelectorAll('[data-pod]').forEach(pod => {
      pod.onclick = () => ctx.setActiveCore(pod.dataset.pod);
    });
  },
  onAgentChange(agentId, el) {
    el.querySelectorAll('.agent-pod').forEach(p =>
      p.classList.toggle('active', p.dataset.pod === agentId));
  },
});

/* ─────────────────────────────────────────────
   WIDGET 2: 任务流 (tasks)
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'tasks',
  title: '任务流',
  icon: '▤',
  defaultColumn: 'left',
  size: { maxH: 200 },
  render(el, ctx) {
    el.innerHTML = `<div class="tc-stream" data-role="task-stream"><div class="empty">等待指令下达</div></div>`;
    ctx._taskStreamEl = el.querySelector('[data-role="task-stream"]');
  },
});

/* ─────────────────────────────────────────────
   WIDGET 3: 中央核心 (core) — 激活 Agent
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'core',
  title: '中央核心 · 激活单元',
  icon: '⊚',
  defaultColumn: 'center',
  size: { flex: true },
  render(el, ctx) {
    el.innerHTML = `
      <div class="core-stage" data-role="core-stage">
        <div class="core-hud-tl">SESSION <b data-hud="session">—</b><br>NODES <b data-hud="nodes">0</b></div>
        <div class="core-hud-tr">CONF <b data-hud="conf">—</b><br>Δ DRIFT <b data-hud="drift">—</b></div>
        <div class="core-hud-bl">POLICY <b data-hud="policy">v1</b><br>RULES <b data-hud="rules">0</b></div>
        <div class="core-hud-br">MODE <b data-hud="mode">SHADOW</b><br>BLOCK <b data-hud="block">0.25</b></div>
        <div class="core-orbit">
          <div class="orbit-ring r1"></div><div class="orbit-ring r2"></div><div class="orbit-ring r3"></div>
          <div class="orbit-tick" data-role="orbit-tick"></div>
          <div class="radar-sweep"></div>
        </div>
        <div class="core-avatar" data-role="core-avatar">🏢</div>
        <div class="core-name" data-role="core-name">主管</div>
        <div class="core-role" data-role="core-role">SUPERVISOR · 全局协调</div>
        <div class="core-statusline">
          <span data-role="core-status-text">系统就绪 · 待命中</span>
          <span class="cs-cursor"></span>
        </div>
        <div class="core-telemetry">
          <div class="ct-stat"><div class="cts-val" data-core="tasks">0</div><div class="cts-lbl">任务</div></div>
          <div class="ct-stat"><div class="cts-val" data-core="mem">0</div><div class="cts-lbl">记忆</div></div>
          <div class="ct-stat"><div class="cts-val" data-core="nodes">0</div><div class="cts-lbl">推理</div></div>
        </div>
        <div class="chat-log" data-role="chat-log"></div>
      </div>`;
    // orbit ticks
    const tick = el.querySelector('[data-role="orbit-tick"]');
    let h=''; for(let i=0;i<24;i++)h+=`<span style="transform:translateX(-50%) rotate(${i*15}deg)"></span>`;
    tick.innerHTML=h;
    ctx._coreEl = el;
  },
  onAgentChange(agentId, el, ctx) {
    const a = ctx.AG(agentId);
    el.querySelector('[data-role="core-avatar"]').textContent = a.emoji;
    el.querySelector('[data-role="core-avatar"]').style.setProperty('--cac', a.color);
    el.querySelector('[data-role="core-name"]').textContent = a.name;
    el.querySelector('[data-role="core-role"]').textContent = `${a.code} · ${a.role}`;
    el.querySelector('[data-role="core-status-text"]').style.color = a.color;
    el.querySelector('.cs-cursor').style.background = a.color;
  },
});

/* ─────────────────────────────────────────────
   WIDGET 4: HARNESS 遥测 (harness)
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'harness',
  title: 'HARNESS 遥测',
  icon: '⛨',
  defaultColumn: 'right',
  size: {},
  events: ['policy_updated', 'shadow_calibrated'],
  render(el) {
    el.innerHTML = `
      <div class="harness-widget">
        <svg class="h-ring-svg" width="72" height="72" viewBox="0 0 72 72">
          <circle class="h-ring-bg" cx="36" cy="36" r="30"/>
          <circle class="h-ring-fg" data-role="h-ring" cx="36" cy="36" r="30" stroke="#ffb347"
            stroke-dasharray="188.5" stroke-dashoffset="188.5"/>
          <text class="h-ring-label" x="36" y="32" fill="#cfe8ff" font-size="16" data-role="h-ring-val">0</text>
          <text class="h-ring-label" x="36" y="46" fill="#3a4d68" font-size="8">/ 20</text>
        </svg>
        <div class="h-info">
          <span class="h-mode shadow" data-role="h-mode">◔ SHADOW 校准</span>
          <div class="h-detail">
            BLOCK 阈值 <b data-role="h-block">0.250</b><br>
            WARN 阈值 <b data-role="h-warn">0.150</b><br>
            Δ 均值 <b data-role="h-avg">—</b>
          </div>
        </div>
      </div>
      <div class="weight-section">
        <div class="wt-row"><span class="wt-lbl">语义距离</span><div class="wt-track"><div class="wt-fill" data-role="wt-sem" style="width:50%;background:#1de9ff;color:#1de9ff"></div></div><span class="wt-pct" data-role="wt-sem-pct">50%</span></div>
        <div class="wt-row"><span class="wt-lbl">工具意图</span><div class="wt-track"><div class="wt-fill" data-role="wt-tool" style="width:30%;background:#b066ff;color:#b066ff"></div></div><span class="wt-pct" data-role="wt-tool-pct">30%</span></div>
        <div class="wt-row"><span class="wt-lbl">范围蔓延</span><div class="wt-track"><div class="wt-fill" data-role="wt-scope" style="width:20%;background:#ffb347;color:#ffb347"></div></div><span class="wt-pct" data-role="wt-scope-pct">20%</span></div>
      </div>`;
  },
  refresh(el, ctx, agentId) {
    ctx.loadHarnessInto(el, agentId);
  },
  onEvent(evt, el, ctx) {
    // policy 更新或校准完成时立即刷新
    ctx.loadHarnessInto(el, ctx.activeAgent);
  },
});

/* ─────────────────────────────────────────────
   WIDGET 5: 推理链 STREAM (thoughtchain)
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'thoughtchain',
  title: '推理链 STREAM',
  icon: '⌬',
  defaultColumn: 'right',
  size: { flex: true },
  render(el) {
    el.innerHTML = `<div class="tc-stream" data-role="tc-stream"><div class="empty">执行任务后显示推理轨迹</div></div>`;
  },
  refresh(el, ctx, agentId) {
    ctx.loadChainInto(el, agentId);
  },
  onAgentChange(agentId, el, ctx) {
    ctx.loadChainInto(el, agentId);
  },
});

/* ─────────────────────────────────────────────
   WIDGET 6: 事件流 LIVE (events)
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'events',
  title: '事件流 · LIVE',
  icon: '⚡',
  defaultColumn: 'right',
  size: { maxH: 180 },
  events: ['harness_block', 'harness_warn', 'policy_updated', 'shadow_calibrated'],
  render(el) {
    el.innerHTML = `<div class="evt-feed" data-role="evt-feed"><div class="empty">监听 Harness 事件...</div></div>`;
  },
  onEvent(evt, el) {
    const feed = el.querySelector('[data-role="evt-feed"]');
    feed.querySelector('.empty')?.remove();
    const cmap={harness_block:'#ff4060',harness_warn:'#ffb347',policy_updated:'#b066ff',shadow_calibrated:'#1dffa6'};
    const color = cmap[evt.type] || '#1de9ff';
    const d = evt.data || {};
    const reason = d.result?.reason || d.rule_id || d.reason || '';
    const item = document.createElement('div');
    item.className = 'evt'; item.style.setProperty('--ec', color);
    item.innerHTML = `
      <div class="evt-top"><span class="evt-type">${evt.type.replace('harness_','').replace('_',' ')}</span>
      <span class="evt-time">${new Date().toLocaleTimeString('en-GB')}</span></div>
      <div class="evt-body">${_esc((d.agent_id||'—')+' · '+reason).slice(0,70)}</div>`;
    feed.insertBefore(item, feed.firstChild);
    while (feed.children.length > 20) feed.lastChild?.remove();
  },
});

/* ─────────────────────────────────────────────
   WIDGET 7: 协同 DAG (dag) — 多 Agent 星舰控制流
   命令 → 主管分发 → 子 Agent → Harness 关卡 → 记忆汇聚
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'dag',
  title: '协同 DAG · 控制流',
  icon: '⊶',
  defaultColumn: 'center',
  size: { maxH: 260 },
  events: ['harness_block', 'harness_warn', 'policy_updated'],
  render(el, ctx) {
    el.innerHTML = `
      <div class="dag-wrap">
        <svg class="dag-svg" viewBox="0 0 640 230" preserveAspectRatio="xMidYMid meet">
          <defs>
            <marker id="dagArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M2 1L8 5L2 9" fill="none" stroke="#3a4d68" stroke-width="1.5" stroke-linecap="round"/>
            </marker>
          </defs>
          <g data-role="dag-edges"></g>
          <g data-role="dag-nodes"></g>
        </svg>
      </div>`;
    ctx._dagEl = el;
    _dagBuild(el, ctx);
  },
  onAgentChange(agentId, el, ctx) {
    _dagSetActive(el, agentId);
  },
  onEvent(evt, el, ctx) {
    if (evt.type === 'harness_block') _dagFlashGate(el, '#ff4060');
    else if (evt.type === 'harness_warn') _dagFlashGate(el, '#ffb347');
    else if (evt.type === 'policy_updated') _dagFlashGate(el, '#b066ff');
  },
});

/* DAG 拓扑定义 */
const _DAG_NODES = {
  command:    { x: 60,  y: 115, label: '指令', icon: '❯', kind: 'entry',  color: '#1de9ff' },
  supervisor: { x: 180, y: 115, label: '主管', icon: '🏢', kind: 'hub',    color: '#1de9ff' },
  assistant:  { x: 320, y: 35,  label: '助手', icon: '🤝', kind: 'agent',  color: '#4d9fff' },
  finance:    { x: 320, y: 90,  label: '财务', icon: '💰', kind: 'agent',  color: '#ffb347' },
  admin:      { x: 320, y: 145, label: '行政', icon: '📅', kind: 'agent',  color: '#ff5e9c' },
  office:     { x: 320, y: 200, label: '办公', icon: '📚', kind: 'agent',  color: '#1dffa6' },
  harness:    { x: 460, y: 115, label: 'HARNESS', icon: '⛨', kind: 'gate', color: '#b066ff' },
  memory:     { x: 580, y: 115, label: 'HMR', icon: '🧠', kind: 'sink',    color: '#1dffa6' },
};
const _DAG_EDGES = [
  ['command','supervisor'],
  ['supervisor','assistant'],['supervisor','finance'],['supervisor','admin'],['supervisor','office'],
  ['assistant','harness'],['finance','harness'],['admin','harness'],['office','harness'],
  ['harness','memory'],['memory','supervisor'],
];

function _dagBuild(el, ctx) {
  const edgesG = el.querySelector('[data-role="dag-edges"]');
  const nodesG = el.querySelector('[data-role="dag-nodes"]');

  // edges
  edgesG.innerHTML = _DAG_EDGES.map(([from, to], i) => {
    const a = _DAG_NODES[from], b = _DAG_NODES[to];
    const curve = (from === 'memory' && to === 'supervisor')
      ? `M${a.x},${a.y+18} C${a.x},${a.y+70} ${b.x},${b.y+70} ${b.x},${b.y+18}`  // return loop below
      : `M${a.x+16},${a.y} C${(a.x+b.x)/2},${a.y} ${(a.x+b.x)/2},${b.y} ${b.x-16},${b.y}`;
    return `<path class="dag-edge" data-edge="${from}-${to}" d="${curve}"
              fill="none" stroke="#1a2942" stroke-width="1.5" marker-end="url(#dagArrow)"/>`;
  }).join('');

  // nodes
  nodesG.innerHTML = Object.entries(_DAG_NODES).map(([id, n]) => {
    const r = n.kind === 'hub' ? 17 : n.kind === 'gate' ? 16 : 14;
    const shape = n.kind === 'gate'
      ? `<rect class="dag-node-shape" data-node="${id}" x="${n.x-r}" y="${n.y-r}" width="${r*2}" height="${r*2}" rx="3"
           fill="rgba(10,20,40,0.9)" stroke="${n.color}" stroke-width="1.5"/>`
      : `<circle class="dag-node-shape" data-node="${id}" cx="${n.x}" cy="${n.y}" r="${r}"
           fill="rgba(10,20,40,0.9)" stroke="${n.color}" stroke-width="1.5"/>`;
    const lbl = n.kind === 'gate'
      ? `<text class="dag-label" x="${n.x}" y="${n.y+r+11}" fill="${n.color}" font-size="8" text-anchor="middle">${n.label}</text>`
      : `<text class="dag-label" x="${n.x}" y="${n.y+r+11}" fill="#6b88aa" font-size="9" text-anchor="middle">${n.label}</text>`;
    return `<g class="dag-node" data-node-g="${id}" style="--dnc:${n.color}">
      ${shape}
      <text class="dag-icon" x="${n.x}" y="${n.y+1}" font-size="13" text-anchor="middle" dominant-baseline="central">${n.icon}</text>
      ${lbl}
    </g>`;
  }).join('');

  _dagSetActive(el, ctx.activeAgent);
}

function _dagSetActive(el, agentId) {
  if (!el) return;
  el.querySelectorAll('.dag-node').forEach(g => {
    g.classList.toggle('dag-dim', false);
  });
  // highlight active agent path: command→supervisor→agent→harness→memory
  const path = ['command','supervisor', agentId, 'harness','memory'];
  el.querySelectorAll('.dag-node').forEach(g => {
    const id = g.dataset.nodeG;
    g.classList.toggle('dag-active', path.includes(id));
  });
}

let _dagAnimSeq = 0;
function _dagPulse(el, agentId) {
  if (!el) return;
  _dagAnimSeq++;
  const seq = _dagAnimSeq;
  const flow = [
    ['command-supervisor', 'command', 'supervisor'],
    [`supervisor-${agentId}`, agentId, null],
    [`${agentId}-harness`, 'harness', null],
    ['harness-memory', 'memory', null],
    ['memory-supervisor', null, null],
  ];
  let i = 0;
  const step = () => {
    if (seq !== _dagAnimSeq) return;  // 被新动画打断
    if (i >= flow.length) { _dagClearFlow(el); return; }
    const [edgeId, nodeId] = flow[i];
    const edge = el.querySelector(`[data-edge="${edgeId}"]`);
    if (edge) {
      edge.classList.add('dag-edge-flow');
      setTimeout(() => edge.classList.remove('dag-edge-flow'), 700);
    }
    if (nodeId) {
      const node = el.querySelector(`[data-node-g="${nodeId}"]`);
      if (node) {
        node.classList.add('dag-pulse');
        setTimeout(() => node.classList.remove('dag-pulse'), 700);
      }
    }
    i++;
    setTimeout(step, 280);
  };
  step();
}

function _dagClearFlow(el) {
  el.querySelectorAll('.dag-edge-flow').forEach(e => e.classList.remove('dag-edge-flow'));
}

function _dagFlashGate(el, color) {
  if (!el) return;
  const gate = el.querySelector('[data-node="harness"]');
  if (!gate) return;
  const g = gate.closest('.dag-node') || gate;
  // 注入一次性 keyframes(如未注入)
  if (!document.getElementById('dag-flash-style')) {
    const st = document.createElement('style');
    st.id = 'dag-flash-style';
    st.textContent = `@keyframes dagGateFlash{
      0%{stroke-width:1.5;filter:none}
      25%{stroke-width:4;filter:drop-shadow(0 0 14px var(--flash-c))}
      60%{stroke-width:3;filter:drop-shadow(0 0 10px var(--flash-c))}
      100%{stroke-width:1.5;filter:none}}
      .dag-gate-flash{animation:dagGateFlash 1.2s ease-out;transform-box:fill-box;transform-origin:center}`;
    document.head.appendChild(st);
  }
  const orig = gate.getAttribute('stroke');
  gate.style.setProperty('--flash-c', color);
  gate.setAttribute('stroke', color);
  gate.classList.remove('dag-gate-flash');
  void gate.offsetWidth;             // 强制重排,允许动画重放
  gate.classList.add('dag-gate-flash');
  // 同时让 HARNESS 文字标签也闪一下
  const label = el.querySelector('.dag-node[data-node-g="harness"] .dag-label');
  if (label) { label.style.fill = color; label.style.fontWeight = '700'; }
  setTimeout(() => {
    gate.setAttribute('stroke', orig);
    gate.classList.remove('dag-gate-flash');
    if (label) { label.style.fill = ''; label.style.fontWeight = ''; }
  }, 1200);
}

/* ─────────────────────────────────────────────
   WIDGET 8: 执行终端 (terminal) — Shell 执行流
   命令回显 · 记忆检索 · 推理 · 工具调用 · Harness 校验
   带打字机流式效果,营造 "AI 正在工作" 的真实感
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'terminal',
  title: '执行终端 · SHELL',
  icon: '▩',
  defaultColumn: 'left',
  size: { maxH: 240, flex: false },
  render(el, ctx) {
    el.innerHTML = `
      <div class="term">
        <div class="term-out" data-role="term-out"></div>
        <div class="term-prompt-line">
          <span class="term-ps">foxclaw@deck</span><span class="term-ps-sep">:</span><span class="term-ps-path" data-role="term-agent">~/supervisor</span><span class="term-ps-dollar">$</span>
          <span class="term-caret"></span>
        </div>
      </div>`;
    ctx._terminalEl = el;
    ctx.termLog = (line, type) => _termWrite(el, line, type);
    ctx.termClear = () => { el.querySelector('[data-role="term-out"]').innerHTML = ''; };
    _termBoot(el);
  },
  onAgentChange(agentId, el, ctx) {
    const a = ctx.AG(agentId);
    const ps = el.querySelector('[data-role="term-agent"]');
    if (ps) ps.textContent = `~/${agentId}`;
    _termWrite(el, `切换激活单元 → ${a.name} [${a.code}]`, 'info');
  },
});

function _termBoot(el) {
  const lines = [
    ['FOXCLAW DECK · 执行终端 v1.0', 'info'],
    ['初始化 HMR 认知运行时...', 'out'],
    ['挂载记忆层 [Vector · Graph · KV]', 'ok'],
    ['Harness 线束在线 · Shadow 校准模式', 'ok'],
    ['五单元编队就绪 · 等待指令', 'info'],
  ];
  let i = 0;
  const tick = () => {
    if (i >= lines.length) return;
    _termWrite(el, lines[i][0], lines[i][1]);
    i++; setTimeout(tick, 220);
  };
  tick();
}

function _termWrite(el, text, type = 'out', stream = false) {
  const out = el.querySelector('[data-role="term-out"]');
  if (!out) return;
  const prefix = {
    cmd:  '❯ ',
    info: '◇ ',
    ok:   '✓ ',
    warn: '▲ ',
    err:  '✕ ',
    tool: '⚙ ',
    out:  '  ',
  }[type] || '  ';
  const line = document.createElement('div');
  line.className = `term-line tl-${type}`;
  const ts = new Date().toLocaleTimeString('en-GB');
  line.innerHTML = `<span class="tl-ts">${ts}</span><span class="tl-pfx">${prefix}</span><span class="tl-txt"></span>`;
  out.appendChild(line);
  const txtEl = line.querySelector('.tl-txt');

  if (stream) {
    let j = 0;
    const speed = 14;
    const type_ = () => {
      txtEl.textContent = text.slice(0, j);
      j++;
      out.scrollTop = out.scrollHeight;
      if (j <= text.length) setTimeout(type_, speed);
    };
    type_();
  } else {
    txtEl.textContent = text;
  }
  out.scrollTop = out.scrollHeight;
  // 限制行数
  while (out.children.length > 80) out.firstChild?.remove();
}

/* ─────────────────────────────────────────────
   WIDGET 9: AI 浏览器 (browser) — 网页操作可视化
   模拟 Agent 浏览网页:地址栏 · 加载流 · 操作高亮(点击/输入/滚动)
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'browser',
  title: 'AI 浏览器 · BROWSE',
  icon: '◍',
  defaultColumn: 'center',
  size: { maxH: 300, flex: false },
  render(el, ctx) {
    el.innerHTML = `
      <div class="brz">
        <div class="brz-bar">
          <div class="brz-dots"><span></span><span></span><span></span></div>
          <div class="brz-url" data-role="brz-url">about:standby</div>
          <div class="brz-status" data-role="brz-status">IDLE</div>
        </div>
        <div class="brz-progress" data-role="brz-progress"></div>
        <div class="brz-viewport" data-role="brz-viewport">
          <div class="brz-empty">浏览器待命 · 等待 Agent 导航指令</div>
        </div>
      </div>`;
    ctx._browserEl = el;
    ctx.browseTo = (url, actions) => _brzNavigate(el, url, actions);
  },
});

function _brzNavigate(el, url, actions) {
  const urlEl = el.querySelector('[data-role="brz-url"]');
  const statusEl = el.querySelector('[data-role="brz-status"]');
  const progEl = el.querySelector('[data-role="brz-progress"]');
  const vp = el.querySelector('[data-role="brz-viewport"]');
  if (!vp) return;

  urlEl.textContent = url;
  statusEl.textContent = 'LOADING';
  statusEl.className = 'brz-status loading';

  // 进度条动画
  progEl.style.width = '0%';
  progEl.style.opacity = '1';
  setTimeout(() => progEl.style.width = '70%', 80);
  setTimeout(() => { progEl.style.width = '100%'; }, 700);
  setTimeout(() => { progEl.style.opacity = '0'; statusEl.textContent = 'LOADED'; statusEl.className = 'brz-status loaded'; }, 950);

  // 渲染一个抽象的"网页骨架" + 操作高亮
  vp.innerHTML = `
    <div class="brz-page">
      <div class="brz-skel brz-skel-title"></div>
      <div class="brz-skel brz-skel-nav">
        <span></span><span></span><span></span><span></span>
      </div>
      <div class="brz-hero"></div>
      <div class="brz-skel brz-skel-line w90"></div>
      <div class="brz-skel brz-skel-line w75"></div>
      <div class="brz-skel brz-skel-line w85"></div>
      <div class="brz-cards">
        <div class="brz-card"></div><div class="brz-card"></div><div class="brz-card"></div>
      </div>
      <div class="brz-cursor" data-role="brz-cursor">➤</div>
    </div>`;

  // AI 操作序列高亮
  const acts = actions || [
    { type: 'scan', label: '解析页面结构', x: 50, y: 20 },
    { type: 'click', label: '点击主内容区', x: 30, y: 45 },
    { type: 'read', label: '提取关键信息', x: 60, y: 65 },
    { type: 'extract', label: '写入记忆库', x: 50, y: 80 },
  ];
  const cursor = vp.querySelector('[data-role="brz-cursor"]');
  let i = 0;
  const runAct = () => {
    if (i >= acts.length || !cursor) { if (cursor) cursor.style.opacity = '0'; return; }
    const a = acts[i];
    cursor.style.opacity = '1';
    cursor.style.left = a.x + '%';
    cursor.style.top = a.y + '%';
    cursor.setAttribute('data-label', a.label);
    cursor.className = `brz-cursor act-${a.type}`;
    // 涟漪
    const ripple = document.createElement('div');
    ripple.className = 'brz-ripple';
    ripple.style.left = a.x + '%';
    ripple.style.top = a.y + '%';
    vp.querySelector('.brz-page')?.appendChild(ripple);
    setTimeout(() => ripple.remove(), 600);
    i++;
    setTimeout(runAct, 750);
  };
  setTimeout(runAct, 1000);
}

/* ─────────────────────────────────────────────
   WIDGET 10: 语音输入 (voice) — 麦克风波形 + 语音识别
   Web Audio API 真实波形 + 浏览器原生 SpeechRecognition
   识别结果可直接作为指令传输给激活 Agent
───────────────────────────────────────────── */
WidgetRuntime.register({
  id: 'voice',
  title: '语音输入 · VOICE',
  icon: '◉',
  defaultColumn: 'right',
  size: { maxH: 200, flex: false },
  render(el, ctx) {
    el.innerHTML = `
      <div class="voice">
        <canvas class="voice-wave" data-role="voice-wave"></canvas>
        <div class="voice-transcript" data-role="voice-transcript">点击麦克风开始语音指令</div>
        <div class="voice-controls">
          <button class="voice-mic" data-role="voice-mic" title="开始/停止录音">◉</button>
          <div class="voice-meta">
            <div class="voice-state" data-role="voice-state">待命</div>
            <div class="voice-hint">支持中文 · 识别后可一键传输</div>
          </div>
          <button class="voice-send" data-role="voice-send" disabled title="传输识别结果">传输 ❯</button>
        </div>
      </div>`;
    _voiceInit(el, ctx);
  },
});

function _voiceInit(el, ctx) {
  const canvas = el.querySelector('[data-role="voice-wave"]');
  const transcriptEl = el.querySelector('[data-role="voice-transcript"]');
  const micBtn = el.querySelector('[data-role="voice-mic"]');
  const sendBtn = el.querySelector('[data-role="voice-send"]');
  const stateEl = el.querySelector('[data-role="voice-state"]');

  let audioCtx = null, analyser = null, dataArr = null, rafId = null, stream = null;
  let recognition = null, recognizing = false, finalText = '';

  // 语音识别(浏览器原生)
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SR) {
    recognition = new SR();
    recognition.lang = 'zh-CN';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (e) => {
      let interim = '', final = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) final += t; else interim += t;
      }
      if (final) finalText += final;
      transcriptEl.textContent = (finalText + interim) || '聆听中...';
      transcriptEl.classList.add('has-text');
      sendBtn.disabled = !(finalText || interim).trim();
    };
    recognition.onerror = (e) => { stateEl.textContent = '识别错误: ' + e.error; };
  } else {
    transcriptEl.textContent = '此浏览器不支持语音识别(需 Chrome/Edge)';
  }

  function drawWave() {
    if (!analyser) return;
    rafId = requestAnimationFrame(drawWave);
    analyser.getByteTimeDomainData(dataArr);
    const w = canvas.width, h = canvas.height;
    const cx = canvas.getContext('2d');
    cx.clearRect(0, 0, w, h);
    cx.lineWidth = 2;
    cx.strokeStyle = '#1de9ff';
    cx.shadowBlur = 8; cx.shadowColor = 'rgba(29,233,255,0.6)';
    cx.beginPath();
    const slice = w / dataArr.length;
    let x = 0;
    for (let i = 0; i < dataArr.length; i++) {
      const v = dataArr[i] / 128.0;
      const y = (v * h) / 2;
      if (i === 0) cx.moveTo(x, y); else cx.lineTo(x, y);
      x += slice;
    }
    cx.lineTo(w, h / 2);
    cx.stroke();
  }

  async function startMic() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      dataArr = new Uint8Array(analyser.frequencyBinCount);
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      canvas.width = canvas.offsetWidth; canvas.height = canvas.offsetHeight;
      drawWave();
      if (recognition) { finalText = ''; recognition.start(); }
      recognizing = true;
      micBtn.classList.add('recording');
      stateEl.textContent = '● 录音中';
      stateEl.style.color = '#ff4060';
    } catch (err) {
      stateEl.textContent = '麦克风权限被拒';
      transcriptEl.textContent = '无法访问麦克风 · 请检查浏览器权限';
    }
  }

  function stopMic() {
    recognizing = false;
    micBtn.classList.remove('recording');
    stateEl.textContent = '待命';
    stateEl.style.color = '';
    if (rafId) cancelAnimationFrame(rafId);
    if (recognition) try { recognition.stop(); } catch {}
    if (stream) stream.getTracks().forEach(t => t.stop());
    if (audioCtx) try { audioCtx.close(); } catch {}
    const cx = canvas.getContext('2d');
    if (cx) cx.clearRect(0, 0, canvas.width, canvas.height);
  }

  micBtn.onclick = () => recognizing ? stopMic() : startMic();
  sendBtn.onclick = () => {
    const text = transcriptEl.textContent.trim();
    if (!text || text === '聆听中...') return;
    stopMic();
    // 把识别结果填入控制台并传输
    const input = document.getElementById('consoleInput');
    if (input) { input.value = text; }
    if (typeof transmit === 'function') transmit();
    transcriptEl.textContent = '已传输 → 控制台';
    transcriptEl.classList.remove('has-text');
    sendBtn.disabled = true;
  };
}
