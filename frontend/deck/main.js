import './auth.js?v=5.37';
import { escapeHtml } from './util.js?v=2.9';
import { renderMarkdown, renderReply } from './markdown.js?v=2.9';
import './visuals.js?v=5.36';   // 球体特效默认关 + foxclawVisual.on/off/toggle API
import { _origin, API, WS, _lastKernelData } from './state.js?v=5.31';
import { fetchAMapConfig, kpSet, renderFinance, renderLearning, renderItinerary, itinSideHTML, renderSituation, drawAMapInto, initMapSearch } from './domain.js?v=5.31';
import { openSetMenu, switchProvider, uiPref, openAuthSetting } from './settings.js?v=5.36';   // uiPref.ballAnim 默认 false
import './chat_model_picker.js?v=1.4';   // chat 输入框旁的下拉式模型选择器;v1.4 保存即切换 + BASE URL 自动补 /v1 + 模型 ID 软提示

let activeAgent='supervisor';
const AGENT_CN={supervisor:'主管',finance:'财务',office:'办公学习',admin:'行程'};

function setUplink(on){
  const el=document.getElementById('upStat');
  if(el){el.textContent=on?'100%':'离线'; el.style.color=on?'var(--green)':'var(--amber)';}
}

function showAuthHint(){
  if(document.getElementById('authHint')) return;
  const bar=document.createElement('div');
  bar.id='authHint';
  bar.innerHTML='<span class="ah-ico">⚠</span><span class="ah-txt">后端已开启鉴权,本机浏览器尚未配置访问密钥,数据无法加载</span>'
    +'<button class="ah-btn" id="ah-go">配置密钥</button><button class="ah-x" id="ah-x">✕</button>';
  document.body.appendChild(bar);
  document.getElementById('ah-go').onclick=()=>{ bar.remove(); openAuthSetting(); };
  document.getElementById('ah-x').onclick=()=>bar.remove();
}
window.__foxclawAuthHint=showAuthHint;

async function loadStatus(){
  try{
    const r=await fetch(`${API}/status`); if(!r.ok)throw 0;
    const data=await r.json(); setUplink(true);
    document.querySelectorAll('.acard').forEach(card=>{
      const id=card.dataset.agent; const st=data[id];
      const dot=card.querySelector('.st');
      if(st&&dot){dot.innerHTML='<i></i>在线'; dot.style.color='var(--green)';}
    });
  }catch(e){ setUplink(false); }
}

async function loadMemStats(){
  const set=(id,v)=>{const el=document.getElementById(id); if(el)el.textContent=v;};
  try{
    const d=await fetch(`${API}/memory/stats`).then(r=>r.json());
    set('msTotal',(d.total_memories||0).toLocaleString());
    set('msPrivate',(d.private||0).toLocaleString());
    set('msShared',(d.shared||0).toLocaleString());
    set('msRel',(d.relations||0).toLocaleString());
    set('memCount',(d.total_memories||0)+' 条');
    set('memVec',(d.total_memories||0).toLocaleString());   // 兼容旧元素(若仍存在)
    set('memRel',(d.relations||0).toLocaleString());
  }catch(e){}
}

async function loadKnowList(){
  const list=document.getElementById('knowList'); if(!list)return;
  try{
    const r=await fetch(`${API}/learning/analysis`).then(x=>x.json());
    const items=r.items||[];
    const cnt=document.getElementById('knowCount'); if(cnt)cnt.textContent=(r.total_items??items.length)+' 条';
    if(!items.length){ list.innerHTML='<div class="kp-empty">知识库为空 · 录入学习数据后显示</div>'; return; }
    list.innerHTML=items.slice(0,20).map((k,i)=>`<div class="info-item" data-idx="${i}">
        <div class="it-h"><b>${escapeHtml(k.title||k.name||'知识条目')}</b>
          ${(k.topic||k.tag)?`<span class="it-tag">${escapeHtml(k.topic||k.tag)}</span>`:''}</div>
        ${k.content?`<div class="it-sum">${escapeHtml(k.content)}</div>`:''}
        ${k.source?`<div class="it-meta">来源: ${escapeHtml(k.source)}</div>`:''}</div>`).join('');
    list.querySelectorAll('.info-item').forEach(el=>el.onclick=()=>{
      const it=items[+el.dataset.idx];
      if(it) showKnowledgeArticle(it); else showKnowledgeFull();
    });
  }catch(e){ list.innerHTML='<div class="kp-empty">后端未连接</div>'; }
}

document.querySelectorAll('.acard').forEach(card=>{
  card.addEventListener('click',()=>{
    document.querySelectorAll('.acard').forEach(c=>c.classList.remove('on'));
    card.classList.add('on');
    activeAgent=card.dataset.agent;
    const cmd=document.getElementById('cmd');
    if(cmd)cmd.placeholder=`告诉我你想做什么…  ${AGENT_CN[activeAgent]} Agent 将处理`;
    loadKernel(activeAgent);
    showInCore(activeAgent);     // 当前 Agent 内容投到中央焦点空间
    refreshAgentStatus();
  });
});
document.querySelectorAll('.abtn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    const ag=btn.dataset.agent, wasOn=btn.classList.contains('on');
    document.querySelectorAll('.abtn').forEach(b=>b.classList.remove('on'));
    if(wasOn){ clearCore(); return; }
    btn.classList.add('on');
    loadKernel(ag); showInCore(ag);
  });
});
function refreshAgentStatus(){
  document.querySelectorAll('.acard').forEach(c=>{
    const st=c.querySelector('.st-tx'); if(!st)return;
    if(c.classList.contains('busy')) st.textContent='工作中';
    else if(c.classList.contains('on')) st.textContent='在线';
    else st.textContent='离线';
  });
}
refreshAgentStatus();

const Center = {
  mode:'home',
  panel(){   // 打开面板层(知识/模块/领域视图);返回 #coreStage 供调用方写 innerHTML
    const wrap=document.querySelector('.corewrap'), stage=document.getElementById('coreStage');
    document.getElementById('coreChat')?.classList.remove('show');   // 关对话浮层
    wrap?.classList.add('busy'); stage?.classList.add('show');
    this.mode='panel'; return stage;
  },
  chat(){    // 打开对话浮层(对话/会话/单轮回复);返回 #coreChat
    const wrap=document.querySelector('.corewrap'), stage=document.getElementById('coreStage');
    wrap?.classList.remove('busy');
    if(stage){ stage.classList.remove('show'); stage.innerHTML=''; }   // 拆面板层
    const chat=document.getElementById('coreChat'); chat?.classList.add('show');
    this.mode='chat'; return chat;
  },
  home(){    // 回到 3D 核心:两层都收起
    const wrap=document.querySelector('.corewrap'), stage=document.getElementById('coreStage');
    document.getElementById('coreChat')?.classList.remove('show');
    wrap?.classList.remove('busy');
    if(stage){ stage.classList.remove('show'); stage.innerHTML=''; }
    this.mode='home';
  }
};
function clearCore(){ Center.home(); _syncNav('overview'); }   // 兼容旧调用名(9 处)→ 统一走中枢
async function showInCore(agent){
  const wrap=document.querySelector('.corewrap'), stage=document.getElementById('coreStage');
  document.getElementById('coreChat')?.classList.remove('show');
  const m=KP_META[agent]||KP_META.office;
  if(agent==='supervisor'){ clearCore(); return; }
  Center.panel();
  stage.innerHTML=`<div class="cs-head"><div class="t"><span class="dot"></span>${m.title} <em>${m.tag}</em></div>
    <span class="x" id="csClose" title="返回核心">✕</span></div>
    <div class="cs-body" id="csBody"><div class="kp-empty" style="padding:30px 0">加载中…</div></div>`;
  document.getElementById('csClose').onclick=clearCore;
  const body=document.getElementById('csBody');
  try{
    const d=_lastKernelData[agent]||{};
    if(agent==='admin'){
      body.innerHTML=`<div style="position:relative;height:100%;min-height:300px">
        <div id="coreMap" style="width:100%;height:100%;border-radius:10px;overflow:hidden"></div>
        <div class="map-search"><input id="mapSearchInput" placeholder="🔍 搜索地点…"><div id="mapSearchResults" class="map-search-results"></div></div>
      </div>`;
      setTimeout(()=>{drawAMapInto('coreMap',d.map_data||[]); initMapSearch();},60);
    }else if(agent==='office'){
      const items=(d.items||[]);
      const ingest='<div class="ingest-row" style="max-width:520px;margin:0 auto 14px">'
        +'<button class="ing-btn" onclick="document.getElementById(\'cmdAttach\')?.click()">📄 导入文件</button>'
        +'<button class="ing-btn" onclick="document.getElementById(\'cmdFolder\')?.click()">📁 导入文件夹</button>'
        +'<button class="ing-btn" onclick="openEntry(\'office\')">＋ 写入知识点</button></div>';
      if(!items.length){
        body.innerHTML=ingest+'<div class="kp-empty" style="padding:24px 0">还没有录入知识 · 用上面的按钮导入文件/文件夹到知识星球(HKC),或手写一条知识点。</div>';
      }else{
        body.innerHTML=ingest+'<div id="_kl"></div>';
        _renderKnowList(document.getElementById('_kl'), items, ()=>showInCore('office'));
      }
    }else if(agent==='finance'){
      body.innerHTML=renderFinance(d);
    }
  }catch(e){ body.innerHTML='<div class="kp-empty">后端未连接</div>'; }
}

const KP_META={
  supervisor:{title:'全局态势',tag:'Situation'},
  finance:{title:'财务驾驶舱',tag:'Finance'},
  admin:{title:'行程规划',tag:'Itinerary'},   // 视图,不是 Agent
  office:{title:'知识与学习',tag:'Knowledge'},
};

async function loadKernel(agent){
  const m=KP_META[agent]||KP_META.office;
  try{
    if(agent==='finance'){
      const d=await fetch(`${API}/finance/analysis?period=2026-06&current_balance=11000`).then(r=>r.json()).catch(()=>({}));
      _lastKernelData.finance=d; kpSet(m.title,m.tag,renderFinance(d||{}));
    }else if(agent==='office'){
      const d=await fetch(`${API}/learning/analysis`).then(r=>r.json()).catch(()=>({}));
      _lastKernelData.office=d; kpSet(m.title,m.tag,renderLearning(d||{}));
    }else if(agent==='admin'){
      const d=await fetch(`${API}/itinerary/plan`).then(r=>r.json()).catch(()=>({}));
      _lastKernelData.admin=d; kpSet(m.title,m.tag,renderItinerary(d||{}));
      if(autoMap)openOverlay('admin');   // 地图自动全屏
    }else if(agent==='supervisor'){
      const d=await fetch(`${API}/orchestration/situation`).then(r=>r.json()).catch(()=>({}));
      _lastKernelData.supervisor=d; kpSet(m.title,m.tag,renderSituation(d||{}));
    }else{
      kpSet(m.title,m.tag,document.getElementById('taskList')?.outerHTML||'<div class="kp-empty">暂无任务</div>');
    }
  }catch(e){ kpSet(m.title,m.tag,'<div class="kp-empty">后端未连接 · 该面板需真实数据</div>'); }
}

function importDomainFile(agent){
  const map={finance:{d:'finance',cn:'交易'}, office:{d:'learning',cn:'知识点'}, admin:{d:'itinerary',cn:'行程点'}};
  const m=map[agent]||map.finance;
  let inp=document.getElementById('_domainImp');
  if(!inp){ inp=document.createElement('input'); inp.type='file'; inp.id='_domainImp'; inp.style.display='none'; inp.accept='.csv,.xlsx,.json'; document.body.appendChild(inp); }
  inp.onchange=async()=>{
    const f=inp.files&&inp.files[0]; inp.value=''; if(!f) return;
    showReplyPop(`📁 导入${m.cn}:${f.name}`, agent, true);
    fillReplyPop('', '⬆ 上传解析中…', 'ok');
    try{
      const fd=new FormData(); fd.append('file', f);
      const r=await fetch(`${API}/${m.d}/import/file`,{method:'POST',body:fd});
      const j=await r.json().catch(()=>({}));
      if(!r.ok){ fillReplyPop('', `导入失败(${r.status}):${String(j.detail||'').slice(0,120)}`, 'err'); return; }
      fillReplyPop(`✓ 已导入 ${j.imported||0} 条${m.cn}`, j.note||'刷新面板查看', 'ok');
      if(typeof loadKernel==='function'){ try{ loadKernel(agent); }catch(e){} }
    }catch(e){ fillReplyPop('', '请求失败:后端未连接', 'err'); }
  };
  inp.click();
}

const agentTasks={};
function setAgentBusy(agent, task){
  agentTasks[agent]=task;
  const card=document.querySelector(`.acard[data-agent="${agent}"]`);
  if(!card)return;
  card.classList.add('busy');
  const lbl=card.querySelector('.task-label');
  if(lbl) lbl.textContent='▸ '+task;
  if(typeof refreshAgentStatus==='function') refreshAgentStatus();
}
function clearAgentBusy(agent){
  delete agentTasks[agent];
  const card=document.querySelector(`.acard[data-agent="${agent}"]`);
  if(card){
    card.classList.remove('busy');
    const l=card.querySelector('.task-label'); if(l)l.textContent='';
    if(typeof refreshAgentStatus==='function') refreshAgentStatus();
  }
}

