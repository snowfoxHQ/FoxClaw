import { API } from './state.js';

/* ===== 设置面板 ===== */
const SETTINGS_TITLE={
  model:'模型路由',runtime:'运行参数',ui:'界面偏好',data:'数据管理',auth:'访问密钥',about:'关于',
  'ag-supervisor':'主管 Agent','ag-finance':'财务 Agent','ag-admin':'行政 Agent',
  'ag-office':'办公 Agent','ag-assistant':'助手 Agent',
  'mem-backend':'记忆后端','mem-layer':'双层记忆','mem-recall':'召回参数','mem-manage':'记忆管理',
};
// 界面偏好(纯前端,存内存)
export const uiPref={ autoChat:true, ballAnim:false, mapStyle:'light' };  // ballAnim 默认 false(球体特效默认关,省 GPU;需要时手动勾)
let _sysInfo=null;
async function fetchSysInfo(){ try{ _sysInfo=await(await fetch(`${API}/system/info`)).json(); }catch(e){ _sysInfo=null; } return _sysInfo; }

async function renderSettingDetail(key){
  const body=document.getElementById('sdBody');
  body.innerHTML='<div class="sd-ph">加载中…</div>';
  // ── Foxclaw 主系统 ──
  if(key==='model'){
    const d=await fetchSysInfo(); const m=d?.model||{};
    let h=`<div class="sd-card"><div class="sd-k">当前模型</div>
      <div class="sd-big">${(m.provider||'—')} <span>${m.active?m.model:'(降级 stub)'}</span></div></div>`;
    h+='<div class="sd-sec">可用 Provider · 点击切换<span class="badge edit">可调</span></div>';
    (m.available_providers||[]).forEach(p=>{
      const cur=p.provider===m.provider, dis=!p.configured;
      h+=`<div class="sd-row ${cur?'cur':''} ${dis?'dis':''}" ${dis||cur?'':`onclick="switchProvider('${p.provider}')"`}>
        <span>${PROVIDER_LABEL[p.provider]?.n||p.provider}<small>${p.default_model}</small></span>
        <span class="tag">${cur?'当前':dis?'未配置':'可用'}</span></div>`;
    });
    h+='<div class="sd-note">模型 Key 在项目根目录 <code>.env</code> 配置,改后重启生效。</div>';
    body.innerHTML=h;
  }else if(key==='runtime'){
    const d=await fetchSysInfo();
    body.innerHTML=`
      <div class="sd-sec">运行参数<span class="badge ro">只读</span></div>
      <div class="sd-ro">
      <div class="sd-kv"><span>数据目录</span><b>${d?.data_dir||'—'}</b></div>
      <div class="sd-kv"><span>Harness 影子轮数</span><b>${d?.shadow_rounds??'—'}</b></div>
      </div>
      <div class="sd-note">运行参数在 <code>.env</code> 配置(FOXCLAW_DATA_DIR / FOXCLAW_SHADOW_ROUNDS),改后重启生效。影子轮数越小,Harness 越快进入漂移拦截模式。</div>`;
  }else if(key==='ui'){
    body.innerHTML=`
      <div class="sd-sec">界面偏好<span class="badge edit">可调</span></div>
      <div class="sd-toggle"><span>发任务自动弹中央对话层</span><label class="sw"><input type="checkbox" id="pf-autoChat" ${uiPref.autoChat?'checked':''}><i></i></label></div>
      <div class="sd-toggle"><span>背景全息球动画</span><label class="sw"><input type="checkbox" id="pf-ballAnim" ${uiPref.ballAnim?'checked':''}><i></i></label></div>
      <div class="sd-kv2"><span>地图默认样式</span>
        <select id="pf-mapStyle"><option value="light" ${uiPref.mapStyle==='light'?'selected':''}>亮色(官网风格)</option><option value="dark" ${uiPref.mapStyle==='dark'?'selected':''}>暗色</option></select></div>
      <div class="sd-note">界面偏好实时生效,保存在当前会话。</div>`;
    document.getElementById('pf-autoChat').onchange=e=>uiPref.autoChat=e.target.checked;
    document.getElementById('pf-ballAnim').onchange=e=>{uiPref.ballAnim=e.target.checked; toggleBallAnim(e.target.checked);};
    document.getElementById('pf-mapStyle').onchange=e=>uiPref.mapStyle=e.target.value;
  }else if(key==='data'){
    const s=await(await fetch(`${API}/memory/stats`)).json().catch(()=>({}));
    body.innerHTML=`
      <div class="sd-kv"><span>记忆后端</span><b>${s.backend||'—'}</b></div>
      <div class="sd-kv"><span>记忆总条数</span><b>${s.total_memories??0}</b></div>
      <div class="sd-kv"><span>知识关系数</span><b>${s.relations??0}</b></div>
      <div class="sd-note">运行数据存于本地 <code>foxclaw_data/</code> 目录。清空数据请在本地删除该目录后重启(网页端不直接删除,避免误操作)。</div>`;
  }else if(key==='auth'){
    const cur=localStorage.getItem('foxclawApiKey')||'';
    body.innerHTML=`
      <div class="sd-sec">后端访问密钥<span class="badge ${cur?'ro':'edit'}">${cur?'已配置':'未配置'}</span></div>
      <div class="sd-desc">后端 <code>.env</code> 若设置了 <code>FOXCLAW_API_KEY</code>,则所有 <code>/api</code>、<code>/knowledge</code> 请求与 WebSocket 都需携带此密钥,否则返回 401/403、数据无法加载。填入与 <code>.env</code> 相同的值即可恢复。</div>
      <div class="api-key-row">
        <input type="password" id="pf-apiKey" class="api-key-inp" placeholder="粘贴 FOXCLAW_API_KEY 的值" value="${cur}" autocomplete="off" spellcheck="false">
      </div>
      <div class="api-key-btns">
        <button id="pf-apiKey-save" class="api-key-btn primary">保存并重连</button>
        <button id="pf-apiKey-clear" class="api-key-btn">清除</button>
      </div>
      <div class="sd-note">密钥仅保存在本机浏览器 <code>localStorage</code>,不会上传到服务器。后端未设 <code>FOXCLAW_API_KEY</code> 时此项留空即可(默认开放)。</div>`;
    document.getElementById('pf-apiKey-save').onclick=()=>{
      const v=document.getElementById('pf-apiKey').value.trim();
      if(v) localStorage.setItem('foxclawApiKey', v);
      else localStorage.removeItem('foxclawApiKey');
      location.reload();
    };
    document.getElementById('pf-apiKey-clear').onclick=()=>{
      localStorage.removeItem('foxclawApiKey');
      location.reload();
    };
  }else if(key==='about'){
    const d=await fetchSysInfo();
    body.innerHTML=`
      <div class="sd-card"><div class="sd-big">Foxclaw <span>v${d?.version||'1.0.0'}</span></div>
        <div class="sd-desc">有记忆的个人智能体 · 记忆/认知层与 LLM 解耦</div></div>
      <div class="sd-sec">五个 Agent</div>
      <div class="sd-kv"><span>主管 Supervisor</span><b>分诊调度 · 全局态势</b></div>
      <div class="sd-kv"><span>财务 Finance</span><b>理财 · 风控 · 守门人</b></div>
      <div class="sd-kv"><span>行政 Admin</span><b>行程 · 路线 · 高德地图</b></div>
      <div class="sd-kv"><span>办公 Office</span><b>学习 · 知识图谱</b></div>
      <div class="sd-kv"><span>助手 Assistant</span><b>通用 · Skill 接入</b></div>
      <div class="sd-sec">三大模块</div>
      <div class="sd-desc">Foxclaw(主系统) · Agents(五智能体) · HMR(记忆系统)</div>
      <div class="sd-note">配置说明见项目 <code>docs/配置指南.md</code></div>`;
  }else if(key && key.startsWith('mem-')){
    const s=await(await fetch(`${API}/memory/stats`)).json().catch(()=>({}));
    if(key==='mem-backend'){
      body.innerHTML=`
        <div class="sd-card"><div class="sd-big">${s.backend||'HMR'} <span>v${s.version||'—'}</span></div>
          <div class="sd-desc">分层记忆系统 · 记忆/认知层与 LLM 解耦</div></div>
        <div class="sd-sec">后端信息<span class="badge ro">只读</span></div>
        <div class="sd-kv"><span>记忆后端</span><b>${s.backend||'—'}</b></div>
        <div class="sd-kv"><span>版本</span><b>v${s.version||'—'}</b></div>
        <div class="sd-kv"><span>Embedding 模型</span><b>${s.embedding||'—'}</b></div>
        <div class="sd-kv"><span>知识图谱关系数</span><b>${s.relations??0}</b></div>
        <div class="sd-note">安装 <code>openai</code> 或 <code>sentence-transformers</code> 可启用更精确的语义向量(当前默认 TF-IDF n-gram 降级)。</div>`;
    }else if(key==='mem-layer'){
      const ba=s.by_agent||{}; const entries=Object.entries(ba).sort((a,b)=>b[1]-a[1]);
      let rows=entries.length?entries.map(([a,n])=>`<div class="sd-kv"><span>${AGENT_META[a]?.cn||a}</span><b>${n} 条</b></div>`).join(''):'<div class="sd-desc">暂无分层记忆数据。</div>';
      body.innerHTML=`
        <div class="sd-sec">双层记忆架构<span class="badge ro">只读</span></div>
        <div class="sd-kv"><span>私有层(各 Agent 独立)</span><b>${s.private??0} 条</b></div>
        <div class="sd-kv"><span>共享层(跨 Agent 可召回)</span><b>${s.shared??0} 条</b></div>
        <div class="sd-sec">按 Agent 分布<span class="badge ro">只读</span></div>
        ${rows}
        <div class="sd-note">私有层:每个 Agent 只能召回自己的记忆;共享层:标记 shared 的记忆所有 Agent 都可召回(如主管的全局决策)。</div>`;
    }else if(key==='mem-recall'){
      body.innerHTML=`
        <div class="sd-sec">召回参数<span class="badge ro">只读</span></div>
        <div class="sd-kv"><span>默认召回数量 (top-k)</span><b>5</b></div>
        <div class="sd-kv"><span>相似度阈值</span><b>自适应</b></div>
        <div class="sd-kv"><span>召回范围</span><b>本 Agent 私有 + 共享层</b></div>
        <div class="sd-note">召回采用语义向量相似度排序,默认返回最相关的 top-k 条记忆 + 共享层记忆。参数在记忆适配器中定义,随召回质量自调。</div>`;
    }else if(key==='mem-manage'){
      body.innerHTML=`
        <div class="sd-card"><div class="sd-big">${s.total_memories??0} <span>条记忆</span></div>
          <div class="sd-desc">私有 ${s.private??0} · 共享 ${s.shared??0} · 关系 ${s.relations??0}</div></div>
        <div class="sd-sec">记忆管理<span class="badge ro">只读</span></div>
        <div class="sd-kv"><span>记忆总条数</span><b>${s.total_memories??0}</b></div>
        <div class="sd-kv"><span>知识关系数</span><b>${s.relations??0}</b></div>
        <div class="sd-note">运行数据存于本地 <code>foxclaw_data/</code> 目录。清空记忆请在本地删除该目录后重启(网页端不直接删除,避免误操作)。导出功能可通过 HMR 后端 API 实现。</div>`;
    }
  }else if(key && key.startsWith('ag-')){
    await renderAgentDetail(key.slice(3), body);
  }else{
    body.innerHTML=`<div class="sd-ph">该模块设置开发中</div>`;
  }
}

