import { escapeHtml } from './util.js?v=2.9';
import { API, _lastKernelData } from './state.js';

export function kpSet(title,tag,html){
  const t=document.getElementById('kpTitle'); if(t)t.textContent=title;
  const g=document.getElementById('kpTag');   if(g)g.textContent=tag;
  const b=document.getElementById('kpBody');  if(b)b.innerHTML=html;
}

export function renderFinance(d){
  let h='<div class="kp-add" onclick="openEntry(\'finance\')">＋ 录入交易</div>'
       +'<div class="kp-add" onclick="importDomainFile(\'finance\')">📁 导入文件</div>';
  const bs=d.budget_status||[];
  if(bs.length){h+='<div class="kp-sec"><div class="h">预算状态</div>';
    bs.forEach(b=>{const pct=Math.min(b.usage_ratio*100,120);
      const col=b.is_over?'var(--amber)':'var(--green)';
      h+=`<div class="kp-bud"><div class="top"><b>${escapeHtml(b.category)}</b><span class="${b.is_over?'over':''}">${b.spent.toFixed(0)}/${b.monthly_limit.toFixed(0)}</span></div>
      <div class="bar"><i style="width:${pct}%;background:${col}"></i></div></div>`;});
    h+='</div>';}
  const an=d.anomalies||[];
  if(an.length){h+='<div class="kp-sec"><div class="h">异常消费</div>';
    an.forEach(a=>{h+=`<div class="kp-chip"><span class="ico">⚠</span><div class="tx"><b>${escapeHtml(a.entity)} ${escapeHtml(a.amount)}</b><span>${escapeHtml(a.reason)}</span></div></div>`;});
    h+='</div>';}
  const sc=d.subscription_creep||{};
  if(sc.subscription_count){h+=`<div class="kp-sec"><div class="h">订阅</div><div class="kp-metric"><span class="lab">订阅数量</span><span class="val">${sc.subscription_count}</span></div><div class="kp-metric"><span class="lab">月度总额</span><span class="val">${sc.monthly_total}</span></div></div>`;}
  const gv=d.gatekeeper_verdicts||[];
  if(gv.length){h+='<div class="kp-sec"><div class="h">守门人裁决</div>';
    gv.forEach(v=>{const cls=v.severity==='critical'?'crit':v.severity==='warn'?'warn':'info';
      h+=`<div class="kp-chip ${cls}"><span class="ico">⬢</span><div class="tx"><b>${v.action}</b><span>${v.message}</span></div></div>`;});
    h+='</div>';}
  return h||'<div class="kp-empty">暂无财务数据 · 先导入交易</div>';
}

export function renderLearning(d){
  let h='<div class="kp-add" onclick="openEntry(\'office\')">＋ 写入知识点</div>'
       +'<div class="kp-add" onclick="document.getElementById(\'cmdAttach\')?.click()">📄 导入文件</div>'
       +'<div class="kp-add" onclick="document.getElementById(\'cmdFolder\')?.click()">📁 导入文件夹</div>';
  h+='<div class="kp-tiprow">导入的文档去哪了?<span class="infotip" tabindex="0">i<span class="infotip-pop">📡「导入文件/文件夹」的文档存入<b>知识星球(HKC)</b>并抽取成知识;本页显示的是<b>学习知识点/掌握度</b>。<span class="infotip-link" onclick="document.getElementById(\'planetLaunch\')?.click()">→ 去星球查看已摄入的知识</span></span></span></div>';
  h+=`<div class="kp-sec"><div class="h">学习概况</div>
    <div class="kp-metric"><span class="lab">知识点</span><span class="val">${d.total_items||0}</span></div>
    <div class="kp-metric"><span class="lab">平均掌握度</span><span class="val">${((d.avg_mastery||0)*100).toFixed(0)}%</span></div>
    <div class="kp-metric"><span class="lab">今日待复习</span><span class="val">${d.due_today||0}</span></div>
    <div class="kp-metric"><span class="lab">知识簇</span><span class="val">${d.graph?.clusters||0}</span></div></div>`;
  const tm=d.topic_mastery||{};
  if(Object.keys(tm).length){h+='<div class="kp-sec"><div class="h">主题掌握度</div>';
    Object.entries(tm).forEach(([t,m])=>{h+=`<div class="kp-row" style="gap:10px"><span style="width:64px;color:var(--ink-dim)">${t}</span><div class="mastery-bar"><i style="width:${m*100}%"></i></div><span style="font-family:Orbitron;font-size:10px;width:32px;text-align:right">${(m*100).toFixed(0)}%</span></div>`;});
    h+='</div>';}
  const fr=d.forgetting_risk||[];
  if(fr.length){h+=`<div class="kp-sec"><div class="h">遗忘风险</div><div class="kp-chip warn"><span class="ico">◷</span><div class="tx"><b>${fr.length} 个知识点高风险</b><span>建议优先复习</span></div></div></div>`;}
  return h;
}