async function transmit(){
  const inp=document.getElementById('cmd'); let task=(inp.value||'').trim();
  const hasImgs=_pendingImages.length>0, hasFiles=_pendingFiles.length>0;
  if(!task && !hasImgs && !hasFiles)return;
  if(task[0]==='/'){ inp.value=''; hideSlashMenu(); runSlash(task); return; }
  inp.value='';
  if((hasImgs||hasFiles) && !task) task=hasImgs?'请描述并分析这张图片':'请阅读并总结这个文件';
  prependTask(task,activeAgent,'pending');
  setAgentBusy(activeAgent, task);
  if(window._emitStream){ for(let i=0;i<3;i++) setTimeout(()=>window._emitStream(activeAgent), i*180); }
  const qThumbs=hasImgs?_pendingImages.map(x=>x.url):null;
  const qFiles=hasFiles?_pendingFiles.map(x=>({name:x.name,size:x.size,url:x.url})):null;
  showReplyPop(task, activeAgent, false, qThumbs, qFiles);
  if(hasImgs || hasFiles){
    const imgs=hasImgs?_pendingImages.map(x=>({media_type:x.media_type, data:x.data})):null;
    const atts=hasFiles?_pendingFiles.map(x=>({name:x.name,size:x.size,mime:x.mime,url:x.url,text:x.text})):null;
    clearPendingImages(); clearPendingFiles();
    _refTriggers={};   // 流式/多模态无技能路由,清掉 @引用残留(仅任务模式经 applyRefs 生效)
    return transmitStream(task, activeAgent, imgs, atts);
  }
  if(chatModeStream){ _refTriggers={}; return transmitStream(task, activeAgent); }
  const outTask=applyRefs(task);
  if(activeAgent!=='supervisor'){ _refTriggers={}; return transmitTaskStream(outTask, activeAgent); }
  try{
    const r=await fetch(`${API}/task`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task:outTask,agent_id:activeAgent})});
    const data=await r.json();
    if(data.error==='harness_blocked'){
      markTask(task,'blocked');
      fillReplyPop(`⚠ 任务被 Harness 拦截`, `偏离原因:${data.reason||'语义漂移超过阈值'}（Δ=${(data.delta||0).toFixed?.(3)||data.delta||'—'}）`, 'blocked');
    }else{
      markTask(task,'ok');
      const res=data.result||{};
      if(res.dispatched_to==='multi' && Array.isArray(res.multi_steps)){
        let html=`<div style="margin-bottom:10px;color:var(--cyan);font-weight:600">${escapeHtml(res.summary||'多步任务')}</div>`;
        res.multi_steps.forEach(s=>{
          const agName=escapeHtml(AGENT_CN[s.agent]||s.agent);
          const blockedTag=s.blocked?' <span style="color:var(--amber)">[被拦截]</span>':'';
          html+=`<div style="margin:8px 0;padding:8px 10px;border-left:2px solid var(--cyan);background:rgba(0,217,255,.05)">
            <div style="font-size:11px;color:var(--ink-dim);margin-bottom:3px">第${escapeHtml(s.step)}步 · ${agName} Agent${blockedTag}</div>
            <div style="font-size:12px;color:var(--cyan)">「${escapeHtml(s.subtask)}」</div>
            <div style="font-size:12px;margin-top:4px">${escapeHtml(s.output||'(无输出)')}</div>
          </div>`;
        });
        fillReplyPop(html, '⤷ 主管已拆解为多步,跨 Agent 顺序执行', 'ok', true);
        loadMemStats();
        return;
      }
      const reply=res.sub_result||res.summary||res.reply||(typeof res==='string'?res:JSON.stringify(res));
      let via='';
      if(res.dispatched_to&&res.dispatched_to!=='supervisor'&&res.dispatched_to!==activeAgent){
        via=`⤷ 主管已分派给 ${AGENT_CN[res.dispatched_to]||res.dispatched_to} Agent 处理`;
        setAgentBusy(res.dispatched_to, task);
      }else if(res.self_answered){
        via='⤷ 主管直接答复';
      }
      fillReplyPop('<div class="cc-md">'+renderMarkdown(reply)+'</div>', via, 'ok', true);
    }
    loadMemStats();
  }catch(e){
    markTask(task,'blocked');
    fillReplyPop('', '请求失败:后端未连接,或模型未配置 Key', 'err');
  }
}
function showReplyPop(task, agent, force, qImages, qFiles){
  if(!force && typeof uiPref!=='undefined' && !uiPref.autoChat) return;
  document.getElementById('ccAgent').textContent=`${AGENT_CN[agent]||agent} Agent`;
  const _b=document.querySelector('#coreChat .cc-body');
  if(_b && !document.getElementById('ccQ'))
    _b.innerHTML='<div class="cc-q" id="ccQ"></div><div id="ccReply"></div><div class="cc-status" id="ccStatus"></div>';
  let qh='你:'+escapeHtml(task);
  if(qImages && qImages.length){   // 用户随问题发的图片 → 提问区渲染缩略图(本地 dataURL,可信)
    qh+='<div class="cc-qimgs">'+qImages.map(u=>`<img class="cc-qimg" src="${u}" alt="">`).join('')+'</div>';
  }
  if(qFiles && qFiles.length){     // 用户随问题发的附件 → 提问区渲染文件卡片(可下载)
    qh+=_attachmentsHtml(qFiles);
  }
  document.getElementById('ccQ').innerHTML=qh;
  document.getElementById('ccReply').innerHTML='<div class="cc-loading">◌ 思考中…(真实模型回复需几秒)</div>';
  document.getElementById('ccStatus').textContent='';
  Center.chat();
}
function fillReplyPop(reply, status, kind, isHtml){
  const rp=document.getElementById('ccReply'), st=document.getElementById('ccStatus');
  if(isHtml){ rp.innerHTML=reply||''; }
  else if(reply){ rp.textContent=reply; } else { rp.innerHTML=''; }
  st.textContent=status||'';
  if(kind==='blocked'){ st.style.color='var(--amber)'; rp.style.color='var(--amber)'; }
  else if(kind==='err'){ st.style.color='var(--pink)'; }
  else { st.style.color='var(--ink-dim)'; rp.style.color='var(--ink)'; }
}
document.getElementById('ccClose')?.addEventListener('click',()=>document.getElementById('coreChat')?.classList.remove('show'));
document.querySelector('.cmdbar .send')?.addEventListener('click',transmit);
document.getElementById('cmd')?.addEventListener('keydown',e=>{if(e.key==='Enter')transmit();});

const _INGEST_MAX_BYTES=20*1024*1024;
let _ingestBusy=false;   // 串行守卫:一次只摄入一个文件,避免并发轮询串写同一进度弹层
(function(){
  const btn=document.getElementById('cmdAttach'), inp=document.getElementById('cmdFile');
  if(!btn||!inp) return;
  btn.addEventListener('click',()=>inp.click());
  inp.addEventListener('change',async ()=>{
    const f=inp.files&&inp.files[0]; inp.value=''; if(!f) return;
    if(_ingestBusy){
      showReplyPop(`📎 ${f.name}`, activeAgent, true);
      fillReplyPop('', '请等当前文件摄入完成再上传下一个', 'err'); return;
    }
    _ingestBusy=true;
    try{ await ingestChatFile(f); } finally{ _ingestBusy=false; }
  });
})();
function _refreshPlanet(){
  try{ const fr=document.getElementById('planetFrame'); const w=fr&&fr.contentWindow;
    if(w && fr.src && fr.src!=='about:blank' && typeof w.loadData==='function') w.loadData(); }catch(e){}
}
async function _ingestFileOnce(file, onStatus, collection){
  if(file.size>_INGEST_MAX_BYTES) return {ok:false, skipped:true, error:`过大(${(file.size/1048576).toFixed(1)}MB>20MB)`};
  let job;
  try{
    const fd=new FormData(); fd.append('file', file);
    fd.append('source_title', (file.name||'').replace(/\.[^.]+$/,''));
    if(collection) fd.append('collection', collection);
    const r=await fetch(`${_origin}/knowledge/ingest/file/async`,{method:'POST',body:fd});
    if(!r.ok) return {ok:false, error: r.status===415?'不支持的格式': r.status===503?'知识核心未启用':`上传失败(${r.status})`};
    job=await r.json();
  }catch(e){ return {ok:false, error:'后端未连接'}; }
  const jobId=job.job_id;
  for(let i=0;i<600;i++){                       // 每文件最多 ~10 分钟
    await new Promise(res=>setTimeout(res,1200));
    let j;
    try{ j=await fetch(`${_origin}/knowledge/ingest/jobs/${encodeURIComponent(jobId)}`).then(x=>x.json()); }
    catch(e){ continue; }
    if(j.status==='running'||j.status==='queued'){
      if(onStatus){ const pct=j.total>0?Math.round(j.processed/j.total*100):0; onStatus(`抽取中 ${j.processed}/${j.total||'?'} 段${j.total>0?' ('+pct+'%)':''}`); }
      continue;
    }
    if(j.status==='done') return {ok:true, ku_count:j.ku_count||0};
    if(j.status==='error') return {ok:false, error:(j.error||'未知错误').slice(0,120)};
  }
  return {ok:false, error:'摄入超时'};
}
async function ingestChatFile(file){
  showReplyPop(`📎 摄入文件:${file.name}`, activeAgent, true);
  fillReplyPop('', '⬆ 上传中…', 'ok');
  const res=await _ingestFileOnce(file, msg=>fillReplyPop('', '⚙ '+msg, 'ok'));
  if(res.ok){
    fillReplyPop(`✓ 已摄入 ${file.name}`,
      `新增 ${res.ku_count} 条知识(KU)已进入知识星球。${res.ku_count===0?'（未配置 LLM 时抽取降级,可能为 0）':''}`, 'ok');
    _refreshPlanet();
  }else{
    fillReplyPop('', (res.skipped?'跳过:':'摄入失败:')+res.error, 'err');
  }
}

const _INGEST_EXTS=['.pdf','.md','.markdown','.txt','.epub','.mobi','.azw3','.azw','.docx','.xlsx'];
const _FOLDER_MAX_FILES=100;   // 单文件夹一次最多摄入的文件数(防选到超大目录顺序摄入几小时+烧 LLM)
function _supportedFile(name){ const n=(name||'').toLowerCase(); return _INGEST_EXTS.some(e=>n.endsWith(e)); }
function _folderHtml(folder, cur, total, current, log, totalKu){
  let h=`<div style="color:var(--cyan);font-weight:600;margin-bottom:6px">📁 ${escapeHtml(folder)} · ${cur}/${total}${totalKu?` · 累计 ${totalKu} KU`:''}</div>`;
  if(current) h+=`<div style="font-size:12px;color:var(--ink-dim);margin-bottom:6px">当前:${escapeHtml(current)}</div>`;
  log.slice(-8).forEach(e=>{ h+=`<div style="font-size:11.5px;margin:2px 0">${e.ok?'✓':'✗'} ${escapeHtml(e.name)} ${e.ok?`<span style="color:var(--ink-faint)">+${e.ku} KU</span>`:`<span style="color:var(--amber)">${escapeHtml(e.err||'')}</span>`}</div>`; });
  return h;
}
async function ingestFolder(fileList){
  const all=[...fileList];
  let files=all.filter(f=>_supportedFile(f.name));
  const folderName=((all[0]&&all[0].webkitRelativePath)||'').split('/')[0]||'文件夹';
  showReplyPop(`📁 摄入文件夹:${folderName}`, activeAgent, true);
  if(!files.length){
    fillReplyPop('', `「${folderName}」里没有可摄入的文件(支持 pdf/txt/md/docx/xlsx/epub 等;共扫描 ${all.length} 个)`, 'err'); return;
  }
  let capNote='';
  if(files.length>_FOLDER_MAX_FILES){          // 超上限 → 只摄前 N 个,提示分批(防失控)
    capNote=` · 超上限,仅摄前 ${_FOLDER_MAX_FILES}(共 ${files.length},请分批)`;
    files=files.slice(0, _FOLDER_MAX_FILES);
  }
  const N=files.length; let doneCount=0, failCount=0, totalKu=0, done=0; const log=[];
  const CONC=Math.min(3, N); let idx=0;
  async function _worker(){
    while(idx<N){
      const f=files[idx++];
      const res=await _ingestFileOnce(f, null, folderName);   // folderName=合集,文件按文件名入库
      done++;
      if(res.ok){ doneCount++; totalKu+=res.ku_count; log.push({name:f.name, ok:true, ku:res.ku_count}); }
      else{ failCount++; log.push({name:f.name, ok:false, err:res.error}); }
      fillReplyPop(_folderHtml(folderName, done, N, '', log, totalKu), '📁 并发摄入中…', 'ok', true);
    }
  }
  await Promise.all(Array.from({length:CONC}, _worker));
  fillReplyPop(_folderHtml(folderName, N, N, '', log, totalKu),
    `✓ 文件夹完成:${doneCount}/${N} 个文件,共 ${totalKu} 条知识${failCount?`,${failCount} 个失败/跳过`:''}${capNote}`, 'ok', true);
  _refreshPlanet();
}
(function(){
  const btn=document.getElementById('cmdFolder'), inp=document.getElementById('cmdFolderInput');
  if(!btn||!inp) return;
  btn.addEventListener('click',()=>inp.click());
  inp.addEventListener('change',async ()=>{
    const fs=[...inp.files]; inp.value=''; if(!fs.length) return;
    if(_ingestBusy){ showReplyPop('📁 文件夹', activeAgent, true); fillReplyPop('', '请等当前摄入完成再选文件夹', 'err'); return; }
    _ingestBusy=true;
    try{ await ingestFolder(fs); } finally{ _ingestBusy=false; }
  });
})();

let chatModeStream=true;
(function(){
  const el=document.getElementById('chatMode');
  if(!el)return;
  const sync=()=>{ el.textContent=chatModeStream?'⚡ 流式':'🔧 任务'; el.classList.toggle('task',!chatModeStream);
    const cmd=document.getElementById('cmd'); if(cmd)cmd.placeholder=chatModeStream?'和我聊聊，逐字回复…':'交给我一个任务，主管自动分诊调度'; };
  el.addEventListener('click',()=>{ chatModeStream=!chatModeStream; sync(); });
  sync();
})();

function toolMenuOpen(){ const m=document.getElementById('toolMenu'); return !!(m&&m.classList.contains('show')); }
function hideToolMenu(){ const m=document.getElementById('toolMenu'); if(m)m.classList.remove('show'); }
function _toolRows(){
  const stream=chatModeStream;
  const hit=id=>{ const b=document.getElementById(id); if(b)b.click(); };
  return [
    {ic: stream?'⚡':'🔧', b: stream?'流式对话':'编排任务', s:'点击切换',
     act:()=>{ hit('chatMode'); renderToolMenu(); }},                       // 切完不关,原地看到变化
    {ic:'📎', b:'上传文件',   s:'摄入知识库',      act:()=>{ hideToolMenu(); hit('cmdAttach'); }},
    {ic:'📁', b:'导入文件夹', s:'逐个摄入',        act:()=>{ hideToolMenu(); hit('cmdFolder'); }},
    {ic:'🖼', b:'附加图片',   s:'发给视觉模型',    act:()=>{ hideToolMenu(); hit('cmdImg'); }},
    {ic:'📄', b:'附件文件',   s:'随问题发给 AI',   act:()=>{ hideToolMenu(); hit('cmdAttachFile'); }},
    {ic:'🔌', b:'连接器/插件', s:'MCP · 技能',     act:()=>{ hideToolMenu(); hit('cmdConn'); }},
  ];
}
function renderToolMenu(){
  const m=document.getElementById('toolMenu'); if(!m)return;
  const rows=_toolRows();
  m.innerHTML=rows.map((r,i)=>`<div class="tool-item" data-i="${i}"><span class="ti-ic">${r.ic}</span>
    <span class="ti-tx"><b>${escapeHtml(r.b)}</b><span>${escapeHtml(r.s)}</span></span></div>`).join('');
  m.querySelectorAll('.tool-item').forEach(el=>el.addEventListener('click',e=>{ e.stopPropagation(); rows[+el.dataset.i].act(); }));
}
(function(){
  const btn=document.getElementById('cmdMore'); if(!btn)return;
  btn.addEventListener('click',e=>{
    e.stopPropagation();
    if(toolMenuOpen()){ hideToolMenu(); return; }
    hideSlashMenu(); hideAtMenu();                                          // 同一时刻只开一个浮层
    const p=document.getElementById('connPanel'); if(p)p.classList.remove('show');
    renderToolMenu();
    document.getElementById('toolMenu').classList.add('show');
  });
  document.addEventListener('click',e=>{                                    // 点外部关闭
    const m=document.getElementById('toolMenu'), tools=document.getElementById('cmdTools');
    if(!toolMenuOpen() || !m) return;
    if(m.contains(e.target) || e.target.id==='cmdMore') return;
    if(tools && tools.contains(e.target)) return;
    hideToolMenu();
  });
})();