/* 单个 Agent 设置详情(5 个共用 + 领域特定块)*/
const AGENT_META={
  supervisor:{cn:'主管',en:'Supervisor',ac:'#ffc24b',role:'分诊调度 · 全局态势编排'},
  finance:{cn:'财务',en:'Finance',ac:'#00ffb2',role:'理财 · 预算 · 风控守门'},
  admin:{cn:'行政',en:'Admin',ac:'#ff9d00',role:'行程 · 日程 · 高德地图'},
  office:{cn:'办公',en:'Office',ac:'#d99a3c',role:'学习 · 知识 · 间隔重复'},
  assistant:{cn:'助手',en:'Assistant',ac:'#38bdf8',role:'通用任务 · Skill 接入'},
};
async function renderAgentDetail(aid, body){
  const meta=AGENT_META[aid]||{cn:aid,en:aid,ac:'#00d9ff',role:''};
  let hs=null, sys=null;
  try{ hs=await(await fetch(`${API}/harness/${aid}/status`)).json(); }catch(e){}
  try{ sys=await fetchSysInfo(); }catch(e){}
  const ag=sys?.agents?.[aid]||{};
  const shadow=ag.shadow_mode, rounds=ag.shadow_rounds??'—', run=ag.run_count??0;
  const th=hs?.thresholds||{}, w=hs?.weights||{}, pol=hs?.policy||{};

  let h=`<div class="sd-card" style="border-color:${meta.ac}55">
    <div class="sd-big" style="color:${meta.ac}">${meta.cn} <span>${meta.en}</span></div>
    <div class="sd-desc">${meta.role}</div></div>`;

  // Harness 漂移检测
  h+='<div class="sd-sec">Harness 漂移检测<span class="badge ro">只读</span></div>';
  h+=`<div class="sd-kv"><span>运行模式</span><b style="color:${shadow?'var(--amber)':'var(--green)'}">${shadow?'影子校准中':'已激活拦截'}</b></div>`;
  h+=`<div class="sd-kv"><span>校准进度</span><b>${run} / ${rounds} 轮</b></div>`;
  if(th.warn!=null||th.block!=null){
    h+=`<div class="sd-kv"><span>预警阈值 (WARN)</span><b>${fmt(th.warn)}</b></div>`;
    h+=`<div class="sd-kv"><span>拦截阈值 (BLOCK)</span><b>${fmt(th.block)}</b></div>`;
  }
  if(w.semantic!=null){
    h+=`<div class="sd-kv"><span>三维权重</span><b>语义 ${fmt(w.semantic)} · 工具 ${fmt(w.tool)} · 范围 ${fmt(w.scope)}</b></div>`;
  }

  // 守门人 Policy
  h+='<div class="sd-sec">守门人 Policy<span class="badge ro">只读</span></div>';
  if(pol.version!=null||pol.total_rules!=null){
    h+=`<div class="sd-kv"><span>Policy 版本</span><b>v${pol.version??'—'}</b></div>`;
    h+=`<div class="sd-kv"><span>规则总数</span><b>${pol.total_rules??0}</b></div>`;
    h+=`<div class="sd-kv"><span>手工规则 / 自学习</span><b>${pol.manual_rules??0} / ${pol.learned_rules??0}</b></div>`;
  }else{
    h+='<div class="sd-desc">策略随运行自学习更新,当前无自定义规则覆盖。</div>';
  }

  // 领域特定块
  if(aid==='admin'){
    h+='<div class="sd-sec">高德地图(行政专属)<span class="badge ro">只读</span></div>';
    const a=sys?.amap||{};
    h+=`<div class="sd-kv"><span>Web服务 Key(路线/POI)</span><b style="color:${a.web_key?'var(--green)':'var(--ink-faint)'}">${a.web_key?'已配置':'未配置'}</b></div>`;
    h+=`<div class="sd-kv"><span>JSAPI Key(前端地图)</span><b style="color:${a.js_key?'var(--green)':'var(--ink-faint)'}">${a.js_key?'已配置':'未配置'}</b></div>`;
    h+=`<div class="sd-kv"><span>JSAPI 安全密钥</span><b style="color:${a.js_scode?'var(--green)':'var(--ink-faint)'}">${a.js_scode?'已配置':'未配置'}</b></div>`;
    h+=`<div class="sd-toggle"><span>地图默认样式(暗色)</span><label class="sw"><input type="checkbox" id="pf-mapDark2" ${uiPref.mapStyle==='dark'?'checked':''}><i></i></label></div>`;
    h+='<div class="sd-note">高德三个 Key 在 <code>.env</code> 配置(AMAP_MAPS_API_KEY / AMAP_JS_KEY / AMAP_JS_SCODE)。</div>';
  }else if(aid==='finance'){
    h+='<div class="sd-sec">财务领域参数<span class="badge ro">只读</span></div>';
    h+='<div class="sd-desc">预算阈值、MAD 异常灵敏度、风控规则在财务内核 policy 中定义,随交易数据自学习。守门人对大额/越权操作执行确认或冻结。</div>';
  }else if(aid==='office'){
    h+='<div class="sd-sec">学习领域参数<span class="badge ro">只读</span></div>';
    h+='<div class="sd-desc">采用 SM-2 间隔重复算法管理知识复习节奏;知识图谱关系自动抽取,守门人防止知识污染。</div>';
  }else if(aid==='supervisor'){
    h+='<div class="sd-sec">路由编排<span class="badge ro">只读</span></div>';
    h+='<div class="sd-desc">元问题(介绍/系统级)由主管自答;业务任务按关键词分诊到财务/行政/办公/助手。可在 agents.py 的 ROUTING_KEYWORDS / META_KEYWORDS 调整。</div>';
  }else if(aid==='assistant'){
    h+='<div class="sd-sec">Skill 接入<span class="badge ro">只读</span></div>';
    h+='<div class="sd-desc">通用任务兜底处理,可接入外部 Skill 工具扩展能力。主管无法明确归类的任务默认派给助手。</div>';
  }

  h+='<div class="sd-note">Agent 行为参数大多在后端 policy 与代码中定义,此处为只读状态展示。漂移阈值由影子校准期自动学习得出。</div>';
  body.innerHTML=h;

  // 行政地图暗色开关联动
  const md=document.getElementById('pf-mapDark2');
  if(md) md.onchange=e=>{ uiPref.mapStyle=e.target.checked?'dark':'light'; };
}
function fmt(v){ return (v==null||isNaN(v))?'—':(typeof v==='number'?v.toFixed(3):v); }
export async function switchProvider(prov){
  try{ await fetch(`${API}/model/switch`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:prov})});
    _sysInfo=null; renderSettingDetail('model'); loadModel(); }catch(e){}
}
function toggleBallAnim(on){
  const c=document.getElementById('coreCanvas'); if(c)c.style.display=on?'block':'none';
  // 仅 display:none 不够(loop 还在 60fps 跑,吃 GPU)→ 同步设置 localStorage 标志,
  // 引导用户硬刷一次(自动 reload)。visuals.js 顶部 _lowpower 检查,刷新后即生效。
  try{ localStorage.setItem('foxclaw_lowpower', on ? '0' : '1'); }catch{}
  // 仅当「关」才 reload(开不影响,因为持久态 = 0 时不阻止)
  if (!on) {
    // 提示一下,避免用户疑惑
    console.info('[foxclaw] 关掉球动画后需刷新一次才停止 GPU 占用');
  }
}

