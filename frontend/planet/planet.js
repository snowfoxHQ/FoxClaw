
const KU_COLORS = {
  Entity:   '#002FA7',  // 克莱因蓝
  Concept:  '#2c9678',  // 绿
  Fact:     '#5a78cc',  // 蓝浅
  Claim:    '#6bbfa6',  // 绿浅
  Evidence: '#001f70',  // 蓝深
  Ability:  '#1f6e58',  // 绿深
};
const KU_LABEL = {
  Entity:'实体', Concept:'概念', Fact:'事实',
  Claim:'观点', Evidence:'证据', Ability:'能力'
};

const DEMO = {
  kus: {
    ENT_01:{ku_id:'ENT_01',ku_type:'Entity',name:'Charlie Munger',summary:'伯克希尔·哈撒韦副董事长，价值投资与多元思维模型的代表人物。',domain:'Investment',confidence:1.0,status:'active',tags:['投资','心理学'],extra:{entity_type:'Person',aliases:['查理·芒格']}},
    ENT_02:{ku_id:'ENT_02',ku_type:'Entity',name:'Warren Buffett',summary:'伯克希尔·哈撒韦董事长，被誉为奥马哈先知。',domain:'Investment',confidence:1.0,status:'active',tags:['投资'],extra:{entity_type:'Person',aliases:['巴菲特']}},
    ENT_03:{ku_id:'ENT_03',ku_type:'Entity',name:'Benjamin Graham',summary:'价值投资之父，《证券分析》《聪明的投资者》作者。',domain:'Investment',confidence:1.0,status:'active',tags:['投资'],extra:{entity_type:'Person',aliases:['格雷厄姆']}},
    CON_01:{ku_id:'CON_01',ku_type:'Concept',name:'价值投资',summary:'以低于内在价值的价格买入优质资产，长期持有。',domain:'Investment',confidence:1.0,status:'active',tags:['方法论'],extra:{definition:'寻找市场价格低于内在价值的证券'}},
    CON_02:{ku_id:'CON_02',ku_type:'Concept',name:'多元思维模型',summary:'用多学科的核心模型交叉分析问题，避免单一视角偏差。',domain:'Investment',confidence:1.0,status:'active',tags:['思维'],extra:{definition:'Mental Models'}},
    CON_03:{ku_id:'CON_03',ku_type:'Concept',name:'安全边际',summary:'买入价与内在价值之间的缓冲，价值投资的核心防御机制。',domain:'Investment',confidence:1.0,status:'active',tags:['估值'],extra:{definition:'Margin of Safety'}},
    CON_04:{ku_id:'CON_04',ku_type:'Concept',name:'护城河',summary:'企业可持续的竞争优势，如品牌、网络效应、转换成本。',domain:'Investment',confidence:1.0,status:'active',tags:['竞争优势'],extra:{definition:'Economic Moat'}},
    CLM_01:{ku_id:'CLM_01',ku_type:'Claim',name:'长期价值投资优于短线',summary:'',domain:'Investment',confidence:0.88,status:'active',tags:[],extra:{statement:'长期价值投资的回报显著优于短线交易',claim_status:'active',supports:['EVD_01','EVD_02']}},
    CLM_02:{ku_id:'CLM_02',ku_type:'Claim',name:'集中投资优于分散',summary:'',domain:'Investment',confidence:0.71,status:'active',tags:[],extra:{statement:'对深度理解的少数标的集中投资优于广泛分散',claim_status:'disputed',supports:['EVD_02']}},
    CLM_03:{ku_id:'CLM_03',ku_type:'Claim',name:'广泛分散降低风险',summary:'',domain:'Investment',confidence:0.69,status:'active',tags:[],extra:{statement:'广泛分散持仓是降低非系统性风险的最优策略',claim_status:'disputed',supports:['EVD_03']}},
    EVD_01:{ku_id:'EVD_01',ku_type:'Evidence',name:'穷查理宝典',summary:'记录芒格投资哲学与思维方法的文集。',domain:'Investment',confidence:1.0,status:'active',tags:[],extra:{source:'Poor Charlie\'s Almanack',author:'Charlie Munger',year:2005,page:127}},
    EVD_02:{ku_id:'EVD_02',ku_type:'Evidence',name:'巴菲特致股东信',summary:'1965年至今的伯克希尔年度股东信合集。',domain:'Investment',confidence:1.0,status:'active',tags:[],extra:{source:'Berkshire Shareholder Letters',author:'Warren Buffett',year:2023}},
    EVD_03:{ku_id:'EVD_03',ku_type:'Evidence',name:'现代投资组合理论',summary:'马科维茨提出的均值-方差分散化框架。',domain:'Investment',confidence:1.0,status:'active',tags:[],extra:{source:'Portfolio Selection',author:'Harry Markowitz',year:1952}},
    ABL_01:{ku_id:'ABL_01',ku_type:'Ability',name:'价值投资者',summary:'',domain:'Investment',confidence:1.0,status:'active',tags:[],extra:{ability_key:'value_investor'}},
  },
  relations:[
    {from:'ENT_01',to:'CON_02',type:'CREATED_BY'},
    {from:'ENT_01',to:'CON_01',type:'USES'},
    {from:'ENT_02',to:'CON_01',type:'USES'},
    {from:'ENT_03',to:'CON_01',type:'CREATED_BY'},
    {from:'ENT_03',to:'CON_03',type:'CREATED_BY'},
    {from:'CON_01',to:'CON_03',type:'DERIVED_FROM'},
    {from:'CON_01',to:'CON_04',type:'DERIVED_FROM'},
    {from:'EVD_01',to:'CLM_01',type:'SUPPORTS'},
    {from:'EVD_02',to:'CLM_01',type:'SUPPORTS'},
    {from:'EVD_02',to:'CLM_02',type:'SUPPORTS'},
    {from:'EVD_03',to:'CLM_03',type:'SUPPORTS'},
    {from:'CLM_02',to:'CLM_03',type:'CONTRADICTS'},
    {from:'ABL_01',to:'CON_01',type:'USES'},
    {from:'EVD_01',to:'ENT_01',type:'MENTIONS'},
  ],
  abilities:[
    {ability_key:'value_investor',display_name:'价值投资者',domain:'Investment',
     coverage:{fundamental_analysis:0.82,valuation:0.78,moat_analysis:0.71,financial_statement:0.55},
     can_compile:true,compiled:true},
    {ability_key:'quant_analyst',display_name:'量化分析师',domain:'Investment',
     coverage:{factor_investing:0.45,backtesting:0.30,risk_management:0.62,portfolio_opt:0.51,data_analysis:0.20},
     can_compile:false,missing:['factor_investing','backtesting','data_analysis']},
    {ability_key:'psychology_expert',display_name:'心理学专家',domain:'Psychology',
     coverage:{cognitive_bias:0.68,behavioral_psychology:0.40,social_influence:0.33,decision_making:0.71},
     can_compile:false,missing:['behavioral_psychology','social_influence']},
  ],
  conflicts:[
    {conflict_id:'CFT_01',claim_a_id:'CLM_02',claim_b_id:'CLM_03',domain:'Investment',
     status:'open',resolution_strategy:'evidence_weight',resolution_note:''},
  ],
};

class HKCClient{
  constructor(){ this.base=null; this.demo=true; this.apiKey=''; }
  async connect(url){
    try{
      const r=await fetch(url.replace(/\/$/,'')+'/health',{signal:AbortSignal.timeout(4000)});
      if(!r.ok) throw 0;
      this.base=url.replace(/\/$/,''); this.demo=false; return true;
    }catch(e){ this.demo=true; this.base=null; return false; }
  }
  useDemo(){ this.demo=true; this.base=null; }
  _h(extra){ const h=Object.assign({}, extra||{}); if(this.apiKey) h['X-API-Key']=this.apiKey; return h; }
  async _get(p){ const r=await fetch(this.base+p,{headers:this._h()}); if(!r.ok) throw new Error(r.status); return r.json(); }
  async _post(p,b){ const r=await fetch(this.base+p,{method:'POST',headers:this._h({'Content-Type':'application/json'}),body:JSON.stringify(b)});
    if(!r.ok){ const e=await r.json().catch(()=>({})); throw {status:r.status,detail:e.detail||e.error||e.message}; } return r.json(); }
  async _postForm(p,fd){ const r=await fetch(this.base+p,{method:'POST',headers:this._h(),body:fd});  // 不设 Content-Type，浏览器自动带 multipart boundary
    if(!r.ok){ const e=await r.json().catch(()=>({})); throw {status:r.status,detail:e.detail||e.error||e.message}; } return r.json(); }
}
const client=new HKCClient();

const state={ kus:{}, relations:[], abilities:[], conflicts:[], selected:null, mode:'hybrid', conflictPick:{}, synth:{}, pathSel:{} };
const $=s=>document.querySelector(s);
const $$=s=>document.querySelectorAll(s);