export function renderItinerary(d){
  let h='<div class="kp-add" onclick="openEntry(\'admin\')">＋ 录入行程</div>'
       +'<div class="kp-add" onclick="importDomainFile(\'admin\')">📁 导入文件</div>';
  h+=`<div class="kp-sec"><div class="h">行程地图</div>
    <div id="mapBox" style="height:160px;border-radius:10px;overflow:hidden;border:1px solid var(--glass-brd);position:relative;background:radial-gradient(circle at 50% 50%,rgba(255,157,0,.08),rgba(8,12,28,.5))">
      <div id="mapHint" style="position:absolute;inset:0;display:grid;place-items:center;text-align:center;font-size:11px;color:var(--ink-faint);padding:10px">绘制示意图…</div>
    </div></div>`;
  h+=itinRouteHTML(d);
  setTimeout(()=>drawMap(d.map_data||[]),60);
  return h||'<div class="kp-empty">暂无行程 · 先导入行程点</div>';
}

export function itinRouteHTML(d){
  let h='';
  const opt=d.optimized_route||{};
  if(opt.order_names){h+=`<div class="kp-sec"><div class="h">最优路线 (${opt.method})</div>`;
    opt.order_names.forEach((n,i)=>{h+=`<div class="kp-route"><span class="seq">${i+1}</span><span>${n}</span></div>`;});
    h+=`<div class="kp-metric" style="margin-top:8px"><span class="lab">总距离</span><span class="val">${opt.total_km} km</span></div><div class="kp-metric"><span class="lab">总通勤</span><span class="val">${opt.total_min} 分</span></div></div>`;}
  const cf=d.conflicts||[];
  if(cf.length){h+='<div class="kp-sec"><div class="h">冲突 / 可达性</div>';
    cf.forEach(c=>{h+=`<div class="kp-chip crit"><span class="ico">✕</span><div class="tx"><b>${c.type==='unreachable'?'赶不及':'时间冲突'}</b><span>${c.reason}</span></div></div>`;});
    h+='</div>';}
  return h;
}

export function itinSideHTML(d){
  let h='<div class="kp-add" onclick="openEntry(\'admin\')">＋ 录入行程</div>'
       +'<div class="kp-add" onclick="importDomainFile(\'admin\')">📁 导入文件</div>';
  const body=itinRouteHTML(d);
  return h + (body || '<div style="text-align:left;color:var(--ink-faint);font-size:11px;padding:8px 0;line-height:1.8">暂无行程数据<br>录入后显示示意图</div>');
}

export function drawMap(mapData){
  const box=document.getElementById('mapBox');
  if(!box)return;
  box.innerHTML=simpleMapSVG(mapData.filter(x=>x.type==='poi'),
                              mapData.filter(x=>x.type==='route'));
}