const SLASH_COMMANDS=[
  {cmd:'搜索', icon:'🔍', arg:'关键词', desc:'检索知识库', aliases:['search'], run:(q)=>slashSearch(q)},
  {cmd:'摄入', icon:'📎', desc:'上传文件进知识库', aliases:['上传','upload'], run:()=>{ const f=document.getElementById('cmdFile'); if(f)f.click(); }},
  {cmd:'财务', icon:'💰', desc:'打开财务驾驶舱', aliases:['finance'], run:()=>openAgentPanel('finance')},
  {cmd:'地图', icon:'🗺', desc:'打开行程地图', aliases:['行程','admin','map'], run:()=>openAgentPanel('admin')},
  {cmd:'工具', icon:'⚒', desc:'打开工具中心', aliases:['tools'], run:()=>{ try{showModuleInCore('tools');}catch(e){} }},
  {cmd:'星球', icon:'🌐', desc:'打开知识星球', aliases:['planet'], run:()=>{ const b=document.getElementById('planetLaunch'); if(b)b.click(); }},
  {cmd:'设置', icon:'⚙', desc:'打开设置菜单', aliases:['settings'], run:()=>{ try{openSetMenu();}catch(e){} }},
  {cmd:'流式', icon:'⚡', desc:'切到流式对话(逐字直答)', aliases:['stream'], run:()=>{ const el=document.getElementById('chatMode'); if(el&&!chatModeStream)el.click(); }},
  {cmd:'任务', icon:'🔧', desc:'切到编排任务模式', aliases:['task'], run:()=>{ const el=document.getElementById('chatMode'); if(el&&chatModeStream)el.click(); }},
  {cmd:'帮助', icon:'❓', desc:'列出所有斜杠命令', aliases:['help','?'], run:()=>slashHelp()},
  {cmd:'清库', icon:'🗑', danger:true, arg:'确认', desc:'清空知识库(需 /清库 确认)', aliases:['reset'], run:(a)=>slashReset(a)},
];
function openAgentPanel(ag){ try{ loadKernel(ag); showInCore(ag); }catch(e){} }
function slashFind(name){
  name=(name||'').toLowerCase();
  return SLASH_COMMANDS.find(c=>c.cmd.toLowerCase()===name || (c.aliases||[]).some(a=>a.toLowerCase()===name));
}
function runSlash(text){
  const body=text.slice(1).trim();
  const sp=body.indexOf(' ');
  const name=sp<0?body:body.slice(0,sp);
  const arg=sp<0?'':body.slice(sp+1).trim();
  const c=slashFind(name);
  if(!c){ showReplyPop('/'+body, activeAgent, true); fillReplyPop('', `未知命令 /${name}。输入 /帮助 查看全部`, 'err'); return; }
  try{ c.run(arg); }catch(e){ showReplyPop('/'+body, activeAgent, true); fillReplyPop('', '命令执行出错:'+(e&&e.message||e), 'err'); }
}
async function slashSearch(q){
  q=(q||'').trim();
  if(!q){ showReplyPop('/搜索', activeAgent, true); fillReplyPop('', '用法:/搜索 <关键词>', 'err'); return; }
  showReplyPop(`🔍 搜索:${q}`, activeAgent, true); fillReplyPop('', '检索中…', 'ok');
  try{
    const r=await fetch(`${API}/knowledge/search`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q,mode:'hybrid',top_k:8})});
    const d=await r.json(); const hits=d.hits||[];
    if(d.available===false){ fillReplyPop('', '知识核心未启用', 'err'); return; }
    if(!hits.length){ fillReplyPop('', `没有匹配「${q}」的知识`, 'ok'); return; }
    let html=`<div style="color:var(--cyan);font-weight:600;margin-bottom:8px">🔍 ${escapeHtml(q)} · ${hits.length} 条</div>`;
    hits.forEach(h=>{ const sc=(typeof h.score==='number')?h.score.toFixed(2):h.score;
      html+=`<div style="margin:7px 0;padding:8px 10px;border-left:2px solid var(--cyan);background:rgba(0,217,255,.05)">
      <div style="font-size:12px;color:var(--cyan);font-weight:600">${escapeHtml(h.name||'(未命名)')} <span style="color:var(--ink-faint);font-weight:400">${escapeHtml(h.ku_type||'')} · ${sc}</span></div>
      <div style="font-size:11.5px;margin-top:3px;color:var(--ink-dim)">${escapeHtml((h.summary||'').slice(0,140))}</div></div>`; });
    fillReplyPop(html, '混合检索 · 点「🌐 知识星球」看全图', 'ok', true);
  }catch(e){ fillReplyPop('', '搜索失败:后端未连接', 'err'); }
}
function slashHelp(){
  let html='<div style="color:var(--cyan);font-weight:600;margin-bottom:8px">斜杠命令</div>';
  SLASH_COMMANDS.forEach(c=>{ html+=`<div style="display:flex;gap:8px;margin:4px 0;font-size:12px">
    <span style="width:18px">${c.icon}</span><b style="color:${c.danger?'#ff8a87':'#eaf6ff'};min-width:70px">/${escapeHtml(c.cmd)}${c.arg?' &lt;'+escapeHtml(c.arg)+'&gt;':''}</b>
    <span style="color:var(--ink-dim)">${escapeHtml(c.desc)}</span></div>`; });
  showReplyPop('/帮助', activeAgent, true);
  fillReplyPop(html, '直接输入命令即可,如 /搜索 记忆架构', 'ok', true);
}
async function slashReset(arg){
  if((arg||'').trim()!=='确认'){
    showReplyPop('/清库', activeAgent, true);
    fillReplyPop('', '⚠ 清空整个知识库(所有 KU/关系),不可逆。确认请输入:/清库 确认', 'err'); return;
  }
  showReplyPop('/清库 确认', activeAgent, true); fillReplyPop('', '清库中…', 'ok');
  try{
    const r=await fetch(`${_origin}/knowledge/reset`,{method:'POST'});
    const d=await r.json();
    fillReplyPop('已清空知识库', `删除 ${d.cleared_kus??'?'} 条 KU`, 'ok');
    try{ const fr=document.getElementById('planetFrame'); const w=fr&&fr.contentWindow;
      if(w&&fr.src&&fr.src!=='about:blank'&&typeof w.loadData==='function')w.loadData(); }catch(e){}
  }catch(e){ fillReplyPop('', '清库失败:后端未连接', 'err'); }
}
let _slashSel=0, _slashList=[];
function slashFilter(text){
  const q=text.slice(1).toLowerCase();
  return SLASH_COMMANDS.filter(c=> !q || c.cmd.toLowerCase().includes(q)
    || (c.aliases||[]).some(a=>a.toLowerCase().includes(q)) || c.desc.toLowerCase().includes(q));
}
function slashMenuOpen(){ const m=document.getElementById('slashMenu'); return !!(m&&m.classList.contains('show')); }
function hideSlashMenu(){ const m=document.getElementById('slashMenu'); if(m){m.classList.remove('show'); m.innerHTML='';} _slashList=[]; _slashSel=0; }
function updateSlashSel(){ document.querySelectorAll('#slashMenu .slash-item').forEach(el=>el.classList.toggle('sel', +el.dataset.i===_slashSel)); }
function renderSlashMenu(){
  const menu=document.getElementById('slashMenu'); if(!menu)return;
  if(!_slashList.length){ hideSlashMenu(); return; }
  let h='<div class="slash-hint">↑↓ 选择 · Enter 执行 · Esc 关闭</div>';
  _slashList.forEach((c,i)=>{ h+=`<div class="slash-item ${c.danger?'danger':''} ${i===_slashSel?'sel':''}" data-i="${i}">
    <span class="si-ic">${c.icon}</span><span class="si-cmd">/${escapeHtml(c.cmd)}${c.arg?' &lt;'+escapeHtml(c.arg)+'&gt;':''}</span>
    <span class="si-desc">${escapeHtml(c.desc)}</span></div>`; });
  menu.innerHTML=h; menu.classList.add('show');
  menu.querySelectorAll('.slash-item').forEach(el=>{
    el.addEventListener('mouseenter',()=>{ _slashSel=+el.dataset.i; updateSlashSel(); });
    el.addEventListener('mousedown',e=>{ e.preventDefault(); pickSlash(+el.dataset.i); });
  });
}
function pickSlash(i){
  const c=_slashList[i]; if(!c)return;
  const inp=document.getElementById('cmd');
  if(c.arg){ inp.value='/'+c.cmd+' '; hideSlashMenu(); inp.focus(); }   // 需参数:补全命令,等用户输入
  else{ inp.value=''; hideSlashMenu(); runSlash('/'+c.cmd); }
}
function onSlashInput(){
  const inp=document.getElementById('cmd'); const v=inp.value;
  if(v[0]!=='/' || v.indexOf(' ')>=0){ hideSlashMenu(); return; }   // 含空格=进入参数阶段 → 关菜单
  _slashList=slashFilter(v); _slashSel=0; renderSlashMenu();
}
(function(){
  const inp=document.getElementById('cmd'); if(!inp)return;
  inp.addEventListener('input', onSlashInput);
  inp.addEventListener('blur', ()=>setTimeout(hideSlashMenu,150));   // 失焦关(留时间给 mousedown)
  document.addEventListener('keydown', function(e){
    if(e.target!==inp || !slashMenuOpen()) return;
    if(e.key==='ArrowDown'){ e.preventDefault(); e.stopPropagation(); _slashSel=(_slashSel+1)%_slashList.length; updateSlashSel(); }
    else if(e.key==='ArrowUp'){ e.preventDefault(); e.stopPropagation(); _slashSel=(_slashSel-1+_slashList.length)%_slashList.length; updateSlashSel(); }
    else if(e.key==='Enter'||e.key==='Tab'){ e.preventDefault(); e.stopPropagation(); pickSlash(_slashSel); }
    else if(e.key==='Escape'){ e.preventDefault(); e.stopPropagation(); hideSlashMenu(); }
  }, true);
})();

let _atList=[], _atSel=0, _atCache=null, _atCtx=null, _refTriggers={};
async function openConnPanel(){
  const p=document.getElementById('connPanel'); if(!p)return;
  if(p.classList.contains('show')){ p.classList.remove('show'); return; }
  hideSlashMenu(); hideAtMenu();
  p.classList.add('show'); p.innerHTML='<div class="conn-empty">加载中…</div>';
  try{
    const [a,b,c]=await Promise.all([
      fetch(`${API}/ahs/skills`).then(x=>x.json()).catch(()=>({})),
      fetch(`${API}/ahs/mcp/servers`).then(x=>x.json()).catch(()=>({})),
      fetch(`${API}/ahs/md-skills`).then(x=>x.json()).catch(()=>({})),
    ]);
    renderConnPanel(p, a.skills||[], b.servers||[], c.skills||[]);
  }catch(e){ p.innerHTML='<div class="conn-empty">后端未连接</div>'; }
}
function connBadge(trust){ const t=trust||'mid'; const lb={high:'高',mid:'中',low:'低'}[t]||t; return `<span class="conn-badge ${t}">${lb}信任</span>`; }
function renderConnPanel(p, skills, servers, md){
  const kindIc={local:'🧩',mcp:'🔌',web_api:'🌐'};
  let h=`<div class="conn-sec"><div class="conn-sec-h"><span>MCP 连接器</span><span>${servers.length}</span></div>`;
  h+= servers.length?'':'<div class="conn-empty" style="padding:8px">未接入</div>';
  servers.forEach(s=>{ h+=`<div class="conn-row"><span class="cr-ic">🔌</span>
    <div><div class="cr-name">${escapeHtml(s.name||s.id)}</div><div class="cr-meta">${s.tool_count||0} 工具 · ${escapeHtml(s.transport||'')}</div></div></div>`; });
  h+='</div>';
  h+=`<div class="conn-sec"><div class="conn-sec-h"><span>技能</span><span>${skills.length}</span></div>`;
  h+= skills.length?'':'<div class="conn-empty" style="padding:8px">无</div>';
  skills.forEach(s=>{ const blk=s.blocked; h+=`<div class="conn-row"><span class="cr-ic">${kindIc[s.kind]||'🧩'}</span>
    <div><div class="cr-name">${escapeHtml(s.name||s.id)}${blk?' <span class="conn-badge low">已封杀</span>':''}</div>
    <div class="cr-meta">${escapeHtml((s.description||'').slice(0,42))}</div></div>
    <div class="cr-right">${connBadge(s.trust)}<label class="conn-sw"><input type="checkbox" ${s.enabled&&!blk?'checked':''} ${blk?'disabled':''} data-skill="${escapeHtml(s.id)}"><i></i></label></div></div>`; });
  h+='</div>';
  h+=`<div class="conn-sec"><div class="conn-sec-h"><span>SKILL.md 指令技能</span><span>${md.length}</span></div>`;
  h+= md.length?'':'<div class="conn-empty" style="padding:8px">无</div>';
  md.forEach(s=>{ h+=`<div class="conn-row"><span class="cr-ic">📄</span>
    <div><div class="cr-name">${escapeHtml(s.name||s.id)}</div><div class="cr-meta">${escapeHtml((s.description||'').slice(0,42))}</div></div>
    <div class="cr-right"><label class="conn-sw"><input type="checkbox" ${s.enabled?'checked':''} data-md="${escapeHtml(s.id)}"><i></i></label></div></div>`; });
  h+='</div><div class="conn-foot"><button id="connManage">⚒ 在工具中心管理全部</button></div>';
  p.innerHTML=h;
  p.querySelectorAll('input[data-skill]').forEach(el=>el.addEventListener('change',async()=>{
    _atCache=null;   // 启停变了 → 让 @引用缓存失效
    try{ const r=await fetch(`${API}/ahs/skills/${encodeURIComponent(el.dataset.skill)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:el.checked})}); if(!r.ok)throw 0; }
    catch(e){ el.checked=!el.checked; }
  }));
  p.querySelectorAll('input[data-md]').forEach(el=>el.addEventListener('change',async()=>{
    _atCache=null;
    try{ const r=await fetch(`${API}/ahs/md-skills/${encodeURIComponent(el.dataset.md)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:el.checked})}); if(!r.ok)throw 0; }
    catch(e){ el.checked=!el.checked; }
  }));
  const mg=document.getElementById('connManage'); if(mg)mg.addEventListener('click',()=>{ p.classList.remove('show'); try{showModuleInCore('tools');}catch(e){} });
}
(function(){
  const btn=document.getElementById('cmdConn'); if(btn)btn.addEventListener('click',e=>{ e.stopPropagation(); openConnPanel(); });
  document.addEventListener('click',e=>{ const p=document.getElementById('connPanel');
    if(p&&p.classList.contains('show')&&!p.contains(e.target)&&e.target.id!=='cmdConn') p.classList.remove('show'); });
})();