function toast(msg,kind=''){
  const t=document.createElement('div');
  t.className='toast '+kind;
  t.innerHTML=(kind==='ok'?'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6 9 17l-5-5"/></svg>':'')+`<span>${msg}</span>`;
  $('#toast-wrap').appendChild(t);
  setTimeout(()=>{t.style.opacity='0';t.style.transition='opacity .3s';setTimeout(()=>t.remove(),300)},2600);
}

async function loadData(){
  if(client.demo){
    state.kus=JSON.parse(JSON.stringify(DEMO.kus));
    state.relations=DEMO.relations.map(r=>({...r}));
    state.abilities=DEMO.abilities.map(a=>({...a}));
    state.conflicts=DEMO.conflicts.map(c=>({...c}));
  }else{
    try{
      const g=await client._get('/knowledge/graph?limit=5000');   // 拉到后端硬上限(默认仅 2000)
      if(!(g.kus && g.kus.length)){
        toast('知识库为空,先展示演示星球;摄入知识后自动显示真实数据','');
        state.kus=JSON.parse(JSON.stringify(DEMO.kus));
        state.relations=DEMO.relations.map(r=>({...r}));
        state.abilities=DEMO.abilities.map(a=>({...a}));
        state.conflicts=DEMO.conflicts.map(c=>({...c}));
        renderAll(); return;
      }
      state.kus={};
      (g.kus||[]).forEach(k=>{ state.kus[k.ku_id]=k; });
      state.relations=(g.relations||[]).map(r=>({
        from:r.from_ku, to:r.to_ku, type:r.rel_type||'RELATED',
      }));
      const ab=await client._get('/abilities').catch(()=>({abilities:[]}));
      state.abilities=[];
      for(const key of (ab.abilities||[])){
        const rep=await client._get(`/abilities/${key}/coverage`).catch(()=>null);
        if(rep) state.abilities.push({ability_key:key,display_name:rep.display_name||key,
          domain:(rep.domains||[]).join(' / '),
          coverage:rep.coverage,can_compile:rep.can_compile,missing:rep.missing_skills});
      }
      const cf=await client._get('/conflicts?status=open').catch(()=>({conflicts:[]}));
      state.conflicts=cf.conflicts||[];
      updateDataInfo().catch(()=>{});
    }catch(e){ toast('数据加载出错，回退演示模式','err'); client.useDemo(); return loadData(); }
  }
  renderAll();
}

async function updateDataInfo(){
  const pill=$('#conn-pill');
  const st=await client._get('/stats');
  const dir=st.data_dir||'(未知)';
  const total=st.total_kus||0;              // 全库真实总数
  const emb=st.embedding||'';               // 生效的向量后端
  const shown=Object.keys(state.kus).length; // 星球实际渲染数(受 limit 限制)
  const stubEmb=/stub/i.test(emb);
  if(pill){
    let tip=`数据目录：${dir}\n全库知识：${total} 个`;
    if(shown<total) tip+=`\n星球显示：${shown} 个（受上限限制，未全部显示）`;
    if(emb) tip+=`\n向量后端：${emb}${stubEmb?'（无真实语义，语义搜索受限；设 HKC_EMBEDDING=local 启用）':''}`;
    pill.title=tip;
  }
  if(total===0){
    toast(`⚠️ 知识库为空！后端数据目录：${dir}\n若你之前上传过书，很可能是后端连错了目录`,'err');
  }else{
    let msg=`已连接 · 全库 ${total} 个知识`;
    if(shown<total) msg+=` · 星球显示 ${shown} 个（达上限）`;
    toast(msg,'ok');
  }
}

function renderAll(){ renderGraph(); renderPanels(); }
function renderPanels(){
  renderAbilities(); renderConflicts(); renderLegend();
  $('#ability-count').textContent=state.abilities.length+' 项';
  const oc=state.conflicts.filter(c=>c.status==='open').length;
  $('#conflict-count').textContent=oc;
}

let THREE = null;            // 动态加载的 Three.js 命名空间
let planet = null;           // 当前星球实例

function disputedOf(k){ return k.extra && k.extra.claim_status==='disputed'; }

function nodeRadius(k){
  if(k.ku_type==='Ability') return 7;
  if(k.ku_type==='Entity'||k.ku_type==='Concept') return 6;
  if(k.ku_type==='Claim') return 3.5 + (k.confidence||0.5)*3;
  return 5;
}

const PLANET_LAYOUT = {
  SHELL_CAPACITY: 800,          // 单层舒适容量;超过开始缩点,(未来)超过可分层
  BASE_R_MIN: 160, BASE_R_MAX: 320,

  compute(n){
    const radius     = Math.max(this.BASE_R_MIN, Math.min(this.BASE_R_MAX, 90 + n*11));
    const dotScale   = Math.max(0.45, Math.min(1, Math.sqrt(this.SHELL_CAPACITY / Math.max(1, n))));
    const shells     = 1;       // 预留:未来 Math.ceil(n/SHELL_CAPACITY) 启用分层
    const labelScale = 1;       // 预留:未来书多时缩小书名标签
    return { radius, dotScale, shells, labelScale };
  },

  position(i, n, params, T){
    const golden = Math.PI * (3 - Math.sqrt(5));
    const shells = params.shells || 1;
    const per    = Math.ceil(n / shells);
    const shell  = Math.floor(i / per);
    const localI = i - shell * per;
    const localN = Math.max(1, Math.min(per, n - shell * per));
    const r      = params.radius * (1 - shell * 0.32);   // 内层半径递减
    const y      = 1 - (localI / Math.max(1, localN - 1)) * 2;
    const rad    = Math.sqrt(Math.max(0, 1 - y*y));
    const theta  = golden * localI;
    return new T.Vector3(Math.cos(theta)*rad*r, y*r, Math.sin(theta)*rad*r);
  },
};

class KnowledgePlanet {
  constructor(container, THREE){
    this.T = THREE;
    this.container = container;
    this.nodes = [];      // {id, ku, mesh, glow, vel, pos, radius, label}
    this.edges = [];      // {from, to, line, contradicts}
    this.raf = null;
    this.disposed = false;
    this.hovered = null;
    this.selected = null;
    this._initScene();
  }

