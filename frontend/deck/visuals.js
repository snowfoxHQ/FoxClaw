const _lowpower = (() => {
  try {
    if (new URLSearchParams(location.search).get('visual') === '1') return false;
    if (localStorage.getItem('foxclaw_visual') === '1') return false;
    if (localStorage.getItem('foxclaw_lowpower') === '0') return false;   // 兼容旧版开关
    if (new URLSearchParams(location.search).get('low') === '1') return true;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return true;
    if (navigator.connection && navigator.connection.saveData) return true;
  } catch {}
  return true;   // ← 默认:不启动(保护 GPU + 启动秒开)
})();
window._foxclawLowpower = _lowpower;   // 暴露给其他模块读
window.foxclawVisual = {
  isOn: () => !_lowpower,
  on()  { try { localStorage.setItem('foxclaw_visual', '1'); } catch{} location.reload(); },
  off() { try { localStorage.removeItem('foxclaw_visual'); localStorage.setItem('foxclaw_lowpower', '1'); } catch{} location.reload(); },
  toggle() { this.isOn() ? this.off() : this.on(); },
};
if (_lowpower) console.info('[foxclaw] 球体特效已关闭(默认) — 想看动画:控制台跑 foxclawVisual.on() 或 URL 加 ?visual=1');
function _lpThrottle(fn, fps) {       // 30fps 节流(不关,但降频)— 关闭模式直接 noop
  if (_lowpower) return () => {};
  let last = 0; const gap = 1000 / fps;
  return function(t) { if (t - last >= gap) { last = t; fn(t); } };
}

(function(){
  if (_lowpower) return;        // 省电:不开中央全息核心(520 球面点 + 经纬弧 + 渐变 60fps)
  const c=document.getElementById('coreCanvas'), x=c.getContext('2d');
  function size(){const r=c.parentElement.getBoundingClientRect();c.width=r.width;c.height=r.height;}
  size(); window.addEventListener('resize',size);
  const N=520, pts=[];
  for(let i=0;i<N;i++){const t=Math.acos(2*Math.random()-1),p=Math.random()*Math.PI*2;
    pts.push({t,p});}
  let rot=0;
  function loop(){
    x.clearRect(0,0,c.width,c.height);
    const cx=c.width/2, cy=c.height/2, R=Math.min(cx,cy)*0.62;
    rot+=0.0024;
    for(let k=0;k<3;k++){
      x.beginPath();
      x.ellipse(cx,cy+R*0.92,R*(1.05+k*0.18),R*(0.26+k*0.05),0,0,Math.PI*2);
      x.strokeStyle=`rgba(0,217,255,${0.18-k*0.04})`; x.lineWidth=2; x.stroke();
    }
    for(const pt of pts){
      const t=pt.t, p=pt.p+rot;
      const xx=Math.sin(t)*Math.cos(p), yy=Math.cos(t), zz=Math.sin(t)*Math.sin(p);
      const sx=cx+xx*R, sy=cy+yy*R;
      const depth=(zz+1)/2;
      x.beginPath(); x.arc(sx,sy,depth*1.6+0.3,0,Math.PI*2);
      const hue=depth>0.5?'0,217,255':'255,194,75';
      x.fillStyle=`rgba(${hue},${depth*0.85+0.1})`; x.fill();
    }
    x.strokeStyle='rgba(41,243,255,.12)';x.lineWidth=1;
    for(let m=0;m<6;m++){
      x.beginPath();
      for(let a=0;a<=64;a++){const t=a/64*Math.PI, p=m/6*Math.PI*2+rot;
        const xx=Math.sin(t)*Math.cos(p),yy=Math.cos(t),zz=Math.sin(t)*Math.sin(p);
        const sx=cx+xx*R,sy=cy+yy*R; a?x.lineTo(sx,sy):x.moveTo(sx,sy);}
      x.stroke();
    }
    const g=x.createRadialGradient(cx,cy,0,cx,cy,R*0.5);
    g.addColorStop(0,'rgba(0,217,255,.28)');g.addColorStop(1,'transparent');
    x.fillStyle=g;x.beginPath();x.arc(cx,cy,R*0.5,0,Math.PI*2);x.fill();
    requestAnimationFrame(loop);
  }
  loop();
})();

(function(){
  if (_lowpower) return;        // 省电:不开记忆向量空间动画
  const c=document.getElementById('memCanvas'); if(!c) return;   // 右栏改版后无此装饰画布 → 静默跳过
  const x=c.getContext('2d');
  function size(){const r=c.parentElement.getBoundingClientRect();c.width=r.width;c.height=r.height;}
  size();window.addEventListener('resize',size);
  const nodes=[];for(let i=0;i<46;i++)nodes.push({x:Math.random(),y:Math.random(),z:Math.random(),s:Math.random()*2+1});
  let rot=0;
  function loop(){
    x.clearRect(0,0,c.width,c.height);rot+=0.005;
    const cx=c.width/2,cy=c.height/2;
    nodes.forEach(n=>{
      const a=n.x*Math.PI*2+rot, rad=(n.y*0.4+0.15)*Math.min(cx,cy);
      const px=cx+Math.cos(a)*rad, py=cy+Math.sin(a)*rad*0.6+(n.z-0.5)*40;
      n._x=px;n._y=py;
      x.beginPath();x.arc(px,py,n.s,0,Math.PI*2);
      x.fillStyle=n.z>0.5?'rgba(255,194,75,.9)':'rgba(0,217,255,.85)';x.fill();
    });
    x.strokeStyle='rgba(255,194,75,.12)';x.lineWidth=.5;
    for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
      const dx=nodes[i]._x-nodes[j]._x,dy=nodes[i]._y-nodes[j]._y,d=Math.hypot(dx,dy);
      if(d<46){x.beginPath();x.moveTo(nodes[i]._x,nodes[i]._y);x.lineTo(nodes[j]._x,nodes[j]._y);x.stroke();}
    }
    requestAnimationFrame(loop);
  }
  loop();
})();