async function ensureAtCache(){
  if(_atCache) return _atCache;
  try{
    const [a,c]=await Promise.all([
      fetch(`${API}/ahs/skills`).then(x=>x.json()).catch(()=>({})),
      fetch(`${API}/ahs/md-skills`).then(x=>x.json()).catch(()=>({})),
    ]);
    const sk=(a.skills||[]).filter(s=>s.enabled&&!s.blocked).map(s=>({id:s.id,name:s.name||s.id,trigger:(s.triggers&&s.triggers[0])||s.name||s.id,kind:s.kind}));
    const md=(c.skills||[]).filter(s=>s.enabled).map(s=>({id:s.id,name:s.name||s.id,trigger:(s.triggers&&s.triggers[0])||s.name||s.id,kind:'md'}));
    _atCache=sk.concat(md);
  }catch(e){ _atCache=[]; }
  return _atCache;
}
function atMenuOpen(){ const m=document.getElementById('atMenu'); return !!(m&&m.classList.contains('show')); }
function hideAtMenu(){ const m=document.getElementById('atMenu'); if(m){m.classList.remove('show'); m.innerHTML='';} _atList=[]; _atSel=0; _atCtx=null; }
function _atSelUpdate(){ document.querySelectorAll('#atMenu .slash-item').forEach(x=>x.classList.toggle('sel',+x.dataset.i===_atSel)); }
function atQueryAtCursor(){
  const inp=document.getElementById('cmd'); const v=inp.value; const pos=inp.selectionStart==null?v.length:inp.selectionStart;
  const upto=v.slice(0,pos); const at=upto.lastIndexOf('@');
  if(at<0) return null;
  const frag=upto.slice(at+1);
  if(/\s/.test(frag)) return null;      // @ 后有空格 = 已结束引用
  return {at, pos, q:frag};
}
function renderAtMenu(){
  const m=document.getElementById('atMenu'); if(!m)return;
  if(!_atList.length){ hideAtMenu(); return; }
  const kindIc={local:'🧩',mcp:'🔌',web_api:'🌐',md:'📄'};
  let h='<div class="slash-hint">@ 引用技能/连接器 · Enter 插入 · Esc 关闭</div>';
  _atList.forEach((c,i)=>{ h+=`<div class="slash-item ${i===_atSel?'sel':''}" data-i="${i}">
    <span class="si-ic">${kindIc[c.kind]||'🧩'}</span><span class="si-cmd">@${escapeHtml(c.name)}</span>
    <span class="si-desc">触发:${escapeHtml(c.trigger)}</span></div>`; });
  m.innerHTML=h; m.classList.add('show');
  m.querySelectorAll('.slash-item').forEach(el=>{
    el.addEventListener('mouseenter',()=>{ _atSel=+el.dataset.i; _atSelUpdate(); });
    el.addEventListener('mousedown',e=>{ e.preventDefault(); pickAt(+el.dataset.i); });
  });
}
function pickAt(i){
  const c=_atList[i]; if(!c||!_atCtx)return;
  const inp=document.getElementById('cmd'); const v=inp.value;
  const before=v.slice(0,_atCtx.at), after=v.slice(_atCtx.pos);
  const ins='@'+c.name+' ';
  inp.value=before+ins+after; _refTriggers[c.name]=c.trigger;
  hideAtMenu(); inp.focus();
  const np=(before+ins).length; try{ inp.setSelectionRange(np,np); }catch(e){}
}
async function onAtInput(){
  const inp=document.getElementById('cmd');
  if(inp.value[0]==='/'){ hideAtMenu(); return; }   // 斜杠命令优先
  const ctx=atQueryAtCursor();
  if(!ctx){ hideAtMenu(); return; }
  _atCtx=ctx;
  const all=await ensureAtCache();
  const q=ctx.q.toLowerCase();
  _atList=all.filter(c=> !q || c.name.toLowerCase().includes(q) || (c.trigger||'').toLowerCase().includes(q)).slice(0,8);
  _atSel=0; renderAtMenu();
}
function applyRefs(task){
  let out=task;
  for(const nm in _refTriggers){
    if(task.includes('@'+nm)){ const tr=_refTriggers[nm]; if(tr && !out.includes(tr)) out+=' '+tr; }
  }
  _refTriggers={};
  return out;
}
(function(){
  const inp=document.getElementById('cmd'); if(!inp)return;
  inp.addEventListener('input', onAtInput);
  inp.addEventListener('blur', ()=>setTimeout(hideAtMenu,150));
  document.addEventListener('keydown', function(e){
    if(e.target!==inp || !atMenuOpen()) return;
    if(e.key==='ArrowDown'){ e.preventDefault(); e.stopPropagation(); _atSel=(_atSel+1)%_atList.length; _atSelUpdate(); }
    else if(e.key==='ArrowUp'){ e.preventDefault(); e.stopPropagation(); _atSel=(_atSel-1+_atList.length)%_atList.length; _atSelUpdate(); }
    else if(e.key==='Enter'||e.key==='Tab'){ e.preventDefault(); e.stopPropagation(); pickAt(_atSel); }
    else if(e.key==='Escape'){ e.preventDefault(); e.stopPropagation(); hideAtMenu(); }
  }, true);
})();

let _pendingImages=[];   // [{media_type,data(base64),url(预览),name}]
const _IMG_MAX=6, _IMG_MAXDIM=1280, _IMG_Q=0.85;
function _downscaleImage(file){
  return new Promise((resolve,reject)=>{
    const img=new Image(); const url=URL.createObjectURL(file);
    img.onload=()=>{
      URL.revokeObjectURL(url);
      let w=img.naturalWidth||img.width, h=img.naturalHeight||img.height;
      const scale=Math.min(1, _IMG_MAXDIM/Math.max(w,h||1));
      w=Math.max(1,Math.round(w*scale)); h=Math.max(1,Math.round(h*scale));
      const cv=document.createElement('canvas'); cv.width=w; cv.height=h;
      cv.getContext('2d').drawImage(img,0,0,w,h);
      const isPng=/png/i.test(file.type);            // png 可能带透明 → 保 png,其余转 jpeg 省体积
      const mt=isPng?'image/png':'image/jpeg';
      let durl; try{ durl=cv.toDataURL(mt,_IMG_Q); }catch(e){ reject(e); return; }
      resolve({media_type:mt, data:durl.split(',')[1]||'', url:durl, name:file.name||'image'});
    };
    img.onerror=()=>{ URL.revokeObjectURL(url); reject(new Error('image load failed')); };
    img.src=url;
  });
}
async function attachImages(files){
  for(const f of [...files]){
    if(!/^image\//.test(f.type||'')) continue;
    if(_pendingImages.length>=_IMG_MAX) break;
    try{ _pendingImages.push(await _downscaleImage(f)); }catch(e){}
  }
  renderImgStrip(); visionAdvisory();
}
function renderImgStrip(){
  const s=document.getElementById('imgStrip'); if(!s)return;
  if(!_pendingImages.length){ s.classList.remove('show'); s.innerHTML=''; return; }
  let h='';
  _pendingImages.forEach((im,i)=>{ h+=`<div class="img-thumb" title="${escapeHtml(im.name)}"><img src="${im.url}" alt=""><span class="it-x" data-i="${i}">✕</span></div>`; });
  h+='<div class="img-note" id="imgNote"></div>';
  s.innerHTML=h; s.classList.add('show');
  s.querySelectorAll('.it-x').forEach(el=>el.addEventListener('click',()=>{ _pendingImages.splice(+el.dataset.i,1); renderImgStrip(); visionAdvisory(); }));
}
function clearPendingImages(){ _pendingImages=[]; renderImgStrip(); }
async function visionAdvisory(){
  const note=document.getElementById('imgNote'); if(!note)return;
  try{
    const m=await fetch(`${API}/model`).then(x=>x.json());
    const model=(m.model||'').toLowerCase();
    const vision=/(claude|sonnet|opus|haiku|gpt-4o|gpt-4\.1|gpt-5|o1|o3|[-_]vl|vision|gemini|pixtral|llava)/i.test(model);
    if(vision){ note.textContent=`📷 ${_pendingImages.length} 张图将随问题发给视觉模型`; note.style.color='var(--ink-dim)'; }
    else{ note.textContent=`⚠ 当前模型「${m.model||'?'}」可能不支持图片,请到「连接设置」切换视觉模型(claude / gpt-4o / qwen-vl 等)`; note.style.color='var(--amber)'; }
  }catch(e){}
}
(function(){
  const btn=document.getElementById('cmdImg'), inp=document.getElementById('cmdImgFile');
  if(btn&&inp){
    btn.addEventListener('click',()=>inp.click());
    inp.addEventListener('change',()=>{ const fs=[...inp.files]; inp.value=''; if(fs.length) attachImages(fs); });  // 拷数组再清空(同 folder:inp.value='' 会清空活引用)
  }
})();

let _pendingFiles=[];   // [{id,name,size,mime,url,text}]
const _FILE_MAX=6, _FILE_MAXB=20*1024*1024;
function _fileSize(sz){
  if(!sz)return '';
  return sz>=1048576?(sz/1048576).toFixed(1)+'MB':(sz/1024).toFixed(1)+'KB';
}
function _attachmentsHtml(atts){
  if(!atts || !atts.length) return '';
  return '<div class="cc-files">'+atts.map(a=>{
    const nm=escapeHtml(a.name||'文件'), sz=_fileSize(a.size);
    const url=a.url?escapeHtml(a.url):'';
    if(url) return `<a class="cc-file" href="${url}" target="_blank" rel="noopener noreferrer">📄 <span>${nm}</span><span class="cc-file-size">${sz}</span></a>`;
    return `<span class="cc-file">📄 <span>${nm}</span><span class="cc-file-size">${sz}</span></span>`;
  }).join('')+'</div>';
}
async function attachFiles(files){
  for(const f of [...files]){
    if(_pendingFiles.length>=_FILE_MAX) break;
    if(f.size>_FILE_MAXB){ console.warn(`[attach] 跳过超限文件: ${f.name} (${f.size}B > 20MB)`); continue; }
    try{
      const fd=new FormData(); fd.append('file', f);
      const r=await fetch(`${API}/upload`,{method:'POST',body:fd});
      if(!r.ok) throw new Error('http '+r.status);
      const a=await r.json();
      _pendingFiles.push({id:a.id,name:a.name||f.name,size:a.size,mime:a.mime,url:a.url,text:a.text||''});
    }catch(e){ console.warn(`[attach] 上传失败: ${f.name}`, e); }
  }
  renderFileStrip();
}
function renderFileStrip(){
  const s=document.getElementById('fileStrip'); if(!s)return;
  if(!_pendingFiles.length){ s.classList.remove('show'); s.innerHTML=''; return; }
  s.innerHTML=_pendingFiles.map((f,i)=>
    `<div class="file-card" title="${escapeHtml(f.name)}"><span>📄</span><span class="fc-name">${escapeHtml(f.name)}</span>`
    +`<span class="fc-size" style="color:var(--dim);font-size:11px">${_fileSize(f.size)}</span>`
    +`<span class="fc-x" data-i="${i}">✕</span></div>`).join('');
  s.classList.add('show');
  s.querySelectorAll('.fc-x').forEach(el=>el.addEventListener('click',()=>{ _pendingFiles.splice(+el.dataset.i,1); renderFileStrip(); }));
}
function clearPendingFiles(){ _pendingFiles=[]; renderFileStrip(); }
(function(){
  const btn=document.getElementById('cmdAttachFile'), inp=document.getElementById('cmdFileUpload');
  if(btn&&inp){
    btn.addEventListener('click',()=>inp.click());
    inp.addEventListener('change',()=>{ const fs=[...inp.files]; inp.value=''; if(fs.length) attachFiles(fs); });
  }
})();

let _chatHistory=[];   // 多轮对话历史 [{role,content}];随请求带给后端 + 界面渲染成对话线程
function clearChatHistory(){ _chatHistory=[]; }   // 新话题/清屏可调

let _sessionId = localStorage.getItem('foxclawSessionId') || '';
async function ensureSession(){
  if(_sessionId){
    const ok = await fetch(`${API}/sessions/${_sessionId}`).then(r=>r.ok).catch(()=>false);
    if(ok) return _sessionId;
  }
  return newSession(false);
}
async function newSession(focus=true){
  try{
    const s=await fetch(`${API}/sessions`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json());
    _sessionId=s.id; localStorage.setItem('foxclawSessionId',_sessionId);
    _chatHistory=[];
    if(focus){ document.getElementById('coreChat')?.classList.remove('show'); }
    await loadSessions();
    return _sessionId;
  }catch(e){ return ''; }
}
async function openSession(id){
  try{
    const d=await fetch(`${API}/sessions/${id}`).then(r=>r.json());
    _sessionId=d.id; localStorage.setItem('foxclawSessionId',_sessionId);
    _chatHistory=(d.messages||[]).map(m=>({role:m.role,content:m.content,ts:(m.ts||0)*1000,attachments:m.attachments||undefined}));
    renderSessionThread(d.agent_id||'supervisor');
    await loadSessions();
  }catch(e){}
}
function renderSessionThread(agent){
  const body=document.querySelector('#coreChat .cc-body'); if(!body)return;
  const a=document.getElementById('ccAgent'); if(a)a.textContent=`${AGENT_CN[agent]||agent} Agent`;
  let h='';
  for(const m of _chatHistory){
    const inner=m.role==='user'?escapeHtml(m.content)+_attachmentsHtml(m.attachments):renderMarkdown(m.content);
    h+=_chatRow(m.role, inner, agent, m.ts, false);
  }
  body.innerHTML=h||'<div class="kp-empty" style="padding:30px">这个会话还没有消息</div>';
  Center.chat();
  body.scrollTop=body.scrollHeight;
}
async function loadSessions(){
  const list=document.getElementById('nsList'); if(!list)return;
  const cnt=document.getElementById('nsCount');
  try{
    const d=await fetch(`${API}/sessions?limit=50`).then(r=>r.json());
    const ss=d.sessions||[];
    if(cnt) cnt.textContent=ss.length||'0';
    if(!ss.length){ list.innerHTML='<div class="kp-empty">还没有会话 · 发一条消息即开始</div>'; return; }
    list.innerHTML=ss.map(s=>{
      const title=s.title||'新会话';
      const when=s.updated_at?new Date(s.updated_at*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}):'';
      const meta=[when, s.message_count?`${s.message_count} 条`:''].filter(Boolean).join(' · ');
      return `<div class="ns-item${s.id===_sessionId?' on':''}" data-sid="${escapeHtml(s.id)}" title="${escapeHtml(title)}">
        <b>${escapeHtml(title)}</b>
        <span>${escapeHtml(meta)}</span>
        <span class="ns-del" data-del="${escapeHtml(s.id)}" title="删除会话">✕</span></div>`;
    }).join('');
    list.querySelectorAll('.ns-item').forEach(el=>{
      el.onclick=(ev)=>{
        if(ev.target.dataset.del){ ev.stopPropagation(); delSession(ev.target.dataset.del); return; }
        openSession(el.dataset.sid);
      };
    });
  }catch(e){ list.innerHTML='<div class="kp-empty">后端未连接</div>'; }
}
async function delSession(id){
  try{ await fetch(`${API}/sessions/${id}`,{method:'DELETE'}); }catch(e){}
  if(id===_sessionId){ _sessionId=''; _chatHistory=[];
    localStorage.removeItem('foxclawSessionId');
    document.getElementById('coreChat')?.classList.remove('show');
    await ensureSession();
  }
  await loadSessions();
}
function _fmtTime(ts){ const d=new Date(ts||Date.now()); return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2); }
function _chatRow(role, inner, agent, ts, isSlot){
  const t=_fmtTime(ts);
  if(role==='user'){
    return `<div class="chat-row me"><div class="chat-col">`
      +`<div class="chat-meta"><span class="chat-time">${t}</span><span class="chat-name">你</span></div>`
      +`<div class="chat-bubble me">${inner}</div></div>`
      +`<div class="chat-av me">你</div></div>`;
  }
  const nm=`${AGENT_CN[agent]||agent} Agent`, av=(AGENT_CN[agent]||'H').slice(0,1);
  const bubble = isSlot
    ? `<div class="chat-bubble ai cc-md" id="ccReply"><span class="cc-loading">◌ 思考中…</span></div>`
    : `<div class="chat-bubble ai cc-md">${inner}</div>`;
  return `<div class="chat-row ai"><div class="chat-av ai">${escapeHtml(av)}</div><div class="chat-col">`
    +`<div class="chat-meta"><span class="chat-name">${escapeHtml(nm)}</span>${isSlot?'':`<span class="chat-time">${t}</span>`}</div>`
    +bubble+`</div></div>`;
}
function renderChatThread(currentQ, agent, attachments){
  document.getElementById('ccAgent').textContent=`${AGENT_CN[agent]||agent} Agent`;
  const body=document.querySelector('#coreChat .cc-body'); if(!body) return;
  let h='';
  for(const m of _chatHistory){
    const inner=m.role==='user'?escapeHtml(m.content)+_attachmentsHtml(m.attachments):renderMarkdown(m.content);
    h += _chatRow(m.role, inner, agent, m.ts, false);
  }
  h += _chatRow('user', escapeHtml(currentQ||'')+_attachmentsHtml(attachments), agent, Date.now(), false);
  h += _chatRow('ai', '', agent, Date.now(), true);   // 空答槽:#ccReply 供流式写入
  h += `<div class="cc-status" id="ccStatus"></div>`;
  body.innerHTML=h;
  Center.chat();
  body.scrollTop=body.scrollHeight;
}
function renderEvidence(items){
  const wrap=document.getElementById('ccEvidence'); if(!wrap)return;
  if(!items||!items.length){ wrap.hidden=true; wrap.innerHTML=''; return; }
  const STAT={verified:['✓','已验证','ev-ok'],unverified:['?','未核验','ev-warn'],failed:['✕','未完成','ev-bad']};
  const rows=items.map(it=>{
    const s=STAT[it.status]||STAT.unverified;
    const chips=(it.evidence||[]).map(e=>{
      const label=escapeHtml(e.label||e.ref||'');
      const tip=escapeHtml(e.ref||'');
      return e.kind==='url'
        ? `<a class="ev-item" href="${encodeURI(e.ref)}" target="_blank" rel="noopener" title="${tip}">${label}</a>`
        : `<span class="ev-item" title="${tip}">${label}</span>`;
    }).join('');
    const why=it.reason?`<span class="ev-why">${escapeHtml(it.reason)}</span>`:'';
    return `<div class="ev-row"><span class="ev-chip ${s[2]}">${s[0]} ${s[1]}</span>`
         + `<span class="ev-tool">${escapeHtml(it.tool||'')}</span>${why}${chips}</div>`;
  }).join('');
  wrap.innerHTML=`<div class="ev-head">执行凭据 <span class="ev-count">${items.length}</span></div>${rows}`;
  wrap.hidden=false;
}

