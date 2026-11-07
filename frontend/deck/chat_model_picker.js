// chat_model_picker.js — chat 输入框旁的「下拉式模型选择器」
//
// 视觉:参考图1 (CodeBuddy 模型列表) + 图2 (「添加自定义」对话框)。
// 数据:全部走 foxclaw/api/routes/llm_admin.py 的 REST 端点(GET/POST/DELETE)。
// 状态:切换写入 FOXCLAW_DATA_DIR/.current_model,model_router 用 mtime 缓存 lazy reload,
//     用户改完**无需重启 foxclaw**,下次 chat 自动走新模型。
//
// 不会外泄:GET /api/llm/models 只返 has_key 布尔;POST /api/llm/custom/add 只在服务端保存。

const API_BASE = (typeof window !== 'undefined' && window.API_BASE) || '';
const _api = (p) => `${API_BASE}/api/llm${p}`;

let _models = { providers: [], custom: [], current: null };
let _menuOpen = false;

// ── 工具 ─────────────────────────────────────────────

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// BASE URL 规范化(与后端 core/model_router.normalize_base_url 同规则):
// 无 scheme → 补 https://;path 为空 → 补 /v1;已有 path(/v1、/compatible-mode/v1…)原样保留。
// 病根:用户照厂商文档填 https://api.deepseek.com,SDK 拼出 /chat/completions → 多数网关 404/401,
// 用户以为是"模型填不上"。前端先收拾干净,后端再兜一层(旧记录 / 直接调 API 的场景)。
function _normalizeBaseUrl(u) {
  let s = String(u || '').trim();
  if (!s) return s;
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(s)) s = 'https://' + s;
  let parsed;
  try { parsed = new URL(s); } catch { return s; }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return s;
  let path = parsed.pathname.replace(/\/+$/, '');
  if (!path) path = '/v1';
  return `${parsed.protocol}//${parsed.host}${path}${parsed.search || ''}`;
}

// 常见厂商的模型 ID 提示(只做 placeholder 引导,不阻断保存 —— 自建网关可能有自定义名)
const _MODEL_HINTS = [
  { re: /api\.deepseek\.com/i,  hint: 'deepseek-chat',   note: 'DeepSeek 官方 ID:deepseek-chat / deepseek-reasoner' },
  { re: /api\.openai\.com/i,    hint: 'gpt-4o-mini',     note: 'OpenAI 官方 ID:gpt-4o-mini / gpt-4o' },
  { re: /dashscope\.aliyuncs/i, hint: 'qwen-plus',       note: '通义千问 ID:qwen-plus / qwen-max' },
  { re: /minimax/i,             hint: 'MiniMax-Text-01', note: 'MiniMax ID:MiniMax-Text-01 / abab6.5s-chat' },
];

function _hintForBaseUrl(u) {
  const s = String(u || '');
  for (const h of _MODEL_HINTS) if (h.re.test(s)) return h;
  return null;
}

function _isCustomCurrent() {
  return _models.current && _models.current.source === 'custom';
}
function _isPresetCurrent() {
  return _models.current && _models.current.source === 'preset';
}
function _autoCurrent() {
  return !_models.current;
}

// 当前显示在 pill 上的名称 + 颜色
function _currentLabel() {
  if (_isCustomCurrent()) {
    const m = _models.custom.find(m => m.name === _models.current.name);
    return { name: m ? m.name : _models.current.name,
             color: '#a78bfa', isCustom: true };
  }
  if (_isPresetCurrent()) {
    const p = _models.providers.find(p => p.provider === _models.current.provider);
    return { name: p ? p.label : _models.current.provider,
             color: p ? p.color : '#7e93b8', isCustom: false };
  }
  return { name: 'Auto', color: '#7e93b8', isCustom: false };
}

// ── 渲染:菜单内容 ─────────────────────────────────────