/* 二级子项定义 */
const SETTINGS_SUBS={
  foxclaw:[['model','模型路由'],['runtime','运行参数'],['ui','界面偏好'],['data','数据管理'],['auth','访问密钥'],['about','关于']],
  agents:[['ag-supervisor','主管 Agent'],['ag-finance','财务 Agent'],['ag-admin','行政 Agent'],['ag-office','办公 Agent'],['ag-assistant','助手 Agent']],
  hmr:[['mem-backend','记忆后端'],['mem-layer','双层记忆'],['mem-recall','召回参数'],['mem-manage','记忆管理']],
};
const _setMenu=document.getElementById('setMenu');
const _smL2=document.getElementById('smL2');
const _sdPop=document.getElementById('sdPop');
let _setHideTimer=null;

export function openSetMenu(){ clearTimeout(_setHideTimer); _setMenu.classList.add('show'); }
/* 直接打开「访问密钥」设置详情(供 main.js 的鉴权提示条调用) */
export function openAuthSetting(){ _setMenu.classList.add('show'); openDetailPop('auth'); }
function closeSetMenu(){
  clearTimeout(_setHideTimer);
  _setHideTimer=setTimeout(()=>{ _setMenu.classList.remove('show'); _smL2.innerHTML=''; document.querySelectorAll('.sm-l1 .sm-btn').forEach(b=>b.classList.remove('active')); }, 400);
}
// 一级悬停 → 展开二级
function showL2(group, l1btn){
  document.querySelectorAll('.sm-l1 .sm-btn').forEach(b=>b.classList.remove('active'));
  l1btn.classList.add('active');
  _smL2.innerHTML=(SETTINGS_SUBS[group]||[]).map(([k,name])=>
    `<div class="sm-btn" data-sub="${k}"><b>${name}</b><span class="chev">›</span></div>`).join('');
  _smL2.querySelectorAll('.sm-btn').forEach(b=>{
    b.addEventListener('click',()=>openDetailPop(b.dataset.sub));
  });
}
function openDetailPop(key){
  document.getElementById('sdTitle').textContent=SETTINGS_TITLE[key]||'详情';
  _sdPop.classList.add('show');
  renderSettingDetail(key);
}
function closeDetailPop(){ _sdPop.classList.remove('show'); }