async function transmitStream(task, agent, images, attachments){
  renderChatThread(task, agent, attachments);   // 先把历史+当前问(含附件卡片)渲染成对话线程,再流式写入答案槽
  const _ccBody=document.querySelector('#coreChat .cc-body');   // 注意:下方请求体也叫 body,故这里另起名避免遮蔽
  const rp=document.getElementById('ccReply'), st=document.getElementById('ccStatus');
  rp.textContent=''; rp.style.color='var(--ink)'; st.style.color='var(--ink-dim)'; st.textContent='◌ 流式生成中…';
  let acc='';
  let _thinkWrap=document.getElementById('ccThink');
  if(!_thinkWrap && rp.parentNode){
    _thinkWrap=document.createElement('details');
    _thinkWrap.id='ccThink'; _thinkWrap.className='cc-think'; _thinkWrap.hidden=true;
    _thinkWrap.innerHTML='<summary>推理过程</summary><div class="cc-think-body" id="ccThinkBody"></div>';
    rp.parentNode.insertBefore(_thinkWrap, rp);
  }
  const _thinkBody=document.getElementById('ccThinkBody');
  if(_thinkBody) _thinkBody.textContent='';
  if(_thinkWrap){ _thinkWrap.hidden=true; _thinkWrap.open=false; }
  let _evWrap=document.getElementById('ccEvidence');
  if(!_evWrap && rp.parentNode){
    _evWrap=document.createElement('div');
    _evWrap.id='ccEvidence'; _evWrap.className='cc-ev'; _evWrap.hidden=true;
    rp.parentNode.insertBefore(_evWrap, rp.nextSibling);
  }
  if(_evWrap){ _evWrap.hidden=true; _evWrap.innerHTML=''; }
  let _evs=[];
  try{
    if(!_sessionId) await ensureSession();          // 保证本轮问答有会话归属(否则落不了盘)
    const body={task, agent_id:agent, history:_chatHistory.slice(-12), session_id:_sessionId};
    if(images && images.length) body.images=images;   // 多模态:图片随问题发给视觉模型
    if(attachments && attachments.length) body.attachments=attachments;   // 聊天附件:随问题发给 LLM
    const r=await fetch(`${API}/chat/stream`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok||!r.body){ st.textContent='⚠ 流式请求失败: '+r.status; st.style.color='var(--pink)'; return; }
    const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {value,done}=await reader.read(); if(done)break;
      buf+=dec.decode(value,{stream:true});
      let idx;
      while((idx=buf.indexOf('\n\n'))>=0){
        const block=buf.slice(0,idx); buf=buf.slice(idx+2);
        const evName=((block.match(/^event:\s*(\S+)/m)||[])[1])||'message';
        const dataLine=((block.match(/^data:\s*([\s\S]*)$/m)||[])[1]||'').trim();
        if(!dataLine)continue;
        let ev; try{ ev=JSON.parse(dataLine); }catch(e){ continue; }
        if(evName==='thinking'||ev.thinking){          // 推理增量 → 折叠区,不进正文
          const t=ev.thinking||'';
          if(t){ if(_thinkWrap) _thinkWrap.hidden=false; if(_thinkBody) _thinkBody.textContent+=t; }
          continue;
        }
        if(evName==='evidence'||ev.evidence){          // 执行证据 → 答案下方核对区
          if(Array.isArray(ev.evidence)){ _evs=ev.evidence; renderEvidence(_evs); }
          continue;
        }
        if(ev.delta){ acc+=ev.delta; rp.textContent=acc; if(_ccBody) _ccBody.scrollTop=_ccBody.scrollHeight; }
        if(ev.error){ st.textContent='⚠ '+ev.error; st.style.color='var(--pink)'; }
        if(ev.done){ st.textContent='✓ 完成'; }
      }
    }
    if(acc){
      renderReply(acc);                    // 流式结束 → 富文本渲染(markdown + 内嵌图/音/视频)
      const _now=Date.now();
      _chatHistory.push({role:'user',content:task,ts:_now,attachments:attachments||undefined},{role:'assistant',content:acc,ts:_now});
      if(_chatHistory.length>24) _chatHistory=_chatHistory.slice(-24);   // 只留最近 ~12 轮
      loadSessions();                        // 会话标题/条数会变 → 刷新左栏列表
    }
    if(!acc && st.textContent.indexOf('⚠')<0) st.textContent='(无输出)';
    markTask(task,'ok');
  }catch(e){ st.textContent='⚠ '+e; st.style.color='var(--pink)'; markTask(task,'blocked'); }
  if(typeof loadMemStats==='function') loadMemStats();
}

async function transmitTaskStream(task, agent){
  const rp=document.getElementById('ccReply'), st=document.getElementById('ccStatus');
  rp.textContent=''; rp.style.color='var(--ink)'; st.style.color='var(--ink-dim)'; st.textContent='◌ 认知管线启动…';
  let acc='', stages=[];
  const STAGE_CN={perceive:'感知',recall:'召回',plan:'规划',route:'路由',act:'执行',verify:'校验',reflect:'反思'};
  let _planWrap=document.getElementById('ccPlan');
  if(!_planWrap && rp.parentNode){
    _planWrap=document.createElement('details');
    _planWrap.id='ccPlan'; _planWrap.className='cc-plan'; _planWrap.hidden=true;
    _planWrap.innerHTML='<summary>执行计划</summary><ol class="cc-plan-body" id="ccPlanBody"></ol>';
    rp.parentNode.insertBefore(_planWrap, rp);
  }
  const _planBody=document.getElementById('ccPlanBody');
  if(_planBody) _planBody.innerHTML='';
  if(_planWrap){ _planWrap.hidden=true; _planWrap.open=true; }
  try{
    const r=await fetch(`${API}/task/stream`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task, agent_id:agent})});
    if(!r.ok||!r.body){ st.textContent='⚠ 流式任务请求失败: '+r.status; st.style.color='var(--pink)'; markTask(task,'blocked'); return; }
    const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {value,done}=await reader.read(); if(done)break;
      buf+=dec.decode(value,{stream:true});
      let idx;
      while((idx=buf.indexOf('\n\n'))>=0){
        const line=buf.slice(0,idx).trim(); buf=buf.slice(idx+2);
        if(!line.startsWith('data:'))continue;
        let ev; try{ ev=JSON.parse(line.slice(5).trim()); }catch(e){ continue; }
        if(ev.stage){ stages.push(STAGE_CN[ev.stage]||ev.stage); st.textContent='◌ '+stages.join(' → '); }
        if(ev.plan && ev.plan.steps && ev.plan.steps.length){   // 计划:整轮固定展示,供用户先看步骤
          if(_planBody) _planBody.innerHTML=ev.plan.steps.map(s=>`<li>${escapeHtml(String(s))}</li>`).join('');
          if(_planWrap) _planWrap.hidden=false;
          const _flow={parallel:'并行',sequential:'顺序',single:'单步'}[ev.plan.flow]||ev.plan.flow||'';
          st.textContent='◌ 计划('+_flow+' · '+ev.plan.steps.length+' 步)';
        }
        if(ev.delta){ acc+=ev.delta; rp.textContent=acc; rp.scrollTop=rp.scrollHeight; }
        if(ev.error){ st.textContent='⚠ '+ev.error; st.style.color='var(--pink)'; markTask(task,'blocked'); }
        if(ev.result){
          const res=ev.result||{};
          if(res.interrupted){ st.textContent='⚠ 任务被中断(部分完成)'; markTask(task,'blocked'); }
          else if(res.error==='harness_blocked'){ st.textContent='⚠ 被 Harness 拦截:'+(res.reason||''); st.style.color='var(--pink)'; markTask(task,'blocked'); }
          else if(res.status==='needs_confirmation'){ st.textContent='⏸ 待确认:'+((res.pending_action||{}).skill_id||''); markTask(task,'blocked'); }
          else{
            const reply=(typeof res.result==='string'?res.result:(res.result?JSON.stringify(res.result):''))||acc;
            if(!acc && reply){ acc=reply; rp.textContent=reply; }   // skill dispatch 路径无 delta,用 result 兜底
            st.textContent='✓ 完成'; markTask(task,'ok');
          }
        }
      }
    }
    if(acc) renderReply(acc);
    else if(st.textContent.indexOf('⚠')<0 && st.textContent.indexOf('⏸')<0) st.textContent='(无输出)';
  }catch(e){ st.textContent='⚠ '+e; st.style.color='var(--pink)'; markTask(task,'blocked'); }
  if(typeof loadMemStats==='function') loadMemStats();
}

function prependTask(name,agent,status){
  const list=document.getElementById('taskList'); if(!list)return;
  const now=new Date(); const hh=String(now.getHours()).padStart(2,'0')+':'+String(now.getMinutes()).padStart(2,'0');
  const div=document.createElement('div');
  div.className='task'+(status==='ok'?' ok':''); div.dataset.task=name;
  div.innerHTML=`<div class="ti">${status==='ok'?'✓':status==='blocked'?'✕':'◌'}</div>
    <div class="tb"><b>${escapeHtml(name)}</b><span>${escapeHtml(AGENT_CN[agent]||agent)} Agent</span></div><div class="tt">${hh}</div>`;
  list.prepend(div);
  while(list.children.length>6)list.removeChild(list.lastChild);
}
function markTask(name,status){
  const list=document.getElementById('taskList'); if(!list)return;
  const row=[...list.children].find(c=>c.dataset.task===name);
  if(row){row.className='task'+(status==='ok'?' ok':'');
    row.querySelector('.ti').textContent=status==='ok'?'✓':status==='blocked'?'✕':'◌';}
}

window.actPulse=0;
function connectWS(){
  try{
    const _k=localStorage.getItem('foxclawApiKey');
    const ws=new WebSocket(WS + (_k?('?key='+encodeURIComponent(_k)):''));
    ws.onmessage=ev=>{
      let m; try{m=JSON.parse(ev.data);}catch(e){return;}
      const e=m.event, d=m.data||{};
      window.actPulse=Math.min(window.actPulse+0.4,1.5);
      if(e==='harness_block'){ flashCmd('var(--pink)'); }
      else if(e==='policy_updated'){ flashCmd('var(--violet)'); }
    };
    ws.onclose=()=>setTimeout(connectWS,3000);
  }catch(e){ setTimeout(connectWS,3000); }
}
function flashCmd(c){
  const bar=document.querySelector('.cmdbar');
  if(!bar)return; const old=bar.style.boxShadow;
  bar.style.boxShadow=`0 0 24px ${c}`; bar.style.borderColor=c;
  setTimeout(()=>{bar.style.boxShadow=''; bar.style.borderColor='';},700);
}