function renderMenu() {
  const menu = document.getElementById('chatModelMenu');
  if (!menu) return;
  const cur = _currentLabel();
  let h = '';

  // Auto(让用户能清掉选择,退回 .env 默认)
  h += `<div class="mp-item ${_autoCurrent() ? 'active' : ''}" data-act="auto">
    <span class="mp-dot" style="background:#7e93b8"></span>
    <span class="mp-name"><b>Auto</b><span>跟随 .env 默认</span></span>
    ${_autoCurrent() ? '<span class="mp-tag">当前</span>' : ''}
  </div>`;

  h += '<div class="mp-sep">预置</div>';
  _models.providers.forEach(p => {
    const isCur = _isPresetCurrent() && _models.current.provider === p.provider;
    const isDis = !p.configured;
    h += `<div class="mp-item ${isCur ? 'active' : ''} ${isDis ? 'disabled' : ''}" data-act="preset" data-prov="${_esc(p.provider)}">
      <span class="mp-dot" style="background:${p.color};box-shadow:0 0 6px ${p.color}"></span>
      <span class="mp-name"><b>${_esc(p.label)}</b><span>${_esc(p.provider)}</span></span>
      ${isCur ? '<span class="mp-tag mp-cur">当前</span>' :
        isDis ? '<span class="mp-tag mp-warn">未配置</span>' : ''}
    </div>`;
  });

  if (_models.custom.length) {
    h += '<div class="mp-sep">自定义</div>';
    _models.custom.forEach(m => {
      const isCur = _isCustomCurrent() && _models.current.name === m.name;
      // 三个 tag 都常驻:当前(按需显隐)+ ✎ 编辑 + ✕ 删除
      // 即使是当前选中的,用户也应该能直接编辑它(改 key / 加 group_id),不能强制切走
      // 删除当前选中的也安全 —— 后端 DELETE 已处理清 .current_model
      h += `<div class="mp-item ${isCur ? 'active' : ''}" data-act="custom" data-name="${_esc(m.name)}">
        <span class="mp-dot" style="background:#a78bfa;box-shadow:0 0 6px #a78bfa"></span>
        <span class="mp-name"><b>${_esc(m.name)}</b><span>${_esc(m.model)}</span></span>
        <span class="mp-tag mp-cur${isCur ? '' : ' hidden'}">当前</span>
        <span class="mp-tag mp-edit" title="编辑(key / base_url / group_id / 模型 ID)">✎</span>
        <span class="mp-tag mp-del" title="删除">✕</span>
      </div>`;
    });
  }

  h += `<div class="mp-foot">
    <button class="mp-btn" data-foot="add">＋ 添加自定义模型</button>
    <button class="mp-btn" data-foot="env">📂 打开配置文件</button>
  </div>`;

  menu.innerHTML = h;

  // 事件
  menu.querySelectorAll('.mp-item').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('mp-del')) {
        e.stopPropagation();
        const name = el.dataset.name;
        if (confirm(`删除自定义模型「${name}」?`)) deleteCustom(name);
        return;
      }
      if (e.target.classList.contains('mp-edit')) {
        e.stopPropagation();
        editCustom(el.dataset.name);
        return;
      }
      const act = el.dataset.act;
      if (act === 'auto')       selectModel('auto', null, null);
      else if (act === 'preset') selectModel('preset', el.dataset.prov, null);
      else if (act === 'custom') selectModel('custom', null, el.dataset.name);
    });
  });
  menu.querySelectorAll('.mp-foot .mp-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (btn.dataset.foot === 'add') openAddDialog();
      else if (btn.dataset.foot === 'env') window.open('/.env', '_blank');
    });
  });
}

function renderPill() {
  const cur = _currentLabel();
  const dot  = document.getElementById('chatModelDot');
  const name = document.getElementById('chatModelName');
  if (dot)  dot.style.cssText = `background:${cur.color};box-shadow:0 0 6px ${cur.color}`;
  if (name) name.textContent = cur.name;
}

// ── API ─────────────────────────────────────────────

async function loadModels() {
  try {
    const r = await fetch(_api('/models'));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    _models = await r.json();
  } catch (e) {
    console.warn('[chat_model_picker] load /models failed', e);
    _models = { providers: [], custom: [], current: null };
  }
  renderPill();
  renderMenu();
}