  _initScene(){
    const T = this.T;
    const w = this.container.clientWidth || 700;
    const h = this.container.clientHeight || 600;

    this.scene = new T.Scene();

    this.camera = new T.PerspectiveCamera(55, w/h, 1, 4000);
    this.camera.position.set(0, 0, 720);

    this.renderer = new T.WebGLRenderer({ antialias:true, alpha:true });
    this.renderer.setSize(w, h);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1, 2));
    this.renderer.setClearColor(0x000000, 0);  // 透明，露出 CSS 星空背景
    this.container.appendChild(this.renderer.domElement);
    this.canvas = this.renderer.domElement;
    this.canvas.style.display = 'block';

    this.scene.add(new T.AmbientLight(0xffffff, 0.75));
    const l1 = new T.PointLight(0x9fe8d2, 0.9, 0); l1.position.set(200, 180, 300); this.scene.add(l1);
    const l2 = new T.PointLight(0x7d97ff, 0.7, 0); l2.position.set(-220, -120, 160); this.scene.add(l2);

    this.world = new T.Group();
    this.scene.add(this.world);

    this._initControls();
  }

  _initControls(){
    this.rot = { x: 0, y: 0 };          // 目标旋转
    this.rotCur = { x: 0, y: 0 };       // 当前（缓动）
    this.autoSpin = true;
    this.camDist = 720;
    this.camDistTarget = 720;
    let dragging = false, lastX = 0, lastY = 0;

    const el = this.canvas;
    el.style.cursor = 'grab';

    this._onDown = (e)=>{ dragging = true; this.dragging = true; lastX = e.clientX; lastY = e.clientY; el.style.cursor='grabbing';
      this._downX = e.clientX; this._downY = e.clientY;   // 记录起点,用于区分"拖动"与"点击"
      this.rot.x = this.rotCur.x; this.rot.y = this.rotCur.y; this.autoSpin = false;
      this._flyTarget = null; };
    this._onMove = (e)=>{
      if(!dragging) return;
      const dx = e.clientX - lastX, dy = e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      this.rot.y += dx * 0.006;
      this.rot.x += dy * 0.006;   // 不再钳制俯仰:可自由翻转,把任意节点(含两极)拉到正面
    };
    this._onUp = ()=>{ dragging = false; this.dragging = false; el.style.cursor='grab'; };
    this._onWheel = (e)=>{
      e.preventDefault();
      this.camDistTarget += e.deltaY * 0.5;
      this.camDistTarget = Math.max(250, Math.min(1500, this.camDistTarget));
    };

    el.addEventListener('pointerdown', this._onDown);
    window.addEventListener('pointermove', this._onMove);
    window.addEventListener('pointerup', this._onUp);
    el.addEventListener('wheel', this._onWheel, { passive:false });
  }

  build(kus, relations){
    const T = this.T;
    const ids = Object.keys(kus);
    const n = ids.length;
    if(!n) return;

    const _h = s => {
      let h = 2166136261 >>> 0;
      for(let k=0;k<s.length;k++){ h ^= s.charCodeAt(k); h = Math.imul(h, 16777619); }
      h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995); h ^= h >>> 15;
      return h >>> 0;
    };
    ids.sort((a,b)=> _h(a) - _h(b));

    const params = PLANET_LAYOUT.compute(n);
    this._layoutParams = params;   // 供 _makeLabel 读取 labelScale 等

    ids.forEach((id, i)=>{
      const ku = kus[id];
      const radius = nodeRadius(ku) * params.dotScale;   // 圆点随节点总数自适应缩小
      const pos = PLANET_LAYOUT.position(i, n, params, T);

      const color = new T.Color(KU_COLORS[ku.ku_type] || '#888');
      const geo = new T.SphereGeometry(radius, 24, 24);
      const mat = new T.MeshStandardMaterial({
        color, emissive: color.clone().multiplyScalar(0.35),
        roughness: 0.45, metalness: 0.15,
      });
      const mesh = new T.Mesh(geo, mat);
      mesh.position.copy(pos);
      mesh.userData.id = id;
      this.world.add(mesh);

      const glowMat = new T.MeshBasicMaterial({ color, transparent:true, opacity:0.12, side:T.BackSide });
      const glow = new T.Mesh(new T.SphereGeometry(radius*1.6, 20, 20), glowMat);
      mesh.add(glow);

      if(disputedOf(ku)){
        const ring = new T.Mesh(
          new T.SphereGeometry(radius*1.35, 20, 20),
          new T.MeshBasicMaterial({ color:0xb0413e, transparent:true, opacity:0.3, side:T.BackSide })
        );
        mesh.add(ring);
      }

      this.nodes.push({
        id, ku, mesh, glow, radius,
        pos: pos.clone(),
        vel: new T.Vector3(),
        basePos: pos.clone(),
        baseColor: color.clone(),         // 复用,避免每帧 new T.Color
        phase: Math.random()*Math.PI*2,   // 漂浮相位
      });

      const label = this._makeLabel(ku.name);
      label.position.set(0, -(radius+9), 0);
      mesh.add(label);
      const _nd = this.nodes[this.nodes.length-1];
      _nd.label = label;
      _nd.isBook = (ku.ku_type === 'Evidence');
      _nd.deg = 0;           // 连接数,建边后回填(作重要度)
      _nd._revealLabel = false;
      label.visible = _nd.isBook;
    });

    const nodeById = {};
    this.nodes.forEach(nd=> nodeById[nd.id] = nd);
    (relations||[]).forEach(r=>{
      const a = nodeById[r.from], b = nodeById[r.to];
      if(!a || !b) return;
      const contradicts = r.type === 'CONTRADICTS';
      const mat = new T.LineBasicMaterial({
        color: contradicts ? 0xb0413e : 0x9aa3b8,
        transparent:true, opacity: contradicts ? 0.55 : 0.22,
      });
      const geo = new T.BufferGeometry().setFromPoints([a.pos.clone(), b.pos.clone()]);
      const line = new T.Line(geo, mat);
      line.visible = false;   // 默认不画边(大图会糊成一团);点中节点才显示它的连线
      this.world.add(line);
      this.edges.push({ from:a, to:b, line, contradicts });
    });

    this.edges.forEach(e=>{ e.from.deg++; e.to.deg++; });

    this._buildPicking();
  }

  _buildPicking(){
    const T = this.T;
    this.raycaster = new T.Raycaster();
    this.pointer = new T.Vector2();
    this._meshes = this.nodes.map(n=>n.mesh);

    this._onPointerMove = (e)=>{
      const rect = this.canvas.getBoundingClientRect();
      this.pointer.x = ((e.clientX - rect.left)/rect.width)*2 - 1;
      this.pointer.y = -((e.clientY - rect.top)/rect.height)*2 + 1;
    };
    this._onClick = (e)=>{
      const moved = Math.hypot(e.clientX - (this._downX||0), e.clientY - (this._downY||0));
      if(moved > 6) return;
      const hit = this._pick();
      if(hit){ if(window.selectKU) window.selectKU(hit.id); }
      else { if(window.clearSelection) window.clearSelection(); }
    };
    this.canvas.addEventListener('pointermove', this._onPointerMove);
    this.canvas.addEventListener('click', this._onClick);
  }

  _pick(){
    if(!this.raycaster) return null;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this._meshes, false);
    if(hits.length){
      const id = hits[0].object.userData.id;
      return this.nodes.find(n=>n.id===id);
    }
    return null;
  }

  start(){
    const T = this.T;
    const tick = ()=>{
      if(this.disposed) return;
      this.raf = requestAnimationFrame(tick);
      this._update();
      this.renderer.render(this.scene, this.camera);
    };
    tick();
  }

  _update(){
    const T = this.T;
    const t = performance.now() * 0.001;

    if(this.autoSpin) this.rot.y += 0.0011;

    const tiltX = this.autoSpin ? Math.sin(t*0.045)*1.35 : this.rot.x;
    const rotEase = this.dragging ? 0.85 : 0.08;
    this.rotCur.x += (tiltX - this.rotCur.x) * rotEase;
    this.rotCur.y += (this.rot.y - this.rotCur.y) * rotEase;
    this.world.rotation.x = this.rotCur.x;
    this.world.rotation.y = this.rotCur.y;

    this.camDist += (this.camDistTarget - this.camDist) * 0.1;
    this.camera.position.z = this.camDist;

    if(this._flyTarget){
      const nd = this._flyTarget;
      const p = nd.pos;
      const targetRotY = -Math.atan2(p.x, p.z);
      const targetRotX = Math.atan2(p.y, Math.sqrt(p.x*p.x+p.z*p.z));
      this.rot.y = targetRotY;
      this.rot.x = targetRotX;   // 不钳制:点击两极节点也能完整转到正面中心
    }

    const nodes = this.nodes;
    nodes.forEach(nd=>{
      let tx, ty, tz;
      if(nd.focusPos){
        tx = nd.focusPos.x; ty = nd.focusPos.y; tz = nd.focusPos.z;
      }else{
        const bx = Math.sin(t*0.5 + nd.phase)*1.0;
        const by = Math.cos(t*0.4 + nd.phase)*1.0;
        tx = nd.basePos.x + bx; ty = nd.basePos.y + by; tz = nd.basePos.z;
      }
      const cur = nd.mesh.position;
      const ease = nd.focusPos ? 0.10 : 0.06;
      cur.x += (tx - cur.x)*ease;
      cur.y += (ty - cur.y)*ease;
      cur.z += (tz - cur.z)*ease;
      nd.pos.copy(cur);

      const targetEmissive = nd.glowOn ? 1.0 : 0.35;
      nd._emi = (nd._emi===undefined?0.35:nd._emi) + (targetEmissive - (nd._emi||0.35))*0.12;
      if(nd.mesh.material && nd.mesh.material.emissive && nd.baseColor){
        nd.mesh.material.emissive.copy(nd.baseColor).multiplyScalar(nd._emi);
      }
      const targetOpacity = (nd.dim ? 0.18 : 1.0);
      nd._op = (nd._op===undefined?1:nd._op) + (targetOpacity - (nd._op||1))*0.12;
      nd.mesh.material.opacity = nd._op;
      nd.mesh.material.transparent = nd._op < 0.99;
      if(nd.label){
        const forced = (nd===this.hovered || nd.id===this.selected);
        const show = forced || (this.selected ? nd._revealLabel : nd.isBook);
        nd.label.visible = show;
        if(show) nd.label.material.opacity = nd._op;
      }
    });

    this.edges.forEach(e=>{
      if(!e.line.visible) return;
      const arr = e.line.geometry.attributes.position.array;
      arr[0]=e.from.pos.x; arr[1]=e.from.pos.y; arr[2]=e.from.pos.z;
      arr[3]=e.to.pos.x;   arr[4]=e.to.pos.y;   arr[5]=e.to.pos.z;
      e.line.geometry.attributes.position.needsUpdate = true;
    });

    const hit = this._pick();
    if(hit !== this.hovered){
      if(this.hovered) this._setHover(this.hovered, false);
      if(hit) this._setHover(hit, true);
      this.hovered = hit;
      this.canvas.style.cursor = hit ? 'pointer' : 'grab';
    }
    this.nodes.forEach(nd=>{
      const target = (nd===this.hovered || nd.id===this.selected) ? 1.5 : 1.0;
      const s = nd.mesh.scale.x + (target - nd.mesh.scale.x)*0.18;
      nd.mesh.scale.set(s,s,s);
    });
  }

  _setHover(nd, on){ /* 状态切换占位，scale 在 _update 平滑处理 */ }

  _makeLabel(text){
    const T = this.T;
    const label = (text||'').length>12 ? text.slice(0,11)+'…' : (text||'');
    const cv = document.createElement('canvas');
    const ctx = cv.getContext('2d');
    const fs = 48;
    ctx.font = `600 ${fs}px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif`;
    const w = Math.ceil(ctx.measureText(label).width) + 24;
    cv.width = w; cv.height = fs+24;
    ctx.font = `600 ${fs}px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif`;
    ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillStyle='rgba(255,255,255,.92)';
    ctx.shadowColor='rgba(0,0,0,.85)'; ctx.shadowBlur=8;
    ctx.fillText(label, cv.width/2, cv.height/2);
    const tex = new T.CanvasTexture(cv);
    tex.minFilter = T.LinearFilter;
    const mat = new T.SpriteMaterial({ map:tex, transparent:true, depthWrite:false });
    const sp = new T.Sprite(mat);
    const scale = 0.22 * (this._layoutParams ? this._layoutParams.labelScale : 1);  // 标签自适应缩放接入点
    sp.scale.set(cv.width*scale, cv.height*scale, 1);
    return sp;
  }

  flyTo(id){
    const nd = this.nodes.find(n=>n.id===id);
    if(!nd) return;
    this.autoSpin = false;
    this._flyTarget = nd;
    this.camDistTarget = 380;
  }

  setSelected(id){
    this.selected = id;
    if(id) this.focusOn(id);
    else this.clearFocus();
  }

  focusOn(id){
    const T = this.T;
    const center = this.nodes.find(n=>n.id===id);
    if(!center) return;
    this.autoSpin = false;
    this._flyTarget = center;
    this.camDistTarget = 420;

    const relatedIds = new Set([id]);
    this.edges.forEach(e=>{
      const on = (e.from.id===id || e.to.id===id);
      e.line.visible = on;
      if(on){ relatedIds.add(e.from.id); relatedIds.add(e.to.id); }
    });
    const related = this.nodes.filter(n=>relatedIds.has(n.id) && n.id!==id);

    this.nodes.forEach(n=> n._revealLabel=false);
    related.slice().sort((a,b)=> (b.deg||0)-(a.deg||0)).slice(0, 6)
           .forEach(n=> n._revealLabel=true);

    center.focusPos = new T.Vector3(0, 0, 0);
    center.glowOn = true; center.dim = false;

    const R = 95;   // 汇聚半径
    const n = related.length;
    related.forEach((nd, i)=>{
      const ang = (i / Math.max(1,n)) * Math.PI*2;
      const tilt = 0.5;
      nd.focusPos = new T.Vector3(
        Math.cos(ang)*R,
        Math.sin(ang)*R*tilt,
        Math.sin(ang)*R*0.8
      );
      nd.glowOn = true; nd.dim = false;
    });

    this.nodes.forEach(nd=>{
      if(!relatedIds.has(nd.id)){
        nd.focusPos = nd.basePos.clone().multiplyScalar(2.4);  // 退远
        nd.glowOn = false; nd.dim = true;
      }
    });
  }

  clearFocus(){
    this._flyTarget = null;
    this.camDistTarget = 720;
    this.autoSpin = true;
    this.nodes.forEach(nd=>{
      nd.focusPos = null;
      nd.glowOn = false;
      nd.dim = false;
      nd._revealLabel = false;   // 回到默认态:只剩一级书名标签
    });
    this.edges.forEach(e=>{ e.line.visible = false; });   // 回到默认:不画边
  }

  highlight(ids){
    const set = new Set(ids || []);
    if(!set.size){ this.setSelected(null); return; }   // 无命中→恢复默认视图
    this.autoSpin = false;
    this._flyTarget = null;
    this.selected = '__search__';   // 让标签逻辑走"只显示 _revealLabel(命中项)"分支
    this.nodes.forEach(nd=>{
      const hit = set.has(nd.id);
      nd.focusPos = null;           // 原地高亮,不像点击那样汇聚
      nd.glowOn = hit;
      nd.dim = !hit;
      nd._revealLabel = hit;        // 命中项显示标签,方便看是哪些
    });
  }

  resetView(){
    this.rot = { x:0, y:0 }; this.autoSpin = true;
    this.camDistTarget = 720;
  }

  resize(){
    if(this.disposed) return;
    const w = this.container.clientWidth, h = this.container.clientHeight;
    this.camera.aspect = w/h; this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  dispose(){
    this.disposed = true;
    if(this.raf) cancelAnimationFrame(this.raf);

    try{
      window.removeEventListener('pointermove', this._onMove);
      window.removeEventListener('pointerup', this._onUp);
      if(this.canvas){
        this.canvas.removeEventListener('pointerdown', this._onDown);
        this.canvas.removeEventListener('wheel', this._onWheel);
        this.canvas.removeEventListener('pointermove', this._onPointerMove);
        this.canvas.removeEventListener('click', this._onClick);
      }
    }catch(e){}

    try{
      this.scene.traverse(obj=>{
        if(obj.geometry) obj.geometry.dispose();
        if(obj.material){
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          mats.forEach(m=>{
            if(m.map) m.map.dispose();          // 纹理(label CanvasTexture)
            if(m.dispose) m.dispose();
          });
        }
      });
    }catch(e){}

    try{
      this.renderer.dispose();
      this.renderer.forceContextLoss();
      if(this.canvas && this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    }catch(e){}

    this.nodes = []; this.edges = []; this._meshes = null;
    this.scene = null; this.renderer = null; this.camera = null; this.world = null;
  }
}

async function ensureThree(){
  if(THREE) return THREE;
  if(window.THREE){ THREE = window.THREE; return THREE; }
  if(window.__threeLoadError) return null;
  return await new Promise(resolve=>{
    let done = false;
    const onReady = ()=>{ if(done) return; done=true; THREE = window.THREE; resolve(THREE); };
    window.addEventListener('three-ready', onReady, { once:true });
    const t0 = Date.now();
    const poll = setInterval(()=>{
      if(window.THREE){ clearInterval(poll); onReady(); }
      else if(window.__threeLoadError || Date.now()-t0 > 5000){ clearInterval(poll); if(!done){ done=true; resolve(null); } }
    }, 100);
  });
}

async function renderGraph(){
  const container = document.getElementById('graph-canvas');
  if(!container) return;

  if(planet){ try{ planet.dispose(); }catch(e){} planet = null; }
  Array.from(container.querySelectorAll('canvas')).forEach(c=>c.remove());

  const T = await ensureThree();
  if(!T){
    container.insertAdjacentHTML('beforeend',
      '<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#8b909b;font-size:13px;text-align:center;padding:20px">3D 引擎需通过 http 访问<br>请用本地服务器打开（python -m http.server）</div>');
    return;
  }
  if(!Object.keys(state.kus).length) return;

  planet = new KnowledgePlanet(container, T);
  planet.build(state.kus, state.relations);
  if(state.selected) planet.setSelected(state.selected);
  planet.start();
  window.planet = planet;

  window._fitGraph = ()=>{ if(planet) planet.resetView(); };
  if(!window._planetResizeBound){
    window.addEventListener('resize', ()=>{ if(planet) planet.resize(); });
    window._planetResizeBound = true;
  }
}

function startRotation(){
  if(!planet) return;
  if(planet.raf) return;       // 已在跑(避免 start() 双重 RAF 嵌套)
  planet.start();               // 复用现有 start() 启动 tick
}
function stopRotation(){
  if(!planet) return;
  if(planet.raf){
    cancelAnimationFrame(planet.raf);
    planet.raf = null;
  }
}

function relLabel(t){
  const m={CREATED_BY:'提出',SUPPORTS:'支持',CONTRADICTS:'冲突',MENTIONS:'提及',
    BELONGS_TO:'属于',DERIVED_FROM:'衍生',IMPLEMENTS:'实现',USES:'运用',SUPERSEDES:'取代'};
  return m[t]||t;
}

function renderLegend(){
  const types=['Entity','Concept','Claim','Evidence','Ability'];
  $('#legend').innerHTML=types.map(t=>
    `<div class="lg-row"><span class="lg-dot" style="background:${KU_COLORS[t]}"></span>${KU_LABEL[t]} ${t}</div>`
  ).join('')+`<div class="lg-row" style="border-top:1px solid var(--line-2);padding-top:6px;margin-top:1px">
    <span class="lg-dot" style="background:none;border:2px dashed var(--alert);width:8px;height:8px"></span>观点冲突</div>`;
}

function selectKU(id){
  if(!state.kus[id]) return;
  state.selected=id;
  if(planet){ try{ planet.setSelected(id); }catch(e){} }
  renderWiki(state.kus[id]);
  hydrateKU(id);   // 原文段落等重字段不在星球初始负载里(为提速),点开时按需补全
  loadSynthesis(id); // 若该节点已有缓存综述,自动显示(GET,不调 LLM)
}

async function loadSynthesis(id){
  if(client.demo) return;
  const cur=state.synth[id];
  if(cur && cur.content) return;                 // 本会话已加载过
  try{
    const r=await client._get('/knowledge/ku/'+encodeURIComponent(id)+'/synthesis');
    if(r && r.has_cache){
      state.synth[id]={content:r.content,model:r.model,generated_at:r.generated_at,stale:false};
      if(state.selected===id) renderWiki(state.kus[id]);
    }
  }catch(e){ /* 无缓存/出错都静默,维持"生成"按钮 */ }
}

async function genSynthesis(id, force){
  if(client.demo){ toast('演示模式无法生成综述,请先连接后端','err'); return; }
  state.synth[id]='loading';
  if(state.selected===id) renderWiki(state.kus[id]);
  try{
    const r=await client._post('/knowledge/ku/'+encodeURIComponent(id)+'/synthesis'+(force?'?force=true':''), {});
    state.synth[id]={content:r.content,model:r.model,generated_at:r.generated_at,stale:!!r.stale};
  }catch(e){
    state.synth[id]=null;
    toast('综述生成失败:'+(e&&e.message?e.message:'请检查已连接后端并配置了 LLM'),'err');
  }
  if(state.selected===id) renderWiki(state.kus[id]);
}
window.genSynthesis=genSynthesis;

function setPathNode(id, which){
  state.pathSel=state.pathSel||{};
  state.pathSel[which]=id; state.pathSel.result='';
  if(state.selected) renderWiki(state.kus[state.selected]);
}
window.setPathNode=setPathNode;
async function findPath(){
  const ps=state.pathSel||{};
  if(!ps.from||!ps.to){ toast('请先设起点①和终点②','err'); return; }
  try{
    const r=await client._get('/search/path?from_id='+encodeURIComponent(ps.from)+'&to_id='+encodeURIComponent(ps.to));
    if(r.reachable && (r.path||[]).length){
      const names=r.path.map(id=>(state.kus[id]&&state.kus[id].name)||id);
      ps.result='最短路径（'+r.hops+' 跳）：'+names.map(esc).join(' → ');
      if(window.planet){ try{ planet.highlight(r.path); }catch(e){} }
    }else{ ps.result='两节点间无连通路径'; }
  }catch(e){ ps.result='路径查询失败（需已连接后端）'; }
  if(state.selected) renderWiki(state.kus[state.selected]);
}
window.findPath=findPath;

async function hydrateKU(id){
  const ku=state.kus[id];
  if(!ku || client.demo) return;                 // 演示数据自带原文,无需拉
  const ex=ku.extra||{};
  if(ex._hydrated || (ex.source_text&&ex.source_text.trim())) return;
  try{
    const full=await client._get('/knowledge/ku/'+encodeURIComponent(id));
    ku.extra=Object.assign({}, ku.extra, full.extra||{}, {_hydrated:true});
    if(state.selected===id) renderWiki(ku);
  }catch(e){ /* 拉不到就维持精简视图,不报错 */ }
}
function clearSelection(){
  state.selected=null;
  if(planet){ try{ planet.setSelected(null); }catch(e){} }
  $('#wiki-body').innerHTML=emptyWiki();
}
window.selectKU = selectKU;
window.clearSelection = clearSelection;

function toggleCollapse(id){ const el=document.getElementById(id); if(el) el.classList.toggle('collapsed'); }
window.toggleCollapse = toggleCollapse;
function emptyWiki(){
  return `<div class="wiki-empty"><svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg><p>点击图谱中的节点<br>查看知识单元详情</p></div>`;
}

function renderWiki(ku){
  if(!ku){ $('#wiki-body').innerHTML=emptyWiki(); return; }
  const c=KU_COLORS[ku.ku_type];
  const ex=ku.extra||{};
  let html=`<div class="wiki-card">`;
  html+=`<div class="wiki-type" style="color:${c};background:${c}1f">
    <span class="lg-dot" style="background:${c};width:8px;height:8px"></span>${KU_LABEL[ku.ku_type]} · ${ku.ku_type}</div>`;
  html+=`<div class="wiki-title">${esc(ku.name)}</div>`;
  if(ku.domain) html+=`<div class="wiki-domain">${esc(ku.domain)}</div>`;

  if(ku.ku_type==='Claim'){
    if(ex.statement) html+=`<div class="wiki-summary">${esc(ex.statement)}</div>`;
    const cs=ex.claim_status||'pending';
    html+=`<div class="wiki-section"><div class="conf-row"><span style="font-size:12px;color:var(--ink-2);font-weight:600">置信度</span>
      <span class="status-badge st-${cs}">${cs}</span></div>
      <div class="conf-bar"><div class="conf-fill" style="width:${(ku.confidence*100).toFixed(0)}%"></div></div>
      <div style="text-align:right;margin-top:4px"><span class="conf-val">${(ku.confidence*100).toFixed(0)}%</span></div></div>`;
  } else if(ku.ku_type==='Fact'){
    if(ex.statement) html+=`<div class="wiki-summary">${esc(ex.statement)}</div>`;
    html+=`<div class="wiki-section"><span class="status-badge st-active">已验证事实 · 置信度 100%</span></div>`;
  } else {
    if(ku.summary) html+=`<div class="wiki-summary">${esc(ku.summary)}</div>`;
  }

  if(!client.demo){
    const sy=state.synth[ku.ku_id];
    html+=`<div class="wiki-section"><h4>AI 综述</h4>`;
    if(sy==='loading'){
      html+=`<div class="synth-loading">正在生成综述…（调用 LLM，请稍候）</div>`;
    }else if(sy && sy.content){
      html+=`<div class="synth-body">${esc(sy.content).replace(/\n/g,'<br>')}</div>`;
      const meta=[sy.model?('模型 '+sy.model):'', sy.stale?'⚠ 知识已更新，建议重新生成':'派生只读视图'].filter(Boolean).join(' · ');
      html+=`<div class="synth-meta">${esc(meta)}</div>
        <button class="synth-btn ghost" onclick="genSynthesis('${ku.ku_id}',true)">重新生成</button>`;
    }else{
      html+=`<button class="synth-btn" onclick="genSynthesis('${ku.ku_id}',false)">✦ 生成 AI 综述</button>
        <div class="synth-hint">把该节点的关联知识用 LLM 组织成连贯词条（只读，不写回知识库）</div>`;
    }
    html+=`</div>`;
  }

  if(!client.demo){
    const ps=state.pathSel||{};
    const nm=id=>id?esc(state.kus[id]&&state.kus[id].name||id):'（未设）';
    html+=`<div class="wiki-section"><h4>路径查询</h4>
      <div class="path-ctl">
        <button class="synth-btn ghost" onclick="setPathNode('${ku.ku_id}','from')">设为起点①</button>
        <button class="synth-btn ghost" onclick="setPathNode('${ku.ku_id}','to')">设为终点②</button>
      </div>
      <div class="path-meta">① ${nm(ps.from)} → ② ${nm(ps.to)}</div>`;
    if(ps.from && ps.to) html+=`<button class="synth-btn" onclick="findPath()">查最短路径</button>`;
    if(ps.result) html+=`<div class="path-result">${ps.result}</div>`;
    html+=`</div>`;
  }

  if(ex.source_text && ex.source_text.trim()){
    html+=`<div class="wiki-section"><h4>原文段落</h4><div class="wiki-source">${esc(ex.source_text)}</div></div>`;
  }
  if(ku.ku_type==='Entity'&&ex.aliases&&ex.aliases.length){
    html+=`<div class="wiki-section"><h4>别名</h4><div class="tag-row">${ex.aliases.map(a=>`<span class="tag">${esc(a)}</span>`).join('')}</div></div>`;
  }

  const nb={Fact:[],Claim:[],Concept:[],Entity:[],Evidence:[]};
  const seen=new Set([ku.ku_id]);
  getRelations(ku.ku_id).forEach(r=>{
    const o=state.kus[r.other];
    if(!o || seen.has(o.ku_id)) return;
    seen.add(o.ku_id);
    if(nb[o.ku_type]) nb[o.ku_type].push(o);
  });
  if(ku.ku_type!=='Evidence'){
    const names=[ku.name, ...((ex.aliases)||[])].filter(n=>n && n.length>=2);
    if(names.length){
      Object.values(state.kus).forEach(k=>{
        if((k.ku_type==='Fact'||k.ku_type==='Claim') && !seen.has(k.ku_id)){
          const st=(k.extra&&k.extra.statement)||k.summary||'';
          if(names.some(n=>st.indexOf(n)>=0)){ seen.add(k.ku_id); nb[k.ku_type].push(k); }
        }
      });
    }
  }
  const stmt=k=>(k.extra&&k.extra.statement)||k.summary||k.name;

  if(ku.ku_type==='Evidence'){
    const isColl=(ku.tags||[]).includes('_collection');
    if(isColl){
      const files=(state.relations||[]).filter(r=>r.type==='PART_OF'&&r.to===ku.ku_id).map(r=>state.kus[r.from]).filter(Boolean);
      html+=`<div class="wiki-section"><h4>合集 · 来源</h4><div class="evid-item"><div class="evid-src">📁 ${esc(ku.name)}</div></div></div>`;
      if(files.length){
        html+=`<div class="wiki-section"><h4>包含文件/章节 ${files.length}（点击进入）</h4><div class="kw-row">`;
        files.forEach(f=>{ const nm=f.name.length>18?f.name.slice(0,17)+'…':f.name; html+=`<span class="kw-chip" onclick="selectKU('${f.ku_id}')">📄 ${esc(nm)}</span>`; });
        html+=`</div></div>`;
      }
    } else {
      html+=`<div class="wiki-section"><h4>来源</h4><div class="evid-item">
        <div class="evid-src">${esc(ex.source||ku.name)}</div>
        <div class="evid-meta">${[ex.author,ex.year,ex.page?'p.'+ex.page:''].filter(Boolean).join(' · ')}</div></div></div>`;
      const inCol=(ku.tags||[]).find(t=>t.startsWith('_incol:'));
      if(inCol){ const col=state.kus[inCol.slice(7)];
        if(col) html+=`<div class="wiki-section"><h4>所属合集</h4><div class="kw-row"><span class="kw-chip" onclick="selectKU('${col.ku_id}')">📁 ${esc(col.name)}</span></div></div>`;
      }
      const deg={}; (state.relations||[]).forEach(r=>{ deg[r.from]=(deg[r.from]||0)+1; deg[r.to]=(deg[r.to]||0)+1; });
      const kws=[...nb.Concept,...nb.Entity,...nb.Fact,...nb.Claim].sort((a,b)=>(deg[b.ku_id]||0)-(deg[a.ku_id]||0));
      if(kws.length){
        html+=`<div class="wiki-section"><h4>本文件关键词 ${kws.length}（点击看词条/原文）</h4><div class="kw-row">`;
        kws.slice(0,40).forEach(k=>{
          const nm = k.name.length>16 ? k.name.slice(0,15)+'…' : k.name;
          html+=`<span class="kw-chip" onclick="selectKU('${k.ku_id}')">${esc(nm)}</span>`;
        });
        html+=`</div></div>`;
      }
    }
  } else {
    if(nb.Fact.length){
      let inner='';
      nb.Fact.slice(0,50).forEach(k=>{ inner+=`<div class="wiki-item fact" onclick="selectKU('${k.ku_id}')">${esc(stmt(k))}</div>`; });
      html+=`<div class="wiki-section"><h4>相关事实 ${nb.Fact.length}</h4><div class="wiki-scroll">${inner}</div></div>`;
    }
    if(nb.Claim.length){
      let inner='';
      nb.Claim.slice(0,50).forEach(k=>{
        const conf=Math.round((k.confidence||0)*100);
        inner+=`<div class="wiki-item claim" onclick="selectKU('${k.ku_id}')"><span class="wiki-conf">${conf}%</span>${esc(stmt(k))}</div>`;
      });
      html+=`<div class="wiki-section"><h4>相关观点 ${nb.Claim.length}</h4><div class="wiki-scroll">${inner}</div></div>`;
    }
    const linked=[...nb.Concept,...nb.Entity];
    if(linked.length){
      html+=`<div class="wiki-section"><h4>关联 ${linked.length}</h4>${kwCluster(linked)}</div>`;
    }
    if(nb.Evidence.length){
      html+=`<div class="wiki-section"><h4>出自</h4><div class="kw-row">`;
      nb.Evidence.forEach(k=>{ html+=`<span class="kw-chip" onclick="selectKU('${k.ku_id}')">📖 ${esc(k.name)}</span>`; });
      html+=`</div></div>`;
    }
  }

  if(ku.tags&&ku.tags.length){
    html+=`<div class="wiki-section"><h4>标签</h4><div class="tag-row">${ku.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></div>`;
  }

  html+=`</div>`;
  $('#wiki-body').innerHTML=html;
}

function kwCluster(list, capN){
  const N=Math.min(list.length, capN||80);
  const cx=130, cy=90, R=82, ga=Math.PI*(3-Math.sqrt(5));
  let dots='';
  for(let i=0;i<N;i++){
    const k=list[i];
    const rr=R*Math.sqrt((i+0.5)/N), th=i*ga;
    const x=(cx+rr*Math.cos(th)).toFixed(1), y=(cy+rr*Math.sin(th)).toFixed(1);
    const col=KU_COLORS[k.ku_type]||'#888';
    dots+=`<circle cx="${x}" cy="${y}" r="4" fill="${col}" onclick="selectKU('${k.ku_id}')"><title>${esc(k.name)}</title></circle>`;
  }
  const more=list.length>N?`（显示 ${N}/${list.length}）`:'';
  return `<div class="kw-cluster"><svg viewBox="0 0 260 180" preserveAspectRatio="xMidYMid meet">${dots}</svg></div>`
       + `<div class="kw-cluster-cap">悬停圆点看名称 · 点击进入${more}</div>`;
}

function getRelations(id){
  const out=[];
  state.relations.forEach(r=>{
    if(r.from===id) out.push({other:r.to,type:r.type,dir:'out'});
    else if(r.to===id) out.push({other:r.from,type:r.type,dir:'in'});
  });
  return out;
}

function renderAbilities(){
  const body=$('#ability-body');
  body.innerHTML=state.abilities.map(a=>{
    const covRows=Object.entries(a.coverage).map(([k,v])=>{
      const pct=(v*100).toFixed(0);
      const low=v<0.5;
      return `<div class="ac-cov-row">
        <span class="ac-cov-label" title="${k}">${k}</span>
        <span class="ac-cov-track"><span class="ac-cov-bar" style="width:${pct}%;${low?'background:var(--klein-l)':''}"></span></span>
        <span class="ac-cov-pct" style="${low?'color:var(--ink-3)':''}">${pct}%</span></div>`;
    }).join('');
    const ready=a.can_compile;
    return `<div class="ability-card">
      <div class="ac-head">
        <div><div class="ac-name">${esc(a.display_name)}</div><div class="ac-domain">${esc(a.domain||'')}</div></div>
        <div class="ac-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg></div>
      </div>
      <div class="ac-cov-list">${covRows}</div>
      <div class="ac-foot">
        <span class="ac-status ${ready?'ac-ready':'ac-locked'}">${ready?'✓ 可编译':'覆盖不足'}</span>
        <button class="btn btn-sm btn-compile" ${ready?'':'disabled'} data-ability="${a.ability_key}">
          ${a.compiled?'重新编译':'编译'}</button>
      </div>
    </div>`;
  }).join('')||'<div style="color:var(--ink-3);font-size:13px;padding:20px">暂无可编译能力</div>';

  body.querySelectorAll('.btn-compile').forEach(b=>{
    b.onclick=()=>compileAbility(b.dataset.ability,b);
  });
}

async function compileAbility(key,btn){
  const a=state.abilities.find(x=>x.ability_key===key);
  btn.disabled=true; btn.innerHTML='<span class="spinner"></span>';
  if(client.demo){
    await new Promise(r=>setTimeout(r,800));
    toast(`已编译能力包 ${a.display_name}.hkap`,'ok');
    btn.disabled=false; btn.textContent='重新编译'; a.compiled=true;
    return;
  }
  try{
    await client._post(`/abilities/${key}/compile`,{});
    toast(`已编译能力包 ${a.display_name}.hkap`,'ok');
    btn.disabled=false; btn.textContent='重新编译';
  }catch(e){
    if(e.status===422){
      const miss=(e.detail&&e.detail.missing_skills)||[];
      toast(`覆盖度不足，还缺：${miss.slice(0,3).join('、')}`,'err');
    }else toast('编译失败','err');
    btn.disabled=false; btn.textContent='编译';
  }
}

function renderConflicts(){
  const open=state.conflicts.filter(c=>c.status==='open');
  const body=$('#conflict-body');
  if(!open.length){
    body.innerHTML=`<div class="conflict-empty">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/></svg>
      <p style="font-size:12.5px">暂无待裁决冲突<br><span style="color:var(--ink-3);font-size:11px">知识库观点一致</span></p></div>`;
    return;
  }
  body.innerHTML=open.map(c=>{
    const a=state.kus[c.claim_a_id], b=state.kus[c.claim_b_id];
    if(!a||!b)return'';
    const pick=state.conflictPick[c.conflict_id];
    return `<div class="conflict-card">
      <div class="cc-head"><span class="cc-domain">${esc(c.domain||'')}</span>
        <span style="font-size:10px;color:var(--ink-3);font-weight:600;margin-left:auto">${c.resolution_strategy}</span></div>
      <div class="cc-claim ${pick===c.claim_a_id?'picked':''}" onclick="pickClaim('${c.conflict_id}','${c.claim_a_id}')">
        <div class="cc-claim-txt">${esc((a.extra&&a.extra.statement)||a.name)}</div>
        <div class="cc-claim-conf">置信度 ${(a.confidence*100).toFixed(0)}%</div></div>
      <div class="cc-vs">对立</div>
      <div class="cc-claim ${pick===c.claim_b_id?'picked':''}" onclick="pickClaim('${c.conflict_id}','${c.claim_b_id}')">
        <div class="cc-claim-txt">${esc((b.extra&&b.extra.statement)||b.name)}</div>
        <div class="cc-claim-conf">置信度 ${(b.confidence*100).toFixed(0)}%</div></div>
      <div class="cc-resolve">
        <button class="btn btn-sm btn-compile" ${pick?'':'disabled'} onclick="resolveConflict('${c.conflict_id}')">采纳所选观点</button>
        <button class="btn btn-sm btn-ghost" onclick="dismissConflict('${c.conflict_id}')" title="两者并不互斥：关闭卡片，双方观点都保留">误判</button>
      </div>
    </div>`;
  }).join('');
}

function pickClaim(cid,clmid){
  state.conflictPick[cid]=clmid;
  renderConflicts();
}

async function resolveConflict(cid){
  const winner=state.conflictPick[cid];
  if(!winner){ toast('请先选择采纳哪个观点','err'); return; }
  if(client.demo){
    await new Promise(r=>setTimeout(r,500));
    const c=state.conflicts.find(x=>x.conflict_id===cid);
    if(c) c.status='resolved';
    const loser=c.claim_a_id===winner?c.claim_b_id:c.claim_a_id;
    if(state.kus[loser]) state.kus[loser].extra.claim_status='superseded';
    if(state.kus[winner]) state.kus[winner].extra.claim_status='active';
    state.relations=state.relations.filter(r=>
      !(r.type==='CONTRADICTS' &&
        ((r.from===loser&&r.to===winner)||(r.from===winner&&r.to===loser))));
    delete state.conflictPick[cid];
    toast('冲突已裁决，落败观点标记为 superseded','ok');
    renderGraph();
    renderPanels();
    return;
  }
  try{
    await client._post(`/conflicts/${cid}/resolve`,{winner_id:winner,note:'UI 裁决',resolved_by:'ui'});
    toast('冲突已裁决','ok');
    delete state.conflictPick[cid];
    await loadData();
  }catch(e){ toast('裁决失败','err'); }
}

async function dismissConflict(cid){
  if(client.demo){
    const c=state.conflicts.find(x=>x.conflict_id===cid);
    if(c) c.status='resolved';
    delete state.conflictPick[cid];
    toast('已按误判关闭，双方观点都保留','ok');
    renderConflicts();
    return;
  }
  try{
    await client._post(`/conflicts/${cid}/dismiss`,{note:'UI 误判关闭',resolved_by:'ui'});
    toast('已按误判关闭，双方观点都保留','ok');
    delete state.conflictPick[cid];
    await loadData();
  }catch(e){ toast('关闭失败','err'); }
}

let searchTimer;
async function doSearch(q){
  if(!q.trim()){ if(window.planet) window.planet.setSelected(null); return; }   // 清空搜索→恢复默认视图(不重建星球)
  if(client.demo){
    const hits=Object.values(state.kus).filter(k=>
      (k.name+k.summary+((k.extra&&k.extra.statement)||'')).toLowerCase().includes(q.toLowerCase()));
    highlightNodes(hits.map(h=>h.ku_id));
    if(hits.length) toast(`找到 ${hits.length} 个匹配（${state.mode}）`);
    else toast('无匹配结果','err');
    return;
  }
  try{
    const res=await client._post('/search',{query:q,mode:state.mode,top_k:15});
    highlightNodes((res.hits||[]).map(h=>h.ku_id));
    toast(`找到 ${(res.hits||[]).length} 个结果`);
  }catch(e){ toast('搜索失败','err'); }
}
function highlightNodes(ids){
  if(window.planet) window.planet.highlight(ids || []);
}

let pickedFiles = [];   // 待摄入文件列表

function fmtSize(b){
  if(b<1024) return b+' B';
  if(b<1024*1024) return (b/1024).toFixed(1)+' KB';
  return (b/1024/1024).toFixed(1)+' MB';
}
const ALLOWED_EXT=['.pdf','.md','.markdown','.txt','.epub','.mobi','.azw3','.azw'];
function extOK(name){ return ALLOWED_EXT.some(e=>name.toLowerCase().endsWith(e)); }

function addFiles(fileList){
  for(const f of fileList){
    if(!extOK(f.name)) continue;                 // 跳过不支持格式
    if(pickedFiles.some(x=>x.name===f.name && x.size===f.size)) continue; // 去重
    pickedFiles.push(f);
  }
  renderFileList();
}

function renderFileList(){
  const box=$('#file-list');
  box.innerHTML='';
  pickedFiles.forEach((f,i)=>{
    const row=document.createElement('div');
    row.className='file-item'; row.dataset.idx=i;
    row.innerHTML=`<span class="fi-name">${f.name}</span>`+
      `<span class="fi-size">${fmtSize(f.size)}</span>`+
      `<button class="fi-del" title="移除"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M18 6 6 18M6 6l12 12"/></svg></button>`;
    row.querySelector('.fi-del').onclick=()=>{ pickedFiles.splice(i,1); renderFileList(); };
    box.appendChild(row);
  });
}

let ingJobs = [];      // {jobId,name,status,processed,total,ku,error}
let _ingPoll = null;

function renderIngestStatus(){
  const el=$('#ingest-status'); if(!el) return;
  const active = ingJobs.filter(j=>j.status!=='done'&&j.status!=='error');
  if(!active.length){ el.innerHTML=''; return; }
  const cur = active[0];
  const pct = cur.total ? Math.round(cur.processed/cur.total*100) : 0;
  const more = active.length>1 ? ` （+${active.length-1} 个排队）` : '';
  el.innerHTML =
    `<div class="ing-line"><span class="spinner"></span> 后台摄入『${cur.name}』`
    + `${cur.total?`${cur.processed}/${cur.total} 段 ${pct}%`:'准备中…'}${more}</div>`
    + `<div class="ing-hint">已转入后台,可关闭本窗或继续添加文件；完成后星球自动刷新</div>`;
}

function startIngestPoll(){
  if(_ingPoll) return;
  _ingPoll = setInterval(pollIngestJobs, 1200);
}
async function pollIngestJobs(){
  const pending = ingJobs.filter(j=>j.status!=='done'&&j.status!=='error');
  if(!pending.length){ clearInterval(_ingPoll); _ingPoll=null; renderIngestStatus(); return; }
  let anyDone=false;
  for(const j of pending){
    try{
      const s = await client._get(`/knowledge/ingest/jobs/${j.jobId}`);
      j.status=s.status; j.processed=s.processed; j.total=s.total; j.ku=s.ku_count; j.error=s.error;
      if(s.status==='done'){
        anyDone=true;
        if((s.ku_count||0) > 0){
          toast(`『${j.name}』摄入完成：${s.ku_count} 个知识单元`,'ok');
        }else{
          toast(`『${j.name}』已上传但未抽取到知识(0 个)。多半是 LLM API Key 无效/未配置——请到「连接设置」重填正确 key 后重传`,'err');
        }
      }
      else if(s.status==='error'){ toast(`『${j.name}』摄入失败：${(s.error||'').slice(0,60)}`,'err'); }
    }catch(e){ /* 网络抖动,下轮再试 */ }
  }
  renderIngestStatus();
  if(anyDone) loadData();                 // 有完成的就刷新星球
  ingJobs = ingJobs.filter(j=> j.status!=='done' && j.status!=='error');
  if(!ingJobs.length){ clearInterval(_ingPoll); _ingPoll=null; renderIngestStatus(); }
}

async function submitIngest(){
  const text=$('#ingest-text').value.trim();
  const url=$('#ingest-url').value.trim();
  const title=$('#ingest-title').value.trim();
  const domain=$('#ingest-domain').value;
  const hasFiles=pickedFiles.length>0;

  if(!text && !url && !hasFiles){ toast('请输入文本、网址或选择文件','err'); return; }

  const btn=$('#btn-ingest-submit');
  btn.disabled=true; btn.innerHTML='<span class="spinner"></span> 摄入中';

  if(client.demo){
    btn.disabled=false; btn.textContent='开始摄入';
    toast('未连接后端,无法摄入。请点左下「连接设置」连接 hkc-api 后再试','err');
    closeModal('#ingest-modal');
    openModal('#settings-modal');
    return;
  }

  if(text){
    btn.disabled=true; btn.innerHTML='<span class="spinner"></span> 摄入文本';
    try{
      const res=await client._post('/knowledge/ingest/text',{text,source_title:title,domain});
      const ku=res.ku_count||0;
      toast(ku>0?`文本摄入完成：${ku} 个知识单元`:'文本已摄入,但未抽取到知识(内容太短或模型未配置好)', ku>0?'ok':'err');
      $('#ingest-text').value='';
      if(ku>0) await loadData();
    }catch(e){ toast('文本摄入失败','err'); }
    btn.disabled=false; btn.textContent='开始摄入';
  }

  if(url){
    btn.disabled=true; btn.innerHTML='<span class="spinner"></span> 抓取网页';
    try{
      const res=await client._post('/knowledge/ingest/url',{url,source_title:title,domain});
      const ku=res.ku_count||0;
      toast(ku>0?`网页摄入完成：${ku} 个知识单元`:'网页已抓取,但未抽取到知识(正文太短/抓取失败/模型未配置)', ku>0?'ok':'err');
      $('#ingest-url').value='';
      if(ku>0) await loadData();
    }catch(e){
      const reason=(e&&e.detail)?String(e.detail).slice(0,60):'抓取或摄入失败';
      toast(`网页摄入失败：${reason}`,'err');
    }
    btn.disabled=false; btn.textContent='开始摄入';
  }

  if(hasFiles){
    btn.disabled=true; btn.innerHTML='<span class="spinner"></span> 提交中';
    let submitted=0;
    for(const f of pickedFiles.slice()){
      try{
        const fd=new FormData();
        fd.append('file', f, f.name);
        fd.append('source_title', title);   // 留空时后端用文件名
        fd.append('domain', domain);
        const res=await client._postForm('/knowledge/ingest/file/async', fd);
        ingJobs.push({jobId:res.job_id, name:f.name, status:'queued', processed:0, total:0, ku:0, error:''});
        submitted++;
      }catch(e){
        const reason=(e&&e.detail)?String(e.detail).slice(0,50):'提交失败';
        toast(`『${f.name}』提交失败：${reason}`,'err');
      }
    }
    btn.disabled=false; btn.textContent='开始摄入';
    if(submitted>0){
      pickedFiles=[]; renderFileList();     // 已转后台,清空选择区
      $('#ingest-title').value='';
      toast(`${submitted} 个文件已转入后台摄入,可继续添加或关闭窗口；完成后自动刷新`,'ok');
      startIngestPoll();
      renderIngestStatus();
    }
  }
}

function openModal(s){
  $(s).classList.add('show');
  if(s === '#ingest-modal') stopRotation();
}
function closeModal(s){
  $(s).classList.remove('show');
  if(s === '#ingest-modal') startRotation();
}

async function openBooks(){
  if(client.demo){ toast('未连接后端,无法管理书籍','err'); openModal('#settings-modal'); return; }
  openModal('#books-modal');
  await loadBooks();
}
async function loadBooks(){
  const box=$('#books-list');
  box.innerHTML='<div style="font-size:13px;color:var(--ink-2);text-align:center;padding:16px">加载中…</div>';
  try{
    const r=await client._get('/knowledge/sources');
    const list=(r&&r.sources)||[];
    if(!list.length){ box.innerHTML='<div style="font-size:13px;color:var(--ink-2);text-align:center;padding:16px">暂无已摄入的书籍</div>'; return; }
    box.innerHTML='';
    for(const b of list){
      const row=document.createElement('div');
      row.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;margin-bottom:8px;';
      const name=document.createElement('span');
      name.style.cssText='font-size:13px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;';
      name.textContent=(b.name||b.source)+(b.year?` (${b.year})`:'');
      name.title=b.source;
      const del=document.createElement('button');
      del.className='btn btn-ghost btn-sm';
      del.textContent='删除';
      del.onclick=()=>deleteBook(b.source, name.textContent);
      row.appendChild(name); row.appendChild(del);
      box.appendChild(row);
    }
  }catch(e){ box.innerHTML='<div style="font-size:13px;color:var(--ink-2);text-align:center;padding:16px">加载失败：'+(e.status||e||'')+'</div>'; }
}
async function deleteBook(source, label){
  if(!confirm(`确定删除「${label}」吗？\n\n将移除该书及其独占知识；与其他书共享的知识只去掉该书引用。此操作不可撤销。`)) return;
  try{
    const r=await client._post('/knowledge/sources/delete',{source});
    toast(`已删除「${label}」：${r.deleted_kus} 个知识单元`,'ok');
    await loadBooks();
    await loadData();
  }catch(e){ toast('删除失败：'+(e.detail||e.status||e||''),'err'); }
}

function setConn(){
  const p=$('#conn-pill'), t=$('#conn-text');
  if(client.demo){ p.className='conn-pill demo'; t.textContent='演示模式'; }
  else{ p.className='conn-pill live'; t.textContent='已连接'; }
}

const CONN_KEY='hkc_conn_v1';
function _connSnapshot(){
  return {
    url:$('#api-url').value.trim(),
    apiKey:$('#api-key').value.trim(),
    provider:$('#llm-provider').value,
    key:$('#llm-key').value.trim(),
    model:$('#llm-model').value.trim(),
    baseUrl:$('#llm-baseurl').value.trim(),
  };
}
function saveConnSettings(){ try{ localStorage.setItem(CONN_KEY, JSON.stringify(_connSnapshot())); }catch(e){} }
function loadConnSettings(){ try{ return JSON.parse(localStorage.getItem(CONN_KEY)||'null'); }catch(e){ return null; } }
function fillConnFields(s){
  if(!s) return;
  if(s.url!=null)     $('#api-url').value=s.url;
  if(s.apiKey!=null){ $('#api-key').value=s.apiKey; client.apiKey=s.apiKey; }
  if(s.provider)      $('#llm-provider').value=s.provider;
  if(s.key!=null)     $('#llm-key').value=s.key;
  if(s.model!=null)   $('#llm-model').value=s.model;
  if(s.baseUrl!=null) $('#llm-baseurl').value=s.baseUrl;
  _syncLLMFields();
}

async function tryConnect(){
  const url=$('#api-url').value.trim();
  if(!url){ toast('请输入 API 地址','err'); return; }
  client.apiKey=$('#api-key').value.trim();   // 鉴权 key 须在 /stats、/graph 等请求前就位
  const btn=$('#btn-connect'); btn.disabled=true; btn.innerHTML='<span class="spinner"></span> 连接中';
  const ok=await client.connect(url);
  if(ok){
    const provider=$('#llm-provider').value;
    const key=$('#llm-key').value.trim();
    const model=$('#llm-model').value.trim();
    const baseUrl=$('#llm-baseurl').value.trim();
    if(key){
      try{
        await client._post('/config/llm', {
          provider, api_key:key,
          model: model||null,
          base_url: baseUrl||null,
        });
        toast(`已连接，知识抽取模型：${provider}`,'ok');
      }catch(e){
        toast('已连接，但模型配置失败：'+(e.detail||e.status||e),'err');
      }
    }else{
      toast('已连接到 HKC API','ok');
    }
    saveConnSettings();   // 连接成功才记住设置(含 key),供刷新后自动重连
    btn.disabled=false; btn.textContent='连接';
    setConn(); closeModal('#settings-modal'); await loadData();
  }else{
    btn.disabled=false; btn.textContent='连接';
    toast('连接失败，请检查 hkc-api 是否运行','err');
  }
}

function _syncLLMFields(){
  const p=$('#llm-provider').value;
  const modelInput=$('#llm-model');
  const baseField=$('#llm-baseurl-field');
  const baseInput=$('#llm-baseurl');
  const defaults={ deepseek:'deepseek-chat', anthropic:'claude-sonnet-4-6', openai:'gpt-4o-mini', 'openai-compatible':'(自定义模型名)' };
  modelInput.placeholder=defaults[p]||'';
  baseField.style.display=(p==='openai-compatible')?'':'none';
  if(p==='openai-compatible' && !baseInput.value.trim()){
    baseInput.value='http://localhost:11434/v1';
  }
}

function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m])); }