function openOverlay(agent){
  const m=KP_META[agent]||KP_META.office;
  document.getElementById('ovTitle').textContent=m.title;
  document.getElementById('ovTag').textContent=m.tag;
  const body=document.getElementById('ovBody');
  const d=_lastKernelData[agent]||{};
  if(agent==='admin'){
    body.innerHTML=`<div class="ov-grid">
      <div class="ov-spacer"></div>
      <div style="position:relative;height:100%">
        <div id="ovMap" style="width:100%;height:100%"></div>
        <div class="map-search">
          <input id="mapSearchInput" placeholder="🔍 搜索地点…">
          <div id="mapSearchResults" class="map-search-results"></div>
        </div>
      </div>
      <div class="ov-side" id="ovItinSide">${itinSideHTML(d)}</div>
    </div>`;
    setTimeout(()=>{drawAMapInto('ovMap',d.map_data||[]); initMapSearch();},80);
  }else if(agent==='office'){
    body.innerHTML=`<div style="height:100%"><canvas id="ovGraph" style="width:100%;height:100%"></canvas></div>`;
    setTimeout(()=>drawKnowledgeGraph('ovGraph',d),80);
  }else if(agent==='finance'){
    body.innerHTML=`<div style="max-width:900px;margin:0 auto">${renderFinance(d)}</div>`;
  }else if(agent==='supervisor'){
    body.innerHTML=`<div style="max-width:900px;margin:0 auto">${renderSituation(d)}</div>`;
  }else{
    body.innerHTML=`<div style="max-width:700px;margin:0 auto">${document.getElementById('kpBody')?.innerHTML||''}</div>`;
  }
  document.getElementById('ovMask').classList.add('show');
}
function closeOverlay(){document.getElementById('ovMask').classList.remove('show');}
document.getElementById('kpFull')?.addEventListener('click',()=>openOverlay(activeAgent));
document.getElementById('ovClose')?.addEventListener('click',closeOverlay);
document.getElementById('ovMask')?.addEventListener('click',e=>{if(e.target.id==='ovMask')closeOverlay();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeOverlay();});

let autoMap=false;
document.getElementById('ovAutoMap')?.addEventListener('change',e=>{autoMap=e.target.checked;});

function drawKnowledgeGraph(canvasId,d){
  const c=document.getElementById(canvasId); if(!c)return;
  const x=c.getContext('2d');
  function size(){const r=c.parentElement.getBoundingClientRect();c.width=r.width;c.height=r.height;}
  size();
  const topics=Object.keys(d.topic_mastery||{});
  const N=Math.max(d.total_items||8,6);
  const nodes=[];
  for(let i=0;i<N;i++){nodes.push({a:i/N*Math.PI*2,r:0.3+Math.random()*0.4,
    topic:topics[i%(topics.length||1)]||'',s:Math.random()*4+4});}
  let rot=0;
  const COL=['#00d9ff','#ffc24b','#00ffb2','#ff9d00','#d99a3c'];
  function loop(){
    if(!document.getElementById('ovMask').classList.contains('show'))return;
    x.clearRect(0,0,c.width,c.height);rot+=0.002;
    const cx=c.width/2,cy=c.height/2,R=Math.min(cx,cy)*0.8;
    nodes.forEach(n=>{n._x=cx+Math.cos(n.a+rot)*n.r*R;n._y=cy+Math.sin(n.a+rot)*n.r*R;});
    x.lineWidth=1;
    for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
      if(nodes[i].topic&&nodes[i].topic===nodes[j].topic){
        x.strokeStyle='rgba(255,194,75,.25)';x.beginPath();
        x.moveTo(nodes[i]._x,nodes[i]._y);x.lineTo(nodes[j]._x,nodes[j]._y);x.stroke();}
    }
    nodes.forEach((n,i)=>{const col=COL[topics.indexOf(n.topic)%COL.length]||'#00d9ff';
      x.beginPath();x.arc(n._x,n._y,n.s,0,Math.PI*2);
      x.fillStyle=col;x.shadowBlur=12;x.shadowColor=col;x.fill();x.shadowBlur=0;});
    requestAnimationFrame(loop);
  }
  loop();
}

const MODULE_META={
  tasks:{title:'任务中心',tag:'Tasks'},
  memory:{title:'记忆中心',tag:'Memory'},
  knowledge:{title:'知识库',tag:'Knowledge'},
  tools:{title:'技能与工具',tag:'Skills'},
  activity:{title:'活动',tag:'Activity'},
  agents:{title:'代理',tag:'Agents'},
  usage:{title:'使用情况',tag:'Usage'},
};
async function fetchModuleBrief(mod){
  try{
    if(mod==='tasks'){
      const out=[];
      for(const a of ['supervisor','finance','office']){
        const r=await fetch(`${API}/thoughtchain/${a}/summary`).then(x=>x.json()).catch(()=>null);
        if(r&&r.total_nodes){ out.push({title:`${AGENT_CN[a]||a} Agent`, sub:`${r.total_nodes} 节点 · ${r.blocked_nodes||0} 拦截`}); }
      }
      return out.length?out:[{title:'暂无任务记录',sub:'发任务后这里显示'}];
    }
    if(mod==='memory'){
      const s=await fetch(`${API}/memory/stats`).then(x=>x.json()).catch(()=>({}));
      const items=[{title:`记忆总数 ${s.total_memories??0} 条`,sub:`私有 ${s.private??0} · 共享 ${s.shared??0}`}];
      const ba=s.by_agent||{};
      Object.entries(ba).sort((a,b)=>b[1]-a[1]).forEach(([a,n])=>items.push({title:AGENT_CN[a]||a,sub:`${n} 条记忆`}));
      return items;
    }
    if(mod==='knowledge'){
      const r=await fetch(`${API}/learning/analysis`).then(x=>x.json()).catch(()=>null);
      if(r&&r.items&&r.items.length) return r.items.map(k=>({title:k.title||k.name||'知识条目',sub:k.tag||k.topic||'',content:k.content,topic:k.topic,source:k.source,tags:k.tags,id:k.id,notes:k.notes}));
      return [{title:'知识库为空',sub:'录入学习数据后显示'}];
    }
    if(mod==='tools'){
      return [{title:'Skill 接入',sub:'助手 Agent 工具扩展'},{title:'插件管理',sub:'开发中'}];
    }
  }catch(e){}
  return [{title:'加载失败',sub:'后端未连接'}];
}

function fillFeed(mod, trackId){
  const track=document.getElementById(trackId); if(!track)return;
  fetchModuleBrief(mod).then(items=>{
    if(!items||!items.length){ track.innerHTML='<div class="feed-empty">暂无数据</div>'; return; }
    const render=()=>items.map((it,idx)=>`<div class="feed-item" data-mod="${mod}" data-idx="${idx}"><b>${escapeHtml(it.title)}</b>${it.sub?`<span>${escapeHtml(it.sub)}</span>`:''}</div>`).join('');
    const enough=items.length>=4;
    track.innerHTML=render()+(enough?render():'');
    track.classList.toggle('rolling', enough);
    if(enough){ track.style.animationDuration=(items.length*2.2)+'s'; }
    else{ track.style.animationDuration=''; }
    track.querySelectorAll('.feed-item').forEach(el=>el.onclick=()=>{
      if(mod==='knowledge'){ const it=items[+el.dataset.idx]; if(it) showKnowledgeArticle(it); }
      else showModuleInCore(mod);
    });
  });
}
function refreshFeeds(){
  fillFeed('tasks','feedTasks');
  fillFeed('memory','feedMemory');
  fillFeed('knowledge','feedKnowledge');
}
function _knowCardHtml(k){
  const notes=(k.notes||[]);
  return `<div class="know-card">
    <div class="know-head"><b>${escapeHtml(k.topic||'知识')}</b>
      <span class="know-tools">${k.source?`<span class="know-src">来源: ${escapeHtml(k.source)}</span>`:''}<span class="know-del" data-id="${escapeHtml(k.id||'')}" title="删除这条知识">🗑</span></span></div>
    <div class="know-body">${escapeHtml(k.content||k.title||'')}</div>
    ${(k.tags&&k.tags.length)?`<div class="know-tags">${k.tags.map(t=>`<span>#${escapeHtml(t)}</span>`).join('')}</div>`:''}
    <div class="know-notes">
      <div class="know-notes-h">📝 我的笔记${notes.length?` (${notes.length})`:''}</div>
      ${notes.length?notes.map(n=>`<div class="know-note"><span>${escapeHtml(n.text||'')}</span><em>${escapeHtml((n.ts||'').slice(0,16).replace('T',' '))}</em></div>`).join(''):'<div class="know-note-empty">还没有笔记,读到有心得就在下面记一条</div>'}
      <div class="know-note-add">
        <textarea class="know-note-input" placeholder="来吧，留下你的学习成果" rows="2"></textarea>
        <button class="know-note-save" data-id="${escapeHtml(k.id||'')}">保存笔记</button>
      </div>
    </div>
  </div>`;
}
function _renderKnowList(body, items, reload){
  body.innerHTML='<div class="know-index">'+items.map((k,i)=>`
    <div class="know-row" data-i="${i}">
      <div class="know-row-tx"><b>${escapeHtml(k.topic||'知识')}</b><span>${escapeHtml((k.content||k.title||'').replace(/\s+/g,' ').trim().slice(0,54))||'(空)'}</span></div>
      <span class="know-del" data-id="${escapeHtml(k.id||'')}" title="删除这条">🗑</span>
    </div>`).join('')+'</div>';
  body.querySelectorAll('.know-row').forEach(el=>el.onclick=(e)=>{ if(e.target.closest('.know-del'))return; _renderKnowDetail(body, items, +el.dataset.i, reload); });
  _wireKnowDelete(body, reload);
}
function _wireKnowNote(body, item, onSaved){
  const saveBtn=body.querySelector('.know-note-save');
  if(!saveBtn)return;
  saveBtn.onclick=async()=>{
    const ta=body.querySelector('.know-note-input'); const text=(ta.value||'').trim();
    if(!text) return;   // 没写就不存(不重复写盘)
    saveBtn.disabled=true;
    try{
      await fetch(`${API}/learning/note`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item_id:item.id, note:text})});
      item.notes=(item.notes||[]).concat([{text, ts:new Date().toISOString()}]);   // 本地追加
      onSaved && onSaved();
    }catch(e){ alert('保存笔记失败:'+e); saveBtn.disabled=false; }
  };
}
function _renderKnowDetail(body, items, i, reload){
  const k=items[i]; if(!k){ _renderKnowList(body, items, reload); return; }
  body.innerHTML=`<div class="know-back" id="knowBack">← 返回列表(共 ${items.length} 篇)</div>`+_knowCardHtml(k);
  document.getElementById('knowBack').onclick=()=>_renderKnowList(body, items, reload);
  _wireKnowDelete(body, reload);
  _wireKnowNote(body, k, ()=>_renderKnowDetail(body, items, i, reload));
}
function showKnowledgeArticle(item){
  const wrap=document.querySelector('.corewrap'), stage=document.getElementById('coreStage');
  if(!wrap||!stage||!item)return;
  document.getElementById('coreChat')?.classList.remove('show');   // 开面板层前关对话浮层(两层互斥)
  Center.panel();
  stage.innerHTML=`<div class="cs-head"><div class="t"><span class="dot"></span>${escapeHtml(item.topic||'知识')} <em>Knowledge</em></div><span class="x" id="csClose" title="关闭">✕</span></div><div class="cs-body" id="csBody"></div>`;
  document.getElementById('csClose').onclick=clearCore;
  const body=document.getElementById('csBody');
  body.innerHTML=_knowCardHtml(item);
  _wireKnowDelete(body, clearCore);                          // 删除后清空中央
  _wireKnowNote(body, item, ()=>showKnowledgeArticle(item)); // 保存笔记后重渲染本篇
}
function _wireKnowDelete(container, onDone){
  container.querySelectorAll('.know-del').forEach(el=>el.onclick=async()=>{
    const id=el.dataset.id; if(!id||!confirm('删除这条知识？此操作不可撤销。'))return;
    try{
      await fetch(`${API}/learning/item/${encodeURIComponent(id)}`,{method:'DELETE'});
      onDone&&onDone(); refreshFeeds();
      try{ loadMemStats(); }catch(_){}
    }catch(e){ alert('删除失败:'+e); }
  });
}
async function showKnowledgeFull(){
  const wrap=document.querySelector('.corewrap'), stage=document.getElementById('coreStage');
  if(!wrap||!stage)return;
  document.getElementById('coreChat')?.classList.remove('show');   // 开面板前关对话浮层(否则被 z5 盖住)
  Center.panel();
  _syncNav('knowledge');
  stage.innerHTML=`<div class="cs-head"><div class="t"><span class="dot"></span>知识库 <em>Knowledge</em></div><span class="x" id="csClose" title="返回">✕</span></div><div class="cs-body" id="csBody"><div class="kp-empty" style="padding:30px 0">加载中…</div></div>`;
  document.getElementById('csClose').onclick=clearCore;
  const body=document.getElementById('csBody');
  try{
    const r=await fetch(`${API}/learning/analysis`).then(x=>x.json());
    const items=(r.items||[]);
    if(!items.length){ body.innerHTML='<div class="kp-empty" style="padding:30px 0">还没有录入知识。点顶部「录入知识点」添加。</div>'; return; }
    _renderKnowList(body, items, showKnowledgeFull);
  }catch(e){ body.innerHTML='<div class="kp-empty" style="padding:30px 0">后端未连接</div>'; }
}
const _navMenu=document.getElementById('navMenu');
const _navMenuBtn=document.getElementById('navMenuBtn');
function setNavMenu(open){
  if(!_navMenu||!_navMenuBtn)return;
  const show=open===undefined?!_navMenu.classList.contains('show'):open;
  if(show){
    const r=_navMenuBtn.getBoundingClientRect();
    _navMenu.style.left=Math.round(r.right+10)+'px';
    _navMenu.style.bottom=Math.round(Math.max(10,window.innerHeight-r.bottom))+'px';
    _navMenu.classList.add('show'); _navMenu.setAttribute('aria-hidden','false');
    _navMenuBtn.classList.add('open'); _navMenuBtn.setAttribute('aria-expanded','true');
  }else{
    _navMenu.classList.remove('show'); _navMenu.setAttribute('aria-hidden','true');
    _navMenuBtn.classList.remove('open'); _navMenuBtn.setAttribute('aria-expanded','false');
  }
}
_navMenuBtn?.addEventListener('click',e=>{ e.stopPropagation(); setNavMenu(); });
document.getElementById('navMenuClose')?.addEventListener('click',()=>setNavMenu(false));
document.addEventListener('click',e=>{
  if(!_navMenu||!_navMenu.classList.contains('show'))return;
  if(_navMenu.contains(e.target)||_navMenuBtn.contains(e.target))return;
  setNavMenu(false);
});
document.addEventListener('keydown',e=>{ if(e.key==='Escape') setNavMenu(false); });
window.addEventListener('resize',()=>setNavMenu(false));

function _syncNav(mod){
  let name='';
  document.querySelectorAll('#navList .nav-link').forEach(x=>{
    const hit=x.dataset.module===mod;
    x.classList.toggle('on',hit);
    if(hit) name=x.querySelector('b')?.textContent||'';
  });
  const lbl=document.getElementById('navMenuLabel');
  if(lbl) lbl.textContent=(name&&mod!=='overview')?`菜单 · ${name}`:'菜单';
}

document.querySelectorAll('#navList .nav-link').forEach(el=>{
  el.addEventListener('click',()=>{
    const mod=el.dataset.module;
    _syncNav(mod);
    setNavMenu(false);                                   // 选完就收起菜单,把屏幕还给内容
    if(mod==='overview') clearCore();
    else if(mod==='knowledge') showKnowledgeFull();
    else showModuleInCore(mod);
  });
});