export function simpleMapSVG(pois,routes){
  if(!pois.length)return '<div style="display:grid;place-items:center;height:100%;font-size:11px;color:var(--ink-faint)">暂无坐标 · 给行程点填入经纬度后显示示意图</div>';
  const lngs=pois.map(p=>p.lnglat[0]),lats=pois.map(p=>p.lnglat[1]);
  const minx=Math.min(...lngs),maxx=Math.max(...lngs),miny=Math.min(...lats),maxy=Math.max(...lats);
  const W=240,H=150,pad=24;
  const sx=v=>pad+((v-minx)/((maxx-minx)||1))*(W-2*pad);
  const sy=v=>H-pad-((v-miny)/((maxy-miny)||1))*(H-2*pad);
  let s=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:100%">`;
  routes.forEach(r=>{s+=`<line x1="${sx(r.start[0])}" y1="${sy(r.start[1])}" x2="${sx(r.end[0])}" y2="${sy(r.end[1])}" stroke="#ff9d00" stroke-width="2" stroke-opacity=".6" stroke-dasharray="4 3"/>`;});
  pois.forEach((p,i)=>{const x=sx(p.lnglat[0]),y=sy(p.lnglat[1]);
    s+=`<circle cx="${x}" cy="${y}" r="6" fill="#ff9d00" fill-opacity=".25" stroke="#ff9d00" stroke-width="1.5"/>`;
    s+=`<text x="${x}" y="${y+3}" font-size="8" fill="#fff" text-anchor="middle" font-family="Orbitron">${i+1}</text>`;
    s+=`<text x="${x}" y="${y-9}" font-size="7" fill="#7e93b8" text-anchor="middle">${p.text}</text>`;});
  s+='</svg>';
  return s+`<div style="position:absolute;bottom:4px;right:6px;font-size:8px;color:var(--ink-faint)">坐标示意图</div>`;
}

export function renderSituation(d){
  let h='';
  const f=d.finance||{},l=d.learning||{},i=d.itinerary||{};
  h+=`<div class="kp-sec"><div class="h">驾驶舱视图</div>
    <div class="kp-metric"><span class="lab">💰 财务交易</span><span class="val">${f.transactions||0}</span></div>
    <div class="kp-metric"><span class="lab">📚 待复习</span><span class="val">${l.due_today||0}</span></div>
    <div class="kp-metric"><span class="lab">📍 行程冲突</span><span class="val">${i.conflicts||0}</span></div></div>`;
  const al=d.alerts||[];
  if(al.length){h+='<div class="kp-sec"><div class="h">跨域告警</div>';
    al.forEach(a=>{const cls=a.severity==='critical'?'crit':a.severity==='warn'?'warn':'info';
      h+=`<div class="kp-chip ${cls}"><span class="ico">▲</span><div class="tx"><b>${escapeHtml(a.domain)}</b><span>${escapeHtml(a.msg)}</span></div></div>`;});
    h+='</div>';}
  else h+='<div class="kp-empty">暂无跨域告警 · 系统平稳</div>';
  return h;
}

export function drawMapInto(boxId,mapData){
  const box=document.getElementById(boxId); if(!box)return;
  box.innerHTML=simpleMapSVG(mapData.filter(x=>x.type==='poi'),
                             mapData.filter(x=>x.type==='route'));
}

export async function addWaypointFromSearch(name,addr,lng,lat){
  try{
    await fetch(`${API}/itinerary/import`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rows:[{name,address:addr,lng,lat,start_time:null,duration_min:0,city:''}]})});
    const d=await(await fetch(`${API}/itinerary/plan`)).json();
    _lastKernelData.admin=d;
    drawMapInto('ovMap',d.map_data||[]);
    const side=document.querySelector('.ov-side');
    if(side)side.innerHTML=itinSideHTML(d);
    kpSet('行程规划','Ops',renderItinerary(d));  // 同步主面板
  }catch(e){}
}
