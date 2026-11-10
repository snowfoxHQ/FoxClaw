
const API_BASE = (typeof window !== 'undefined' && window.API_BASE) || '';
const _api = (p) => `${API_BASE}/api/llm${p}`;

let _models = { providers: [], custom: [], current: null };
let _menuOpen = false;

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

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

function renderMenu() {
  const menu = document.getElementById('chatModelMenu');
  if (!menu) return;
  const cur = _currentLabel();
  let h = '';

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
  const r = await fetch(_api(`/custom/${encodeURIComponent(name)}`));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function updateCustom(name, payload) {
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

let _cmMode = 'add';          // 'add' | 'edit'
let _cmEditingName = null;    // edit 模式下的原名(PUT URL 用);改名后仍指向磁盘上那条记录

function openAddDialog(mode = 'add', preset = null) {
  _cmMode = mode;
  _cmEditingName = (mode === 'edit' && preset)
    ? (preset._editingName || preset.name)
    : null;

  const mask = document.getElementById('cmAddMask');
  if (!mask) return;

  const title = document.getElementById('cmAddTitle');
  if (title) {
    title.textContent = mode === 'edit' && preset?.name
      ? `编辑自定义模型 · ${preset.name}`
      : '添加自定义模型';
  }

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

function _modelWarning(baseUrl, modelId) {
  const s = String(baseUrl || ''), m = String(modelId || '').trim();
  if (!m) return '';
  if (/api\.deepseek\.com/i.test(s) && !/^deepseek-(chat|reasoner)$/.test(m))
    return `⚠ DeepSeek 官方没有「${m}」,应填 deepseek-chat 或 deepseek-reasoner`;
  return '';
}

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
      _cmEditingName = r.name;
    }
    await loadModels();
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
      _setMsg(`测试已保存配置(${_cmEditingName})…`, 'info');
      const r = await testModel('custom', null, _cmEditingName);
      if (r.ok) {
        _setMsg(`✓ 连通 (${r.latency_ms} ms, model=${r.model}, reply="${(r.reply || '').slice(0, 30)}")`, 'ok');
      } else {
        _setMsg(`✗ 连通失败: ${(r.error || '').slice(0, 200)}\n(测的是已保存配置;要测表单里的新值请先点「保存」)`, 'err');
      }
      return;
    }
    if (!payload.name || !payload.api_key) {
      _setMsg('add 模式请先填「模型名称」+「API KEY」再测试', 'err');
      return;
    }
    _setMsg('保存并测试中…', 'info');
    await addCustom(payload);
    const r = await testModel('custom', null, payload.name);
    if (r.ok) {
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

function bindEvents() {
  const pill = document.getElementById('chatModelPill');
  const menu = document.getElementById('chatModelMenu');
  if (pill && menu) {
    pill.addEventListener('click', (e) => {
      e.stopPropagation();
      _menuOpen = !_menuOpen;
      menu.classList.toggle('show', _menuOpen);
    });
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

(function init() {
  bindEvents();
  loadModels();
  window.addEventListener('storage', (e) => {
    if (e.key === '__foxclaw_model_changed__') loadModels();
  });
  setInterval(() => {
    if (_menuOpen) loadModels();
  }, 5000);
})();