const _gear=document.getElementById('settingsBtn');
_gear.addEventListener('mouseenter', openSetMenu);
_gear.addEventListener('mouseleave', closeSetMenu);
_gear.addEventListener('click', openSetMenu);
_setMenu.addEventListener('mouseenter', ()=>clearTimeout(_setHideTimer));
_setMenu.addEventListener('mouseleave', closeSetMenu);
document.querySelectorAll('.sm-l1 .sm-btn').forEach(b=>{
  b.addEventListener('mouseenter', ()=>showL2(b.dataset.group, b));
});
document.getElementById('settingsBtn2')?.addEventListener('click', ()=>{ openSetMenu(); clearTimeout(_setHideTimer); });
document.getElementById('sdClose').addEventListener('click', closeDetailPop);

document.querySelectorAll('.nav-mini, .feed-item').forEach(it=>{
  it.addEventListener('click',()=>{ it.classList.add('pressed'); setTimeout(()=>it.classList.remove('pressed'),180); });
});


/* ===== 模型选择器 ===== */
const PROVIDER_LABEL=
{anthropic:{n:'Claude',c:'#ff9d00'},openai:{n:'GPT',c:'#00ffb2'},
  google:{n:'Gemini',c:'#38bdf8'},qwen:{n:'通义千问',c:'#ffc24b'},
  local:{n:'本地模型',c:'#d99a3c'},stub:{n:'Stub 降级',c:'#7e93b8'}};