async function showModuleInCore(mod){
  const meta=MODULE_META[mod]||{title:mod,tag:''};
  const wrap=document.querySelector('.corewrap'), stage=document.getElementById('coreStage');
  document.getElementById('coreChat')?.classList.remove('show');   // 开面板前关对话浮层(否则被 z5 盖住)
  Center.panel();
  _syncNav(mod);
  stage.innerHTML=`<div class="cs-head"><div class="t"><span class="dot"></span>${meta.title} <em>${meta.tag}</em></div>
    <span class="x" id="csClose" title="返回核心">✕</span></div>
    <div class="cs-body" id="csBody"><div class="kp-empty" style="padding:30px 0">加载中…</div></div>`;
  document.getElementById('csClose').onclick=clearCore;
  const body=document.getElementById('csBody');
  try{
    body.innerHTML=await renderModuleBig(mod);
    if(mod==='tools') wireToolCenter(body);
    if(mod==='memory') wirePrefDelete(body);
  }catch(e){ body.innerHTML='<div class="kp-empty">后端未连接</div>'; }
}
function wirePrefDelete(body){
  body.querySelectorAll('.pref-del').forEach(btn=>{
    btn.onclick=async()=>{
      const id=btn.dataset.prefId; if(!id)return;
      btn.disabled=true; btn.textContent='…';
      try{ await fetch(`${API}/preferences/${encodeURIComponent(id)}`,{method:'DELETE'}); }catch(e){}
      showModuleInCore('memory');   // 刷新列表
    };
  });
}
async function renderModuleBig(mod){
  if(mod==='tasks'){
    let h='<div class="big-grid">';
    for(const a of ['supervisor','finance','office']){
      const r=await fetch(`${API}/thoughtchain/${a}/summary`).then(x=>x.json()).catch(()=>null);
      h+=`<div class="big-card"><div class="bc-head">${AGENT_CN[a]||a} Agent</div>
        <div class="bc-row"><span>推理节点</span><b>${r?.total_nodes??0}</b></div>
        <div class="bc-row"><span>被拦截</span><b style="color:var(--amber)">${r?.blocked_nodes??0}</b></div>
        <div class="bc-row"><span>拦截率</span><b>${r?.block_rate!=null?(r.block_rate*100).toFixed(1)+'%':'—'}</b></div>
        <div class="bc-row"><span>平均置信</span><b>${r?.avg_confidence!=null?r.avg_confidence.toFixed(2):'—'}</b></div></div>`;
    }
    h+='</div>';
    return h;
  }
  if(mod==='activity'){
    const d=await fetch(`${API}/bus/history?limit=40`).then(x=>x.json()).catch(()=>({}));
    const evs=(d.events||[]);
    if(!evs.length) return '<div class="kp-empty" style="padding:40px">暂无活动记录 · 系统事件(执行→审计→记忆)会实时汇入这里</div>';
    return '<div class="big-list">'+evs.slice().reverse().map(e=>{
      const topic=e.topic||e.channel||'事件';
      const ts=Number(e.ts);
      const when=(isFinite(ts)&&ts>0)?new Date(ts*1000).toLocaleString('zh-CN',{hour12:false}):'';
      const p=(e.payload&&typeof e.payload==='object')?e.payload:{};
      const bits=Object.entries(p).slice(0,4)
        .map(([k,v])=>`${k}=${(v&&typeof v==='object')?JSON.stringify(v):v}`).join(' · ');
      const meta=[when, e.source].filter(Boolean).join(' · ');
      return `<div class="big-li"><b>${escapeHtml(String(topic))}</b><span>${escapeHtml(meta)}${bits?' · '+escapeHtml(bits.slice(0,110)):''}</span></div>`;
    }).join('')+'</div>';
  }
  if(mod==='agents'){
    const AG=[['supervisor','主管','全局统筹·任务分派'],
      ['finance','财务','收支·预算·理财'],['office','办公学习','学习·知识·写作·日程行程']];
    const stats=await fetch(`${API}/memory/stats`).then(x=>x.json()).catch(()=>({}));
    const ba=stats.by_agent||{};
    let h='<div class="big-grid">';
    for(const [id,cn,role] of AG){
      const tc=await fetch(`${API}/thoughtchain/${id}/summary`).then(x=>x.json()).catch(()=>null);
      h+=`<div class="big-card"><div class="bc-head">${cn} Agent</div>
        <div style="font-size:11px;color:var(--dim);margin:-4px 0 8px">${role}</div>
        <div class="bc-row"><span>记忆</span><b>${ba[id]??0} 条</b></div>
        <div class="bc-row"><span>推理节点</span><b>${tc?.total_nodes??0}</b></div>
        <div class="bc-row"><span>被拦截</span><b style="color:var(--amber)">${tc?.blocked_nodes??0}</b></div></div>`;
    }
    return h+'</div>';
  }
  if(mod==='usage'){
    const m=await fetch(`${API}/metrics`).then(x=>x.json()).catch(()=>({}));
    const tr=await fetch(`${API}/tokens/report`).then(x=>x.json()).catch(()=>({}));
    const llm=(m.llm||m.providers||{});
    const llmRows=Object.entries(llm).map(([k,v])=>{
      const calls=(v&&typeof v==='object')?(v.calls??v.count??v.total??v.ok??'—'):v;
      return `<div class="bc-row"><span>${escapeHtml(k)}</span><b>${escapeHtml(String(calls))}</b></div>`;
    }).join('');
    const up=m.uptime??m.uptime_seconds;
    const report=String(tr.report||tr.error||'token meter 未启用或暂无数据');
    return `<div class="big-grid">
      <div class="big-card"><div class="bc-head">运行</div>
        <div class="bc-row"><span>uptime</span><b>${up!=null?Math.round(up)+' s':'—'}</b></div>
        <div class="bc-row"><span>LLM provider 数</span><b>${Object.keys(llm).length}</b></div></div>
      <div class="big-card"><div class="bc-head">各 provider 调用</div>${llmRows||'<div class="kp-empty" style="padding:8px">暂无调用</div>'}</div>
      <div class="big-card" style="grid-column:1/-1"><div class="bc-head">Token 消耗报告</div>
        <pre style="white-space:pre-wrap;font-size:11px;color:var(--dim);line-height:1.55;margin:0;font-family:var(--font)">${escapeHtml(report)}</pre></div>
    </div>`;
  }
  if(mod==='memory'){
    const s=await fetch(`${API}/memory/stats`).then(x=>x.json()).catch(()=>({}));
    const pf=await fetch(`${API}/preferences`).then(x=>x.json()).catch(()=>({preferences:[]}));
    const ba=s.by_agent||{};
    let rows=Object.entries(ba).sort((a,b)=>b[1]-a[1]).map(([a,n])=>`<div class="bc-row"><span>${AGENT_CN[a]||a}</span><b>${n} 条</b></div>`).join('')||'<div class="kp-empty">暂无分层记忆</div>';
    const prefs=(pf.preferences||[]);
    const prefRows=prefs.length
      ? prefs.map(p=>`<div class="bc-row" style="align-items:flex-start;gap:6px">
          <span style="flex:1;font-size:11px;line-height:1.4">${escapeHtml((p.content||'').replace(/^用户偏好[:：]/,''))}</span>
          <button class="pref-del" data-pref-id="${escapeHtml(p.id||'')}" title="删除这条偏好" style="background:none;border:0;color:var(--amber);cursor:pointer;font-size:13px;padding:0 2px">✕</button></div>`).join('')
      : '<div class="kp-empty">暂无记住的偏好(说"我喜欢…/以后…"会被记住)</div>';
    return `<div class="big-grid">
      <div class="big-card"><div class="bc-head">记忆总览</div>
        <div class="bc-row"><span>总条数</span><b>${s.total_memories??0}</b></div>
        <div class="bc-row"><span>私有层</span><b>${s.private??0}</b></div>
        <div class="bc-row"><span>共享层</span><b>${s.shared??0}</b></div>
        <div class="bc-row"><span>知识关系</span><b>${s.relations??0}</b></div></div>
      <div class="big-card"><div class="bc-head">按 Agent 分布</div>${rows}</div>
      <div class="big-card"><div class="bc-head">后端</div>
        <div class="bc-row"><span>backend</span><b>${s.backend||'—'}</b></div>
        <div class="bc-row"><span>Embedding</span><b style="font-size:11px">${s.embedding||'—'}</b></div></div>
      <div class="big-card"><div class="bc-head">用户偏好 <em style="font-size:10px;color:var(--ink-dim);font-style:normal">点 ✕ 删除误记</em></div>${prefRows}</div>
    </div>`;
  }
  if(mod==='knowledge'){
    const r=await fetch(`${API}/learning/analysis`).then(x=>x.json()).catch(()=>null);
    if(r&&r.items&&r.items.length){
      return '<div class="big-list">'+r.items.map(k=>`<div class="big-li"><b>${k.title||k.name||'条目'}</b><span>${k.category||k.tag||''}</span></div>`).join('')+'</div>';
    }
    return '<div class="kp-empty" style="padding:40px">知识库为空 · 通过办公 Agent 录入学习数据后显示</div>';
  }
  if(mod==='tools'){
    let skills=[], breakers={}, audit=[];
    try{ skills=(await fetch(`${API}/ahs/skills`).then(x=>x.json())).skills||[]; }catch(e){}
    try{ breakers=(await fetch(`${API}/ahs/breaker`).then(x=>x.json())).breakers||{}; }catch(e){}
    try{ audit=(await fetch(`${API}/ahs/hooks/audit`).then(x=>x.json())).audit||[]; }catch(e){}
    let servers=[];
    try{ servers=(await fetch(`${API}/ahs/mcp/servers`).then(x=>x.json())).servers||[]; }catch(e){}
    let mdskills=[];
    try{ mdskills=(await fetch(`${API}/ahs/md-skills`).then(x=>x.json())).skills||[]; }catch(e){}
    const mdRows=mdskills.map(s=>`<div class="md-row${s.blocked?' tc-blocked':''}" data-id="${escapeHtml(s.id)}" data-triggers="${escapeHtml((s.triggers||[]).join(', '))}">
        <div class="md-h"><b>${escapeHtml(s.name)}</b>${s.version?` <span class="tc-kind">v${escapeHtml(s.version)}</span>`:''} <span class="tc-trust" style="background:#b0413e" title="信任分级">low</span> <span class="tc-src user">${s.enabled?'启用':'停用'}</span>${s.blocked?' <span class="tc-blk-tag">已封杀</span>':''}</div>
        <div class="md-desc">${escapeHtml(s.description||'')} · 触发: ${escapeHtml((s.triggers||[]).join(', ')||'无(靠描述相关注入)')}</div>
        <div class="md-acts"><button class="md-view">查看</button>
          <button class="md-trig">编辑触发词</button>
          <button class="md-toggle">${s.enabled?'停用':'启用'}</button>
          <button class="md-deny">${s.blocked?'解封':'封杀'}</button>
          <button class="md-del">删除</button></div></div>`).join('')||'<div class="kp-empty" style="padding:10px">未安装指令技能</div>';
    const srvRows=servers.map(s=>`<div class="mcp-row" data-id="${escapeHtml(s.id)}">
        <div><b>${escapeHtml(s.name||s.id)}</b> <span class="tc-kind">${escapeHtml(s.transport)}</span> <span class="mcp-tc">${s.tool_count} 工具</span></div>
        <div class="mcp-url">${escapeHtml(s.url)}</div>
        <button class="mcp-del">移除</button></div>`).join('')||'<div class="kp-empty" style="padding:10px">未接入 MCP 服务器</div>';
    const seColor={read:'#2c9678',write:'#d9822b',send:'#0066CC',pay:'#b0413e'};
    const trustColor={high:'#2c9678',mid:'#d9822b',low:'#b0413e'};
    const brDot=st=>st&&st.state==='open'?'#b0413e':st&&st.state==='half_open'?'#d9822b':'#2c9678';
    const rows=skills.map(s=>`<div class="tc-row${s.blocked?' tc-blocked':''}" data-id="${escapeHtml(s.id)}">
        <div class="tc-main"><b>${escapeHtml(s.name||s.id)}</b>
          <span class="tc-badge" style="background:${seColor[s.side_effect]||'#888'}">${escapeHtml(s.side_effect)}</span>
          <span class="tc-trust" style="background:${trustColor[s.trust]||'#888'}" title="信任分级">${escapeHtml(s.trust||'')}</span>
          <span class="tc-kind">${escapeHtml(s.kind)}</span>
          <span class="tc-src ${s.source}">${s.source==='user'?'已安装':'内置'}</span>
          <span class="tc-brk" title="断路器" style="background:${brDot(breakers[s.id])}"></span>
          ${s.blocked?'<span class="tc-blk-tag">已封杀</span>':''}</div>
        <div class="tc-desc">${escapeHtml(s.description||'')} · 触发: ${escapeHtml((s.triggers||[]).join(', ')||'—')}</div>
        <div class="tc-acts">
          <label class="tc-sw"><input type="checkbox" class="tc-en" ${s.enabled?'checked':''}><span>启用</span></label>
          <button class="tc-test">测试</button>
          <button class="tc-deny">${s.blocked?'解封':'封杀'}</button>
          ${s.source==='user'?'<button class="tc-del">删除</button>':''}
        </div></div>`).join('')||'<div class="kp-empty">还没有技能</div>';
    const auditRows=audit.slice(0,8).map(a=>`<div class="tc-audit"><span>${escapeHtml(a.skill_id||a.skill||'—')}</span><em>${escapeHtml(String(a.result||a.status||''))}</em></div>`).join('')||'<div class="kp-empty" style="padding:12px">暂无调用</div>';
    return `<div class="tc-wrap">
      <div class="tc-col">
        <div class="tc-h">连接器 · MCP <span>${servers.length}</span></div>
        <div class="mcp-list">${srvRows}</div>
        <div class="mcp-form">
          <input id="mcf-id" placeholder="服务器 id(如 my_mcp)">
          <input id="mcf-name" placeholder="名称">
          <input id="mcf-url" placeholder="URL https://.../mcp">
          <input id="mcf-token" placeholder="Authorization 令牌(可选,明文存本地)">
          <select id="mcf-transport"><option value="streamable_http">streamable_http</option><option value="sse">sse</option></select>
          <button id="mcf-submit">接入</button>
        </div>
        <div class="tc-h" style="margin-top:14px">技能 <span>${skills.length}</span></div>
        <div class="tc-list">${rows}</div>
        <div class="tc-h" style="margin-top:14px">指令技能 · SKILL.md <span>${mdskills.length}</span></div>
        <div class="md-list">${mdRows}</div>
        <div class="md-form">
          <input type="file" id="mdf-file" accept=".md,.markdown,.zip,text/markdown">
          <button id="mdf-submit">上传安装</button>
        </div>
        <div class="tc-h" style="margin-top:14px">安装技能</div>
        <div class="tc-form">
          <input id="tcf-id" placeholder="唯一 id(如 my_weather)">
          <input id="tcf-name" placeholder="名称">
          <select id="tcf-kind"><option value="web_api">web_api(HTTP 接口)</option><option value="mcp">mcp(MCP 工具)</option></select>
          <input id="tcf-triggers" placeholder="触发词(逗号分隔)">
          <select id="tcf-se"><option value="read">read(只读)</option><option value="write">write</option><option value="send">send</option><option value="pay">pay</option></select>
          <input id="tcf-endpoint" placeholder="web_api: https://...  |  mcp: server 名">
          <input id="tcf-tool" placeholder="mcp: tool 名(web_api 留空)">
          <button id="tcf-submit">安装</button>
        </div>
      </div>
      <div class="tc-col tc-gov">
        <div class="tc-h">治理 · 最近调用</div>
        <div class="tc-auditlist">${auditRows}</div>
      </div>
    </div>`;
  }
  return '<div class="kp-empty">未知模块</div>';
}
async function wireToolCenter(root){
  const reload=()=>showModuleInCore('tools');
  root.querySelectorAll('.tc-row').forEach(row=>{
    const id=row.dataset.id;
    const en=row.querySelector('.tc-en');
    if(en) en.onchange=async()=>{ await fetch(`${API}/ahs/skills/${encodeURIComponent(id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:en.checked})}); reload(); };
    const del=row.querySelector('.tc-del');
    if(del) del.onclick=async()=>{ if(!confirm('删除这个技能？'))return; await fetch(`${API}/ahs/skills/${encodeURIComponent(id)}`,{method:'DELETE'}); reload(); };
    const deny=row.querySelector('.tc-deny');
    if(deny) deny.onclick=async()=>{ const on=deny.textContent.trim()==='封杀';
      if(on&&!confirm('封杀该技能？deny 会压倒启用、立即对所有执行/注入生效。'))return;
      if(on) await fetch(`${API}/ahs/policy/deny`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
      else await fetch(`${API}/ahs/policy/deny/${encodeURIComponent(id)}`,{method:'DELETE'});
      reload(); };
    const test=row.querySelector('.tc-test');
    if(test) test.onclick=async()=>{
      let args={}; const raw=prompt('测试参数(JSON,可留空)','{}');
      if(raw===null)return;
      try{ args=raw.trim()?JSON.parse(raw):{}; }catch(e){ alert('参数不是合法 JSON'); return; }
      let r=await fetch(`${API}/ahs/skills/${encodeURIComponent(id)}/test`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({args})}).then(x=>x.json());
      if(r.status==='needs_confirmation'){
        if(!confirm('此操作有副作用，确认执行？'))return;
        r=await fetch(`${API}/ahs/skills/${encodeURIComponent(id)}/test`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({args,confirmed:true})}).then(x=>x.json());
      }
      alert((r.success?'✓ 成功':'✗ 失败')+' · '+(r.message||JSON.stringify(r.output||{}).slice(0,200)));
    };
  });
  root.querySelectorAll('.mcp-row').forEach(row=>{
    const del=row.querySelector('.mcp-del');
    if(del) del.onclick=async()=>{ if(!confirm('移除该 MCP 服务器及其全部工具技能？'))return;
      await fetch(`${API}/ahs/mcp/servers/${encodeURIComponent(row.dataset.id)}`,{method:'DELETE'}); reload(); };
  });
  const msub=root.querySelector('#mcf-submit');
  if(msub) msub.onclick=async()=>{
    const g=s=>(root.querySelector('#mcf-'+s)||{}).value||'';
    const id=g('id').trim(), url=g('url').trim();
    if(!id||!url){ alert('id 和 URL 必填'); return; }
    const headers={}; const tok=g('token').trim(); if(tok) headers['Authorization']=tok;
    const body={id,name:g('name').trim()||id,url,headers,transport:g('transport')};
    const doPost=b=>fetch(`${API}/ahs/mcp/servers`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
    let r=await doPost(body);
    if(r.status===403){ const e=await r.json().catch(()=>({})); alert('❌ 接入被审核拦截:\n'+((e.detail&&e.detail.reasons)||['被拒绝']).join('\n')); return; }
    if(r.status===409){ const e=await r.json().catch(()=>({})); const w=(e.detail&&e.detail.warnings)||[];
      if(!confirm('⚠ 接入审核提示风险:\n'+w.join('\n')+'\n\n确认仍要接入？')) return;
      r=await doPost({...body, acknowledged:true});
    }
    if(!r.ok){ const e=await r.json().catch(()=>({})); alert('接入失败: '+(JSON.stringify(e&&e.detail)||r.status)); return; }
    const d=await r.json(); alert(`✓ 已接入,注册 ${d.tools_registered} 个工具技能`); reload();
  };
  root.querySelectorAll('.md-row').forEach(row=>{
    const id=row.dataset.id;
    const v=row.querySelector('.md-view');
    if(v) v.onclick=async()=>{ const d=await fetch(`${API}/ahs/md-skills/${encodeURIComponent(id)}`).then(x=>x.json());
      alert(`【${d.name}】\n${d.description}\n\n触发: ${(d.triggers||[]).join(', ')||'无'}\n\n${(d.body||'').slice(0,1500)}`); };
    const tg=row.querySelector('.md-toggle');
    if(tg) tg.onclick=async()=>{ const on=tg.textContent.trim()==='启用';
      await fetch(`${API}/ahs/md-skills/${encodeURIComponent(id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:on})}); reload(); };
    const tr=row.querySelector('.md-trig');
    if(tr) tr.onclick=async()=>{
      const cur=row.dataset.triggers||'';
      const val=prompt('触发词(逗号分隔;留空=靠英文描述相关匹配)。\n中文任务想稳定触发就在这加中文词。', cur);
      if(val===null)return;
      const triggers=val.split(',').map(x=>x.trim()).filter(Boolean);
      await fetch(`${API}/ahs/md-skills/${encodeURIComponent(id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({triggers})}); reload(); };
    const dy=row.querySelector('.md-deny');
    if(dy) dy.onclick=async()=>{ const on=dy.textContent.trim()==='封杀';
      if(on&&!confirm('封杀该指令技能？封杀后不再注入任何 Agent 提示词。'))return;
      if(on) await fetch(`${API}/ahs/policy/deny`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
      else await fetch(`${API}/ahs/policy/deny/${encodeURIComponent(id)}`,{method:'DELETE'});
      reload(); };
    const dl=row.querySelector('.md-del');
    if(dl) dl.onclick=async()=>{ if(!confirm('删除该指令技能？'))return;
      await fetch(`${API}/ahs/md-skills/${encodeURIComponent(id)}`,{method:'DELETE'}); reload(); };
  });
  const mdsub=root.querySelector('#mdf-submit');
  if(mdsub) mdsub.onclick=async()=>{
    const f=(root.querySelector('#mdf-file')||{}).files?.[0];
    if(!f){ alert('请先选择 .md 或 OpenClaw .zip 包'); return; }
    let body;
    if(/\.zip$/i.test(f.name)){
      const b64=await new Promise((res,rej)=>{const fr=new FileReader();fr.onload=()=>res(String(fr.result).split(',')[1]);fr.onerror=rej;fr.readAsDataURL(f);});
      body={package_b64:b64};
    }else{
      const content=await f.text();
      const id=f.name.replace(/\.(md|markdown)$/i,'').replace(/[^a-z0-9]+/gi,'_').toLowerCase();
      body={content,id};
    }
    const doPost=b=>fetch(`${API}/ahs/md-skills`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
    let r=await doPost(body);
    if(r.status===400){ const e=await r.json().catch(()=>({})); alert('解析失败: '+(e.detail||'SKILL.md/包 格式错')); return; }
    if(r.status===409){ const e=await r.json().catch(()=>({})); const w=(e.detail&&e.detail.warnings)||[];
      if(!confirm('⚠ 安装审核提示:\n'+w.join('\n')+'\n\n确认安装？')) return;
      r=await doPost({...body,acknowledged:true});
    }
    if(!r.ok){ const e=await r.json().catch(()=>({})); alert('安装失败: '+(JSON.stringify(e&&e.detail)||r.status)); return; }
    alert('✓ 指令技能已安装'); reload();
  };
  const sub=root.querySelector('#tcf-submit');
  if(sub) sub.onclick=async()=>{
    const g=s=>(root.querySelector('#tcf-'+s)||{}).value||'';
    const kind=g('kind');
    const body={id:g('id').trim(),name:g('name').trim()||g('id').trim(),description:'',kind,
      triggers:g('triggers').split(',').map(s=>s.trim()).filter(Boolean),side_effect:g('se')};
    if(!body.id){ alert('id 必填'); return; }
    if(kind==='web_api'){ body.endpoint=g('endpoint').trim(); }
    else{ body.mcp_server=g('endpoint').trim(); body.mcp_tool=g('tool').trim(); }
    const doPost=b=>fetch(`${API}/ahs/skills`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
    let r=await doPost(body);
    if(r.status===403){ const e=await r.json().catch(()=>({})); alert('❌ 安装被审核拦截:\n'+((e.detail&&e.detail.reasons)||['被拒绝']).join('\n')); return; }
    if(r.status===409){ const e=await r.json().catch(()=>({})); const w=(e.detail&&e.detail.warnings)||[];
      if(!confirm('⚠ 安装审核提示风险:\n'+w.join('\n')+'\n\n确认仍要安装？')) return;
      r=await doPost({...body, acknowledged:true});
    }
    if(!r.ok){ const e=await r.json().catch(()=>({})); alert('安装失败: '+(JSON.stringify(e.detail)||r.status)); return; }
    reload();
  };
}

const ENTRY_FORMS={
  finance:{title:'录入交易',sub:'添加一笔收支记录',endpoint:'/finance/import',
    fields:[
      {k:'amount',label:'金额(支出填负数)',type:'number',ph:'-29.99'},
      {k:'category',label:'分类',type:'text',ph:'food / subscription / transport'},
      {k:'entity',label:'商户/对象',type:'text',ph:'星巴克'},
      {k:'account',label:'账户',type:'text',ph:'credit_card_1'},
    ],
    build:v=>({rows:[{amount:parseFloat(v.amount),category:v.category||'uncategorized',
      entity:v.entity||'unknown',account:v.account||'default'}]})},
  office:{title:'录入知识点',sub:'添加一条学习知识',endpoint:'/learning/import',
    fields:[
      {k:'content',label:'知识内容(支持多行,可整段粘贴)',type:'textarea',ph:'粘贴完整知识/教程,支持多行\n如:\n第一步 …\n第二步 …'},
      {k:'topic',label:'主题',type:'text',ph:'学习方法'},
      {k:'source',label:'来源',type:'text',ph:'《学习之道》'},
      {k:'tags',label:'标签(多个用 | 分隔)',type:'text',ph:'例：安装 | 教程 | 工具'},
    ],
    build:v=>({rows:[{content:v.content,topic:v.topic||'general',source:v.source||'',
      tags:(v.tags||'').split('|').map(s=>s.trim()).filter(Boolean)}]})},
  admin:{title:'录入行程点',sub:'添加一个行程地点',endpoint:'/itinerary/import',
    fields:[
      {k:'name',label:'地点名',type:'text',ph:'客户A会议'},
      {k:'address',label:'地址(配高德Key可自动定位)',type:'text',ph:'北京市朝阳区三里屯'},
      {k:'lng',label:'经度',type:'number',ph:'116.455'},
      {k:'lat',label:'纬度',type:'number',ph:'39.936'},
      {k:'start_time',label:'到达时间',type:'text',ph:'2026-06-10T10:00:00'},
      {k:'duration_min',label:'停留(分钟)',type:'number',ph:'60'},
    ],
    build:v=>({rows:[{name:v.name,address:v.address||'',
      lng:v.lng?parseFloat(v.lng):null,lat:v.lat?parseFloat(v.lat):null,
      start_time:v.start_time||null,duration_min:parseInt(v.duration_min||0),city:''}]})},
};
let _entryKind=null;
function openEntry(kind){
  const f=ENTRY_FORMS[kind]; if(!f)return; _entryKind=kind;
  document.getElementById('entryTitle').textContent=f.title;
  document.getElementById('entrySub').textContent=f.sub;
  document.getElementById('entryMsg').textContent='';
  let h='';
  f.fields.forEach(fd=>{
    const ctl = fd.type==='textarea'
      ? `<textarea data-k="${fd.k}" placeholder="${fd.ph||''}" rows="8" style="width:100%;resize:vertical;font-family:inherit;line-height:1.6"></textarea>`
      : `<input data-k="${fd.k}" type="${fd.type}" placeholder="${fd.ph||''}">`;
    h+=`<div class="entry-field"><label>${fd.label}</label>${ctl}</div>`;
  });
  document.getElementById('entryFields').innerHTML=h;
  document.getElementById('entryMask').classList.add('show');
}
function closeEntry(){document.getElementById('entryMask').classList.remove('show');}
document.getElementById('entryCancel')?.addEventListener('click',closeEntry);
document.getElementById('entryMask')?.addEventListener('click',e=>{if(e.target.id==='entryMask')closeEntry();});
document.getElementById('entrySubmit')?.addEventListener('click',async()=>{
  const f=ENTRY_FORMS[_entryKind]; if(!f)return;
  const v={}; document.querySelectorAll('#entryFields input, #entryFields textarea').forEach(i=>v[i.dataset.k]=i.value);
  const msg=document.getElementById('entryMsg');
  try{
    const body=f.build(v);
    const r=await fetch(`${API}${f.endpoint}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    msg.style.color='var(--green)'; msg.textContent=`✓ 已录入 ${d.imported||0} 条`;
    setTimeout(()=>{closeEntry(); loadKernel(activeAgent); loadMemStats();},700);
  }catch(e){ msg.style.color='var(--amber)'; msg.textContent='录入失败:后端未连接'; }
});

const _bootTry=(fn,name)=>{ try{ const r=fn(); if(r&&r.catch) r.catch(e=>{ try{document.getElementById('upStat').textContent='启动失败:'+name}catch(_){ } }); }
                       catch(e){ try{document.getElementById('upStat').textContent='启动失败:'+name}catch(_){ } } };
_bootTry(fetchAMapConfig, 'fetchAMapConfig');
_bootTry(loadStatus, 'loadStatus');
_bootTry(loadMemStats, 'loadMemStats');
_bootTry(loadKnowList, 'loadKnowList');
_bootTry(()=>connectWS(), 'connectWS');
_bootTry(()=>loadKernel('supervisor'), 'loadKernel');
try{ setInterval(loadStatus,15000); }catch(e){}
try{ setInterval(loadMemStats,20000); }catch(e){}
try{ setInterval(loadKnowList,30000); }catch(e){}
document.getElementById('memMore')?.addEventListener('click',()=>showModuleInCore('memory'));
document.getElementById('knowMore')?.addEventListener('click',()=>showKnowledgeFull());
document.getElementById('ingFile')?.addEventListener('click',()=>document.getElementById('cmdAttach')?.click());
document.getElementById('ingFolder')?.addEventListener('click',()=>document.getElementById('cmdFolder')?.click());
document.getElementById('ingNote')?.addEventListener('click',()=>{ try{ openEntry('office'); }catch(e){} });
_bootTry(loadSessions, 'loadSessions');
document.getElementById('sessNew')?.addEventListener('click',()=>newSession(true));

Object.assign(window, { openEntry, importDomainFile, switchProvider });

