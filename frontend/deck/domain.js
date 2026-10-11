import { escapeHtml } from './util.js?v=2.9';
import { API, _lastKernelData } from './state.js';

/* ===== 高德地图 JSAPI 配置 =====
   Key 不再硬编码:由后端从 .env 读取后,经 /api/config/amap 接口下发。
   用户只需在项目根目录 .env 里填 AMAP_JS_KEY / AMAP_JS_SCODE 即可。
   未配置则地图降级为 SVG 坐标示意图。 */
let AMAP_JS_KEY    = "";   // 运行时从后端注入,勿硬编码
let AMAP_JS_SCODE  = "";   // 运行时从后端注入,勿硬编码
export async function fetchAMapConfig(){
  try{
    const c=await(await fetch(`${API}/config/amap`)).json();
    AMAP_JS_KEY=c.js_key||""; AMAP_JS_SCODE=c.js_scode||"";
  }catch(e){}
}
let _amapLoaded=false, _amapLoading=false, _amapMap=null;
export function loadAMap(){
  return new Promise((resolve,reject)=>{
    if(_amapLoaded)return resolve(window.AMap);
    if(!AMAP_JS_KEY)return reject('no-key');
    if(_amapLoading){const t=setInterval(()=>{if(_amapLoaded){clearInterval(t);resolve(window.AMap);}},200);return;}
    _amapLoading=true;
    // 安全密钥配置必须在加载脚本之前设置
    if(AMAP_JS_SCODE){ window._AMapSecurityConfig={securityJsCode:AMAP_JS_SCODE}; }
    const s=document.createElement('script');
    s.src=`https://webapi.amap.com/maps?v=2.0&key=${AMAP_JS_KEY}`;
    s.onload=()=>{_amapLoaded=true;resolve(window.AMap);};
    s.onerror=()=>reject('load-fail');
    document.head.appendChild(s);
  });
}

export function kpSet(title,tag,html){
  // 右栏改版为「记忆 + 知识」后不再有 kernelPanel;这些元素可能不存在 → 逐个保护,静默跳过。
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
  // 地图容器(高德 JSAPI 可用时画真地图,否则显示文字路线)
  h+=`<div class="kp-sec"><div class="h">行程地图</div>
    <div id="amapBox" style="height:160px;border-radius:10px;overflow:hidden;border:1px solid var(--glass-brd);position:relative;background:radial-gradient(circle at 50% 50%,rgba(255,157,0,.08),rgba(8,12,28,.5))">
      <div id="amapHint" style="position:absolute;inset:0;display:grid;place-items:center;text-align:center;font-size:11px;color:var(--ink-faint);padding:10px">加载地图中…</div>
    </div></div>`;
  h+=itinRouteHTML(d);
  // 异步画地图(DOM 插入后)
  setTimeout(()=>drawAMap(d.map_data||[]),60);
  return h||'<div class="kp-empty">暂无行程 · 先导入行程点</div>';
}

/* 行程的路线/冲突部分(不含地图,供全屏侧栏复用)*/
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

/* 全屏侧栏:只放录入 + 路线信息(不重复地图、不显示无用状态行)*/
export function itinSideHTML(d){
  let h='<div class="kp-add" onclick="openEntry(\'admin\')">＋ 录入行程</div>'
       +'<div class="kp-add" onclick="importDomainFile(\'admin\')">📁 导入文件</div>';
  const body=itinRouteHTML(d);
  return h + (body || '<div style="text-align:left;color:var(--ink-faint);font-size:11px;padding:8px 0;line-height:1.8">搜索地点<br>加入行程</div>');
}