$('#modes').addEventListener('click',e=>{
  const b=e.target.closest('.mode-btn'); if(!b)return;
  $$('.mode-btn').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); state.mode=b.dataset.mode;
});
$('#search-input').addEventListener('input',e=>{
  clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>doSearch(e.target.value),450);
});
$('#search-input').addEventListener('keydown',e=>{ if(e.key==='Enter') doSearch(e.target.value); });

$('#btn-ingest').onclick=()=>openModal('#ingest-modal');
$('#btn-books').onclick=openBooks;
$('#btn-settings').onclick=()=>openModal('#settings-modal');
$('#btn-pick-files').onclick=()=>$('#ingest-files').click();
$('#btn-pick-folder').onclick=()=>$('#ingest-folder').click();
$('#ingest-files').addEventListener('change',e=>{ addFiles(e.target.files); e.target.value=''; });
$('#ingest-folder').addEventListener('change',e=>{ addFiles(e.target.files); e.target.value=''; });
$('#btn-ingest-submit').onclick=submitIngest;
$('#btn-connect').onclick=tryConnect;
$('#llm-provider').addEventListener('change', _syncLLMFields);
_syncLLMFields();  // 初始化字段状态
$('#btn-use-demo').onclick=()=>{ client.useDemo(); setConn(); closeModal('#settings-modal'); loadData(); toast('已切换到演示数据'); };
$('#btn-fit').onclick=()=>window._fitGraph&&window._fitGraph();
$$('[data-close]').forEach(b=>b.onclick=()=>{ closeModal('#ingest-modal'); closeModal('#settings-modal'); closeModal('#books-modal'); });
$$('.modal-bg').forEach(m=>m.addEventListener('click',e=>{ if(e.target===m) closeModal('#'+m.id); }));

async function startup(){
  const saved=loadConnSettings();
  if(saved && saved.url){
    fillConnFields(saved);
    client.apiKey=saved.apiKey||'';   // 鉴权 key 只能取保存的 apiKey,绝不混用模型的 key
    const ok=await client.connect(saved.url);
    if(ok){
      setConn(); await loadData(); return;
    }
    toast('已保存的后端未连上，回退演示模式（设置已回填，可在「连接设置」重连）','err');
  }
  if(await client.connect('')){ setConn(); await loadData(); return; }
  setConn();
  loadData();
}
startup();
