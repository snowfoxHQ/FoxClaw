/* ════════════════════════════════════════════════════════════
   FOXCLAW DECK — Widget Runtime
   可插拔 widget 系统。新模块只需 registerWidget() 一次。
   ════════════════════════════════════════════════════════════ */

const WidgetRuntime = (() => {

  // ── 注册表:定义有哪些 widget 类型 ──
  const registry = new Map();

  // ── 实例:当前布局中存在的 widget ──
  // { instanceId, widgetId, column, order }
  let instances = [];

  // ── 事件订阅:widgetId -> Set<eventType> ──
  const eventSubs = new Map();

  // ── 共享上下文,所有 widget 都能访问 ──
  let ctx = {};

  // ── DOM 列引用 ──
  const columns = {};

  /* ───────────────────────────────────────────
     注册一个 widget 类型
     spec = {
       id, title, icon,
       defaultColumn: 'left'|'center'|'right',
       size: {minH, maxH, flex},
       events: ['harness_block', ...],   // 关心的事件类型
       render(el, ctx, instance),         // 渲染主体
       onEvent(evt, el, ctx),             // 收到订阅事件
       onAgentChange(agentId, el, ctx),   // 激活 Agent 变化
       refresh(el, ctx, agentId),         // 数据刷新
     }
  ─────────────────────────────────────────── */
  function register(spec) {
    if (!spec.id) throw new Error('widget 必须有 id');
    registry.set(spec.id, {
      defaultColumn: 'right',
      size: { flex: false },
      events: [],
      render: () => {},
      onEvent: null,
      onAgentChange: null,
      refresh: null,
      ...spec,
    });
    // 建立事件订阅索引
    (spec.events || []).forEach(ev => {
      if (!eventSubs.has(ev)) eventSubs.set(ev, new Set());
      eventSubs.get(ev).add(spec.id);
    });
  }

  /* 初始化:绑定列容器 + 共享上下文 */
  function init(columnEls, sharedCtx) {
    columns.left   = columnEls.left;
    columns.center = columnEls.center;
    columns.right  = columnEls.right;
    ctx = sharedCtx || {};
  }

  /* 设置布局(从默认或 localStorage 恢复) */
  function setLayout(layout) {
    instances = layout.map((item, i) => ({
      instanceId: item.instanceId || `inst_${item.widgetId}_${i}`,
      widgetId: item.widgetId,
      column: item.column,
      order: item.order != null ? item.order : i,
    }));
    renderAll();
  }

  /* 默认布局:每个注册的 widget 放进它的 defaultColumn */
  function defaultLayout() {
    return [...registry.values()].map((spec, i) => ({
      instanceId: `inst_${spec.id}`,
      widgetId: spec.id,
      column: spec.defaultColumn,
      order: i,
    }));
  }

  /* 渲染全部实例 */
  function renderAll() {
    Object.values(columns).forEach(col => { if (col) col.innerHTML = ''; });
    const byCol = { left: [], center: [], right: [] };
    instances.forEach(inst => {
      if (byCol[inst.column]) byCol[inst.column].push(inst);
    });
    Object.keys(byCol).forEach(colName => {
      byCol[colName]
        .sort((a, b) => a.order - b.order)
        .forEach(inst => mountWidget(inst, colName));
    });
  }

  /* 挂载单个 widget 到 DOM */
  function mountWidget(inst, colName) {
    const spec = registry.get(inst.widgetId);
    if (!spec) return;
    const col = columns[colName];
    if (!col) return;

    const panel = document.createElement('div');
    panel.className = 'panel';
    panel.dataset.widget = inst.widgetId;
    panel.dataset.instance = inst.instanceId;
    if (spec.size.flex) panel.style.flex = '1';
    if (spec.size.maxH) panel.style.maxHeight = spec.size.maxH + 'px';

    panel.innerHTML = `
      <div class="panel-corner-tr"></div><div class="panel-corner-bl"></div>
      <div class="panel-head">
        <span class="ph-icon">${spec.icon || '◈'}</span>
        <span class="ph-title">${spec.title}</span>
        <span class="ph-status" data-role="status"></span>
        <span class="ph-drag" title="拖动">⠿</span>
        <span class="ph-remove" title="移除" style="display:none">✕</span>
      </div>
      <div class="panel-body" data-role="body"></div>`;

    col.appendChild(panel);

    const body = panel.querySelector('[data-role="body"]');
    inst._el = panel;
    inst._body = body;

    // 移除按钮
    panel.querySelector('.ph-remove').onclick = (e) => {
      e.stopPropagation();
      removeInstance(inst.instanceId);
    };

    // 调用 widget 自己的渲染
    try {
      spec.render(body, ctx, inst);
    } catch (err) {
      body.innerHTML = `<div class="empty">渲染错误: ${err.message}</div>`;
    }

    // 首次同步当前 Agent
    if (spec.onAgentChange && ctx.activeAgent) {
      try { spec.onAgentChange(ctx.activeAgent, body, ctx); } catch {}
    }
  }

  /* 事件分发:把事件发给所有订阅的 widget 实例 */
  function dispatch(eventType, payload) {
    const subscribers = eventSubs.get(eventType);
    if (!subscribers) return;
    instances.forEach(inst => {
      if (!subscribers.has(inst.widgetId) || !inst._body) return;
      const spec = registry.get(inst.widgetId);
      if (spec.onEvent) {
        try { spec.onEvent({ type: eventType, ...payload }, inst._body, ctx); } catch {}
      }
    });
  }

  /* 通知所有 widget:激活 Agent 变了 */
  function notifyAgentChange(agentId) {
    ctx.activeAgent = agentId;
    instances.forEach(inst => {
      const spec = registry.get(inst.widgetId);
      if (spec.onAgentChange && inst._body) {
        try { spec.onAgentChange(agentId, inst._body, ctx); } catch {}
      }
    });
  }

  /* 刷新所有 widget 的数据 */
  function refreshAll(agentId) {
    instances.forEach(inst => {
      const spec = registry.get(inst.widgetId);
      if (spec.refresh && inst._body) {
        try { spec.refresh(inst._body, ctx, agentId); } catch {}
      }
    });
  }

  /* 设置某个实例的 head status 文本 */
  function setStatus(instanceId, text) {
    const inst = instances.find(i => i.instanceId === instanceId);
    if (inst && inst._el) {
      const s = inst._el.querySelector('[data-role="status"]');
      if (s) s.textContent = text;
    }
  }

  /* ── 增删实例 ── */
  function addInstance(widgetId, column) {
    const spec = registry.get(widgetId);
    if (!spec) return;
    const inst = {
      instanceId: `inst_${widgetId}_${Date.now()}`,
      widgetId,
      column: column || spec.defaultColumn,
      order: instances.length,
    };
    instances.push(inst);
    renderAll();
    saveLayout();
    if (ctx.activeAgent) {
      if (spec.onAgentChange) try { spec.onAgentChange(ctx.activeAgent, inst._body, ctx); } catch {}
      if (spec.refresh) try { spec.refresh(inst._body, ctx, ctx.activeAgent); } catch {}
    }
  }

  function removeInstance(instanceId) {
    instances = instances.filter(i => i.instanceId !== instanceId);
    renderAll();
    saveLayout();
  }

  /* ── 拖拽:重排实例 ── */
  function captureLayoutFromDOM() {
    const newInstances = [];
    ['left', 'center', 'right'].forEach(colName => {
      const col = columns[colName];
      if (!col) return;
      [...col.children].forEach((panel, order) => {
        const instanceId = panel.dataset.instance;
        const widgetId = panel.dataset.widget;
        const existing = instances.find(i => i.instanceId === instanceId);
        newInstances.push({
          instanceId,
          widgetId,
          column: colName,
          order,
          _el: panel,
          _body: panel.querySelector('[data-role="body"]'),
          ...(existing ? {} : {}),
        });
      });
    });
    instances = newInstances;
    saveLayout();
  }

  /* ── 持久化 ── */
  function saveLayout() {
    try {
      const layout = instances.map(i => ({
        instanceId: i.instanceId,
        widgetId: i.widgetId,
        column: i.column,
        order: i.order,
      }));
      localStorage.setItem('foxclaw_widget_layout', JSON.stringify(layout));
    } catch {}
  }

  function loadLayout() {
    try {
      const saved = JSON.parse(localStorage.getItem('foxclaw_widget_layout'));
      if (saved && saved.length) {
        // 过滤掉已不存在的 widget 类型
        const valid = saved.filter(i => registry.has(i.widgetId));
        if (valid.length) { setLayout(valid); return true; }
      }
    } catch {}
    return false;
  }

  /* ── 编辑模式:显示/隐藏移除按钮 ── */
  function setEditMode(on) {
    instances.forEach(inst => {
      if (!inst._el) return;
      const rm = inst._el.querySelector('.ph-remove');
      if (rm) rm.style.display = on ? 'block' : 'none';
    });
  }

  /* ── 面板库:列出未激活的 widget 供添加 ── */
  function getLibrary() {
    return [...registry.values()].map(spec => ({
      id: spec.id,
      title: spec.title,
      icon: spec.icon,
      defaultColumn: spec.defaultColumn,
    }));
  }

  /* 公开 API */
  return {
    register, init, setLayout, defaultLayout, renderAll,
    dispatch, notifyAgentChange, refreshAll, setStatus,
    addInstance, removeInstance, captureLayoutFromDOM,
    saveLayout, loadLayout, setEditMode, getLibrary,
    get instances() { return instances; },
    get ctx() { return ctx; },
  };
})();
