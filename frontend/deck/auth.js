(function(){
  if(window.__foxclawFetchPatched) return;
  const _fetch=window.fetch.bind(window);
  let _authHinted=false;
  window.fetch=function(url,opts){
    try{
      const key=localStorage.getItem('foxclawApiKey');
      const u=String(url||'');
      if(key && (u.indexOf('/api')>=0 || u.indexOf('/knowledge')>=0 || u.indexOf('/stats')>=0)){
        opts=opts||{}; opts.headers=Object.assign({}, opts.headers, {'X-API-Key':key});
      }
    }catch(e){}
    const p=_fetch(url,opts);
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