async function loadModel(){
  try{
    const d=await(await fetch(`${API}/model`)).json();
    const lbl=PROVIDER_LABEL[d.provider]||{n:d.provider,c:'#7e93b8'};
    document.getElementById('modelName').textContent=lbl.n;
    renderModelMenu(d);
  }catch(e){}
}
function renderModelMenu(d){
  const menu=document.getElementById('modelMenu'); if(!menu)return;
  let h='<div class="mtitle">模型路由 · 点击切换</div>';
  (d.available_providers||[]).forEach(p=>{
    const lbl=PROVIDER_LABEL[p.provider]||{n:p.provider,c:'#7e93b8'};
    const cur=p.provider===d.provider;
    const dis=!p.configured;
    h+=`<div class="model-opt ${cur?'active':''} ${dis?'disabled':''}" data-prov="${p.provider}">
      <span class="mdot" style="background:${lbl.c};box-shadow:0 0 6px ${lbl.c}"></span>
      <span class="mname"><b>${lbl.n}</b><span>${p.default_model}</span></span>
      ${cur?'<span class="mtag">当前</span>':dis?'<span class="mtag" style="background:rgba(255,157,0,.12);color:var(--amber)">未配置</span>':'<span class="mtag">可用</span>'}</div>`;
  });
  menu.innerHTML=h;
  menu.querySelectorAll('.model-opt:not(.disabled):not(.active)').forEach(opt=>{
    opt.addEventListener('click',async()=>{
      const prov=opt.dataset.prov;
      try{await fetch(`${API}/model/switch`,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider:prov})});
        menu.classList.remove('show'); loadModel();
      }catch(e){}
    });
  });
}
document.getElementById('modelPill').addEventListener('click',e=>{
  e.stopPropagation();
  document.getElementById('modelMenu').classList.toggle('show');
});
document.addEventListener('click',()=>document.getElementById('modelMenu')?.classList.remove('show'));
loadModel();