export function drawAMap(mapData){
  const box=document.getElementById('amapBox'), hint=document.getElementById('amapHint');
  if(!box)return;
  const pois=mapData.filter(x=>x.type==='poi');
  const routes=mapData.filter(x=>x.type==='route');
  if(!AMAP_JS_KEY){
    // 无 Key:画一个简版坐标示意(SVG),不依赖外部
    box.innerHTML=simpleMapSVG(pois,routes);
    return;
  }
  loadAMap().then(AMap=>{
    box.innerHTML='';
    const center=pois.length?pois[0].lnglat:[116.40,39.90];
    _amapMap=new AMap.Map(box,{zoom:11,center,viewMode:'2D'});
    const pts=[];
    pois.forEach((p,i)=>{
      const mk=new AMap.Marker({position:p.lnglat,map:_amapMap,
        label:{content:`${i+1}.${p.text}`,direction:'top'}});
      pts.push(p.lnglat);
    });
    routes.forEach(r=>{
      new AMap.Polyline({path:[r.start,r.end],map:_amapMap,
        strokeColor:'#ff9d00',strokeWeight:4,strokeOpacity:.85});
    });
    if(pts.length)_amapMap.setFitView();
  }).catch(()=>{ box.innerHTML=simpleMapSVG(pois,routes); });
}

