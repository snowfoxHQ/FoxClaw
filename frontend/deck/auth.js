/* 鉴权注入:后端若设了 FOXCLAW_API_KEY,同源 /api、/knowledge 请求需带 X-API-Key。
   从 localStorage.foxclawApiKey 读取并包一层 fetch 自动注入。
   必须最先加载(在 settings.js 等业务模块之前),否则模块顶层提前发出的请求
   (如 settings.js 的 loadModel → /api/model)会漏带 key 被 401。 */
(function(){
  if(window.__foxclawFetchPatched) return;
  const _fetch=window.fetch.bind(window);
  let _authHinted=false;
  window.fetch=function(url,opts){
    try{
      const key=localStorage.getItem('foxclawApiKey');
      const u=String(url||'');
      // /knowledge/* 与 /api/* 一样受后端可选鉴权守护,两者都要注入 key
      // /stats 是 HKC 统计数据面,后端已纳入守护,同样需要注入
      if(key && (u.indexOf('/api')>=0 || u.indexOf('/knowledge')>=0 || u.indexOf('/stats')>=0)){
        opts=opts||{}; opts.headers=Object.assign({}, opts.headers, {'X-API-Key':key});
      }
    }catch(e){}
    const p=_fetch(url,opts);
    // 401 检测:后端开了鉴权但本机浏览器没配 key(或 key 错)→ 弹提示条引导(只弹一次)
    try{
      p.then(r=>{
        if(r && r.status===401 && !_authHinted){
          _authHinted=true;
          if(typeof window.__foxclawAuthHint==='function') window.__foxclawAuthHint();
        }
      }).catch(()=>{});
    }catch(e){}
    return p;
  };
  window.__foxclawFetchPatched=true;
})();