(function(){
  if (_lowpower) return;        // 省电:不开中枢数据流 + 2.2s 粒子 emit
  const cv=document.getElementById('streamCanvas'); if(!cv)return;
  const ctx=cv.getContext('2d');
  const center=document.querySelector('.center');
  function resize(){ const r=center.getBoundingClientRect(); cv.width=r.width; cv.height=r.height; }
  resize(); window.addEventListener('resize',resize);
  function pts(){
    const cr=center.getBoundingClientRect();
    const core=document.querySelector('.corewrap');
    const coreC=core?core.getBoundingClientRect():cr;
    const origin={x:coreC.left-cr.left+coreC.width/2, y:coreC.top-cr.top+coreC.height/2};
    const targets=[...document.querySelectorAll('.acard')].map(a=>{
      const b=a.getBoundingClientRect();
      return {x:b.left-cr.left+b.width/2, y:b.top-cr.top+b.height/2,
        color:getComputedStyle(a).getPropertyValue('--ac').trim()||'#00d9ff'};
    });
    return {origin,targets};
  }
  let particles=[];
  function emit(targetAgent){
    const {origin,targets}=pts();
    if(!targets.length)return;
    let t;
    if(targetAgent){
      const cards=[...document.querySelectorAll('.acard')];
      const idx=cards.findIndex(c=>c.dataset.agent===targetAgent);
      t=idx>=0?targets[idx]:targets[Math.floor(Math.random()*targets.length)];
    }else{
      t=targets[Math.floor(Math.random()*targets.length)];
    }
    const reverse=targetAgent?false:Math.random()<0.4;  // 发任务恒为 核心→Agent
    particles.push({
      sx:reverse?t.x:origin.x, sy:reverse?t.y:origin.y,
      ex:reverse?origin.x:t.x, ey:reverse?origin.y:t.y,
      x:reverse?t.x:origin.x, y:reverse?t.y:origin.y,
      p:0, speed:0.012+Math.random()*0.01, color:t.color, trail:[]
    });
  }
  window._emitStream=emit;  // 暴露给发任务联动
  function ease(t){ return t<.5?2*t*t:1-Math.pow(-2*t+2,2)/2; }
  function hexA(c,a){
    c=c||'#00d9ff';
    if(c.startsWith('rgb'))return c.replace(/rgba?\(([^)]+)\)/,(m,p)=>{const v=p.split(',').slice(0,3).join(','); return `rgba(${v},${a})`;});
    let h=c.replace('#',''); if(h.length===3)h=h.split('').map(x=>x+x).join('');
    const n=parseInt(h,16); return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`;
  }
  function loop(){
    ctx.clearRect(0,0,cv.width,cv.height);
    particles.forEach(pt=>{
      pt.p+=pt.speed;
      pt.x=pt.sx+(pt.ex-pt.sx)*ease(pt.p);
      pt.y=pt.sy+(pt.ey-pt.sy)*ease(pt.p);
      pt.trail.push({x:pt.x,y:pt.y}); if(pt.trail.length>14)pt.trail.shift();
      pt.trail.forEach((tp,i)=>{
        const a=(i/pt.trail.length)*(1-pt.p)*0.7;
        ctx.beginPath(); ctx.arc(tp.x,tp.y,1.6,0,7);
        ctx.fillStyle=hexA(pt.color,a); ctx.fill();
      });
      ctx.beginPath(); ctx.arc(pt.x,pt.y,2.4,0,7);
      ctx.fillStyle=hexA(pt.color,(1-pt.p)*0.9); ctx.shadowBlur=8; ctx.shadowColor=pt.color; ctx.fill(); ctx.shadowBlur=0;
    });
    particles=particles.filter(pt=>pt.p<1);
    requestAnimationFrame(loop);
  }
  loop();
  setInterval(emit, 2200);
  setTimeout(emit, 800);
})();

(function(){
  if (_lowpower) return;        // 省电:不开系统活动波形
  const c=document.getElementById('actCanvas'); if(!c)return;
  const x=c.getContext('2d');
  function size(){const r=c.parentElement.getBoundingClientRect();c.width=r.width;c.height=90;}
  size();window.addEventListener('resize',size);
  let t=0;
  function loop(){
    x.clearRect(0,0,c.width,c.height);t+=0.03;
    if(window.actPulse>0)window.actPulse*=0.96;
    const boost=1+(window.actPulse||0);
    [[ '0,217,255',0],['255,194,75',1.5]].forEach(([col,ph])=>{
      x.beginPath();
      for(let i=0;i<=c.width;i+=4){
        const y=c.height/2+Math.sin(i*0.025+t+ph)*18*boost*Math.sin(i*0.006+t*0.5)+
          Math.sin(i*0.05+t*1.3+ph)*8*boost;
        i?x.lineTo(i,y):x.moveTo(i,y);
      }
      x.strokeStyle=`rgba(${col},.7)`;x.lineWidth=2;x.shadowBlur=8;x.shadowColor=`rgba(${col},.6)`;x.stroke();x.shadowBlur=0;
    });
    requestAnimationFrame(loop);
  }
  loop();
})();