/* 无 Key 时的简版坐标示意图(SVG,纯本地)*/
export function simpleMapSVG(pois,routes){
  if(!pois.length)return '<div style="display:grid;place-items:center;height:100%;font-size:11px;color:var(--ink-faint)">暂无坐标 · 配置高德 JSAPI Key 后显示真地图</div>';
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
  return s+`<div style="position:absolute;bottom:4px;right:6px;font-size:8px;color:var(--ink-faint)">坐标示意 · 配 JSAPI Key 看真地图</div>`;
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

/* 把地图画进指定容器(全屏复用 drawAMap 逻辑) */
let _ovMapRef=null;   // 全屏地图引用,供搜索使用
export function amapPlaceUrl(name,lng,lat){
  // 跳转高德官网查看完整详情(评价/照片/营业时间)
  return `https://www.amap.com/search?query=${encodeURIComponent(name)}&center=${lng},${lat}&zoom=16`;
}
export function drawAMapInto(boxId,mapData){
  const box=document.getElementById(boxId); if(!box)return;
  const pois=mapData.filter(x=>x.type==='poi');
  const routes=mapData.filter(x=>x.type==='route');
  if(!AMAP_JS_KEY){ box.innerHTML=simpleMapSVG(pois,routes); return; }
  loadAMap().then(AMap=>{
    box.innerHTML='';
    const center=pois.length?pois[0].lnglat:[116.40,39.90];
    // 官网风格:默认亮色底图(normal)
    const map=new AMap.Map(box,{zoom:12,center,viewMode:'2D'});
    _ovMapRef=map;
    // 图层控件:缩放工具条 / 比例尺 / 卫星·路况切换(官网都有)
    AMap.plugin(['AMap.ToolBar','AMap.Scale','AMap.MapType'],()=>{
      map.addControl(new AMap.ToolBar({position:{top:'90px',right:'20px'}}));
      map.addControl(new AMap.Scale());
      map.addControl(new AMap.MapType({
        defaultType:0,        // 0=标准 1=卫星
        showTraffic:false,    // 路况开关
        showRoad:false,       // 卫星图上叠加路网
      }));
    });
    const pts=[];
    pois.forEach((p,i)=>{
      const mk=new AMap.Marker({position:p.lnglat,map,label:{content:`${i+1}.${p.text}`,direction:'top'}});
      mk.on('click',()=>{
        const info=new AMap.InfoWindow({
          content:`<div style="padding:8px 6px;font-size:13px;min-width:160px">
            <b style="font-size:14px">${p.text}</b><br>
            <span style="color:#666;font-size:11px">${p.remark||''}</span><br>
            <a href="${amapPlaceUrl(p.text,p.lnglat[0],p.lnglat[1])}" target="_blank"
               style="display:inline-block;margin-top:8px;padding:5px 12px;background:#3478f6;color:#fff;border-radius:6px;font-size:12px;text-decoration:none">
               在高德查看详情 ↗</a></div>`,
          offset:new AMap.Pixel(0,-30)});
        info.open(map,p.lnglat);
      });
      pts.push(p.lnglat);
    });
    routes.forEach(r=>{new AMap.Polyline({path:[r.start,r.end],map,strokeColor:'#ff9d00',strokeWeight:5,strokeOpacity:.85,showDir:true});});
    if(pts.length)map.setFitView();
  }).catch(()=>{box.innerHTML=simpleMapSVG(pois,routes);});
}

/* 地图地点搜索(高德 AutoComplete 联想)+ 加入行程 */
let _searchMarker=null;
export function initMapSearch(){
  const inp=document.getElementById('mapSearchInput');
  const resBox=document.getElementById('mapSearchResults');
  if(!inp||!AMAP_JS_KEY)return;
  loadAMap().then(AMap=>{
    AMap.plugin(['AMap.AutoComplete'],()=>{
      const auto=new AMap.AutoComplete({city:'全国'});
      let timer=null;
      inp.addEventListener('input',()=>{
        clearTimeout(timer);
        const kw=inp.value.trim();
        if(!kw){resBox.innerHTML='';return;}
        timer=setTimeout(()=>{
          auto.search(kw,(status,result)=>{
            if(status==='complete'&&result.tips){
              resBox.innerHTML=result.tips.filter(t=>t.location).slice(0,8).map(t=>
                `<div class="msr-item" data-lng="${escapeHtml(t.location.lng)}" data-lat="${escapeHtml(t.location.lat)}" data-name="${escapeHtml(t.name)}" data-addr="${escapeHtml((t.district||'')+(t.address||''))}">
                  <b>${escapeHtml(t.name)}</b><span>${escapeHtml(t.district||'')} ${escapeHtml(t.address||'')}</span></div>`).join('');
              bindSearchResults();
            }
          });
        },300);
      });
    });
  });
}
export function bindSearchResults(){
  document.querySelectorAll('.msr-item').forEach(it=>{
    it.addEventListener('click',()=>{
      const lng=parseFloat(it.dataset.lng),lat=parseFloat(it.dataset.lat),name=it.dataset.name,addr=it.dataset.addr;
      if(_ovMapRef&&window.AMap){
        _ovMapRef.setZoomAndCenter(15,[lng,lat]);
        if(_searchMarker)_ovMapRef.remove(_searchMarker);
        _searchMarker=new AMap.Marker({position:[lng,lat],map:_ovMapRef,
          label:{content:name,direction:'top'}});
        // 弹出加入行程
        const info=new AMap.InfoWindow({
          content:`<div style="padding:6px 4px;font-size:13px;min-width:170px"><b>${name}</b><br><span style="color:#666;font-size:11px">${addr}</span><br>
            <div style="display:flex;gap:6px;margin-top:8px">
            <button id="addWpBtn" style="padding:6px 12px;background:#ff9d00;border:0;border-radius:6px;color:#fff;cursor:pointer;font-size:12px">＋ 加入行程</button>
            <a href="${amapPlaceUrl(name,lng,lat)}" target="_blank" style="padding:6px 12px;background:#3478f6;border-radius:6px;color:#fff;font-size:12px;text-decoration:none">详情 ↗</a>
            </div></div>`,
          offset:new AMap.Pixel(0,-30)});
        info.open(_ovMapRef,[lng,lat]);
        setTimeout(()=>{const b=document.getElementById('addWpBtn');
          if(b)b.onclick=()=>addWaypointFromSearch(name,addr,lng,lat);},100);
      }
      document.getElementById('mapSearchResults').innerHTML='';
      document.getElementById('mapSearchInput').value=name;
    });
  });
}
export async function addWaypointFromSearch(name,addr,lng,lat){
  try{
    await fetch(`${API}/itinerary/import`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rows:[{name,address:addr,lng,lat,start_time:null,duration_min:0,city:''}]})});
    // 重新拉行程数据并重绘地图
    const d=await(await fetch(`${API}/itinerary/plan`)).json();
    _lastKernelData.admin=d;
    drawAMapInto('ovMap',d.map_data||[]);
    // 侧栏路线信息也刷新
    const side=document.querySelector('.ov-side');
    if(side)side.innerHTML=itinSideHTML(d);
    kpSet('行程规划','Ops',renderItinerary(d));  // 同步主面板
  }catch(e){}
}