async function selectModel(source, provider, name) {
  try {
    const body = { source };
    if (source === 'preset' && provider) body.provider = provider;
    if (source === 'custom' && name)     body.name = name;
    const r = await fetch(_api('/select'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch {}
      alert(`切换失败: ${msg}`);
      return false;
    }
    const j = await r.json();
    _models.current = j.current;
    _menuOpen = false;
    document.getElementById('chatModelMenu')?.classList.remove('show');
    renderPill();
    renderMenu();
    return true;
  } catch (e) {
    alert(`切换失败: ${e.message || e}`);
    return false;
  }
}

async function deleteCustom(name) {
  try {
    const r = await fetch(_api(`/custom/${encodeURIComponent(name)}`), { method: 'DELETE' });
    if (!r.ok) { alert('删除失败'); return; }
    await loadModels();
  } catch (e) { alert(`删除失败: ${e.message || e}`); }
}

async function addCustom(payload) {
  const r = await fetch(_api('/custom/add'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch {}
    throw new Error(msg);
  }
}

async function getCustom(name) {
  // 取单个自定义模型详情(后端不返回 api_key 明文,只返 has_key)
  const r = await fetch(_api(`/custom/${encodeURIComponent(name)}`));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function updateCustom(name, payload) {
  // payload 字段语义:填了非空 = 用新值;没传(或不传该字段)= 不变;api_key 留空=保留原值
  const r = await fetch(_api(`/custom/${encodeURIComponent(name)}`), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

async function editCustom(name) {
  // 加载详情 → 复用 openAddDialog 切到 edit 模式预填表单
  try {
    const m = await getCustom(name);
    openAddDialog('edit', { ...m, _editingName: name });
  } catch (e) {
    alert(`加载详情失败: ${e.message || e}`);
  }
}

async function testModel(source, provider, name) {
  const body = { source };
  if (source === 'preset' && provider) body.provider = provider;
  if (source === 'custom' && name)     body.name = name;
  const r = await fetch(_api('/test'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── 添加对话框(图2 风格)────────────────────────────

// 对话框全局状态:add / edit 模式共用同一表单
let _cmMode = 'add';          // 'add' | 'edit'
let _cmEditingName = null;    // edit 模式下的原名(PUT URL 用);改名后仍指向磁盘上那条记录

function openAddDialog(mode = 'add', preset = null) {
  _cmMode = mode;
  _cmEditingName = (mode === 'edit' && preset)
    ? (preset._editingName || preset.name)
    : null;

  const mask = document.getElementById('cmAddMask');
  if (!mask) return;

  // 标题 + 副标题
  const title = document.getElementById('cmAddTitle');
  if (title) {
    title.textContent = mode === 'edit' && preset?.name
      ? `编辑自定义模型 · ${preset.name}`
      : '添加自定义模型';
  }

  // 填表单 — api_key 永远不清空回填(安全考虑)
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
  set('cm_name',          preset?.name);
  set('cm_base_url',      preset?.base_url);
  set('cm_api_key',       '');
  set('cm_model',         preset?.model);
  set('cm_group_id',      preset?.group_id);
  set('cm_extra_headers', preset?.extra_headers && Object.keys(preset.extra_headers).length
    ? JSON.stringify(preset.extra_headers) : '');
  set('cm_note',          preset?.note);
  _applyModelHint(preset?.base_url);

  // API KEY 字段 placeholder 区分 add / edit
  const keyInp = document.getElementById('cm_api_key');
  if (keyInp) {
    keyInp.placeholder = (mode === 'edit' && preset?.has_key)
      ? '留空保持原值;填入则覆盖'
      : '请输入 API Key';
  }

  const msg = document.getElementById('cm_msg');
  if (msg) { msg.textContent = ''; msg.className = 'cm-msg'; }
  mask.classList.add('show');
  setTimeout(() => document.getElementById('cm_name')?.focus(), 50);
}

// 已知厂商的"模型 ID 写错"软提示(只警告不阻断 —— 自建网关可以有自定义别名)
function _modelWarning(baseUrl, modelId) {
  const s = String(baseUrl || ''), m = String(modelId || '').trim();
  if (!m) return '';
  if (/api\.deepseek\.com/i.test(s) && !/^deepseek-(chat|reasoner)$/.test(m))
    return `⚠ DeepSeek 官方没有「${m}」,应填 deepseek-chat 或 deepseek-reasoner`;
  return '';
}

// 按 BASE URL 域名更新「模型 ID」的 placeholder + 副标题提示(不阻断保存)
function _applyModelHint(baseUrl) {
  const hint = _hintForBaseUrl(baseUrl);
  const inp = document.getElementById('cm_model');
  if (inp) inp.placeholder = hint ? `例:${hint.hint}` : '例:MiniMax-Text-01';
  const sub = document.getElementById('cmAddSubHint');
  if (sub) {
    const warn = _modelWarning(baseUrl, inp?.value);
    sub.textContent = warn || (hint ? hint.note : '');
    sub.style.color = warn ? '#e0685a' : '#e0a45c';
  }
}

function closeAddDialog() {
  document.getElementById('cmAddMask')?.classList.remove('show');
}

function _setMsg(text, kind) {
  const msg = document.getElementById('cm_msg');
  if (!msg) return;
  msg.textContent = text;
  msg.className = 'cm-msg' + (kind ? ' cm-msg-' + kind : '');
}

function _readForm() {
  const v = (id) => (document.getElementById(id)?.value || '').trim();
  const out = {
    name:         v('cm_name'),
    // 保存前再规范化一次(用户可能没触发 blur 直接点保存);后端也会兜一层
    base_url:     _normalizeBaseUrl(v('cm_base_url')),
    api_key:      v('cm_api_key'),
    model:        v('cm_model'),
    group_id:     v('cm_group_id'),
    note:         v('cm_note'),
  };
  const raw = v('cm_extra_headers');
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed === 'object' && parsed && !Array.isArray(parsed)) {
        out.extra_headers = parsed;
      } else { throw new Error('必须是 JSON object'); }
    } catch (e) {
      throw new Error(`额外 header JSON 不合法: ${e.message}`);
    }
  }
  return out;
}

async function _onSave() {
  try {
    const payload = _readForm();
    // name / base_url / model 必填;api_key 在 edit 模式下留空 = 保留(后端校验)
    if (!payload.name || !payload.base_url || !payload.model) {
      _setMsg('请填全 * 号必填项', 'err');
      return;
    }
    if (_cmMode === 'add' && !payload.api_key) {
      _setMsg('add 模式必须填 API KEY(edit 模式可留空保留原值)', 'err');
      return;
    }
    _setMsg('保存中…', 'info');
    let savedName = payload.name;
    if (_cmMode === 'add') {
      await addCustom(payload);
      savedName = payload.name;
    } else {
      const r = await updateCustom(_cmEditingName, payload);
      savedName = r.name;
      // 改名后,_cmEditingName 失效 → 下次 _onTest 会失败 → 清掉,避免误测
      _cmEditingName = r.name;
    }
    await loadModels();
    // 保存即生效 —— 用户点「保存」的诉求就是「用它」。
    // 病根:此前只写盘不切换,聊天仍走 .env 的旧 provider,用户看到 AuthenticationError/stub,
    // 以为是"模型填不上";实际上是要再回菜单点一次这个条目。现在省掉这一步。
    _setMsg('已保存,正在切换…', 'info');
    const ok = await selectModel('custom', null, savedName);
    _setMsg(ok ? `已保存并切换到 ${savedName}` : `已保存 ${savedName},但切换失败(见弹窗)`,
            ok ? 'ok' : 'err');
    setTimeout(closeAddDialog, 600);
  } catch (e) {
    _setMsg(`保存失败: ${e.message || e}`, 'err');
  }
}

async function _onTest() {
  try {
    const payload = _readForm();
    if (!payload.base_url || !payload.model) {
      _setMsg('请先填 BASE URL / 模型 ID 再测', 'err');
      return;
    }
    if (_cmMode === 'edit') {
      // edit 模式:测磁盘上**已保存**的配置(避免被 form 里未保存的改动误导)
      // 若想测表单里的新值,先点「保存」再点「测试连通性」
      _setMsg(`测试已保存配置(${_cmEditingName})…`, 'info');
      const r = await testModel('custom', null, _cmEditingName);
      if (r.ok) {
        _setMsg(`✓ 连通 (${r.latency_ms} ms, model=${r.model}, reply="${(r.reply || '').slice(0, 30)}")`, 'ok');
      } else {
        _setMsg(`✗ 连通失败: ${(r.error || '').slice(0, 200)}\n(测的是已保存配置;要测表单里的新值请先点「保存」)`, 'err');
      }
      return;
    }
    // add 模式:必须 name + api_key,临时 add 一个 tmp 名测,失败保留
    if (!payload.name || !payload.api_key) {
      _setMsg('add 模式请先填「模型名称」+「API KEY」再测试', 'err');
      return;
    }
    _setMsg('保存并测试中…', 'info');
    await addCustom(payload);
    const r = await testModel('custom', null, payload.name);
    if (r.ok) {
      // 测试通过 = 这份配置真的能用 → 直接切过去,不用用户再回菜单点一次
      await loadModels();
      await selectModel('custom', null, payload.name);
      _setMsg(`✓ 连通 (${r.latency_ms} ms, model=${r.model}) — 已切换`, 'ok');
    } else {
      _setMsg(`✗ 连通失败: ${(r.error || '').slice(0, 200)}\n(已为你保留,可手动改后再测或删除)`, 'err');
    }
    await loadModels();
  } catch (e) {
    _setMsg(`测试失败: ${e.message || e}`, 'err');
  }
}

// ── 事件绑定 ─────────────────────────────────────

function bindEvents() {
  const pill = document.getElementById('chatModelPill');
  const menu = document.getElementById('chatModelMenu');
  if (pill && menu) {
    pill.addEventListener('click', (e) => {
      e.stopPropagation();
      _menuOpen = !_menuOpen;
      menu.classList.toggle('show', _menuOpen);
    });
    // 点外面收起
    document.addEventListener('click', (e) => {
      if (!_menuOpen) return;
      if (e.target.closest('#chatModelPick')) return;
      _menuOpen = false;
      menu.classList.remove('show');
    });
  }
  const closeBtn = document.getElementById('cmAddClose');
  const cancelBtn = document.getElementById('cm_cancel');
  const saveBtn = document.getElementById('cm_save');
  const testBtn = document.getElementById('cm_test');
  const eyeBtn = document.getElementById('cm_eye');
  const mask = document.getElementById('cmAddMask');
  if (closeBtn)  closeBtn.addEventListener('click', closeAddDialog);
  if (cancelBtn) cancelBtn.addEventListener('click', closeAddDialog);
  if (saveBtn)   saveBtn.addEventListener('click', _onSave);
  if (testBtn)   testBtn.addEventListener('click', _onTest);
  if (eyeBtn) {
    eyeBtn.addEventListener('click', () => {
      const inp = document.getElementById('cm_api_key');
      if (!inp) return;
      inp.type = inp.type === 'password' ? 'text' : 'password';
    });
  }
  if (mask) {
    mask.addEventListener('click', (e) => {
      if (e.target === mask) closeAddDialog();
    });
  }
  // BASE URL 失焦即规范化(补 https:// / 补 /v1),并联动模型 ID 提示
  const baseInp = document.getElementById('cm_base_url');
  if (baseInp) {
    baseInp.addEventListener('blur', () => {
      const v = baseInp.value.trim();
      if (!v) return;
      baseInp.value = _normalizeBaseUrl(v);
      _applyModelHint(baseInp.value);
    });
    baseInp.addEventListener('input', () => _applyModelHint(baseInp.value));
  }
  const modelInp = document.getElementById('cm_model');
  if (modelInp) modelInp.addEventListener('input', () => _applyModelHint(baseInp?.value));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (mask?.classList.contains('show')) closeAddDialog();
      if (_menuOpen) { _menuOpen = false; menu?.classList.remove('show'); }
    }
  });
}

// ── 启动 ─────────────────────────────────────────

(function init() {
  bindEvents();
  loadModels();
  // 多 tab 同步:监听 storage 事件
  window.addEventListener('storage', (e) => {
    if (e.key === '__foxclaw_model_changed__') loadModels();
  });
  // 切完模型后,广播给其它 tab(简版:周期性 reload)
  setInterval(() => {
    if (_menuOpen) loadModels();
  }, 5000);
})();
