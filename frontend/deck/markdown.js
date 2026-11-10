import { escapeHtml } from './util.js';

function _safeUrl(u){ u=(u||'').trim(); return (/^https?:\/\//i.test(u)||/^mailto:/i.test(u))?u:''; }
function _safeImgUrl(u){ u=(u||'').trim(); return (/^https?:\/\//i.test(u)||/^data:image\/(png|jpe?g|gif|webp);base64,/i.test(u))?u:''; }
function _mdInline(s){
  s=s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g,(m,alt,url)=>{ const u=_safeImgUrl(url); return u?`<img class="cc-img" src="${u}" alt="${alt}" loading="lazy">`:m; });
  s=s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,(m,txt,url)=>{ const u=_safeUrl(url); return u?`<a href="${u}" target="_blank" rel="noopener noreferrer">${txt}</a>`:m; });
  s=s.replace(/(^|\s)(https?:\/\/[^\s<]+?\.(?:png|jpe?g|gif|webp))(?=\s|$)/gi,(m,pre,url)=>`${pre}<img class="cc-img" src="${url}" alt="" loading="lazy">`);
  s=s.replace(/(^|\s)(https?:\/\/[^\s<]+?\.(?:mp3|wav|ogg|m4a))(?=\s|$)/gi,(m,pre,url)=>`${pre}<audio class="cc-media" controls src="${url}"></audio>`);
  s=s.replace(/(^|\s)(https?:\/\/[^\s<]+?\.(?:mp4|webm|mov))(?=\s|$)/gi,(m,pre,url)=>`${pre}<video class="cc-media" controls src="${url}"></video>`);
  s=s.replace(/`([^`]+)`/g,(m,c)=>`<code class="cc-ic">${c}</code>`);
  s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/__([^_]+)__/g,'<strong>$1</strong>');
  s=s.replace(/(^|[^*])\*([^*\n]+)\*/g,'$1<em>$2</em>').replace(/(^|[^_\w])_([^_\n]+)_/g,'$1<em>$2</em>');
  return s;
}
export function renderMarkdown(src){
  if(!src) return '';
  const blocks=[];
  src=src.replace(/```(\w*)\n?([\s\S]*?)```/g,(m,lang,code)=>{ blocks.push(`<pre class="cc-code"><code>${escapeHtml(code.replace(/\n$/,''))}</code></pre>`); return `B${blocks.length-1}`; });
  const t=escapeHtml(src);              // 整体转义:此后所有原始 <>&" 都是纯文本
  const lines=t.split('\n'); const out=[]; let inUl=false, inOl=false;
  const closeLists=()=>{ if(inUl){out.push('</ul>');inUl=false;} if(inOl){out.push('</ol>');inOl=false;} };
  for(const raw of lines){
    const line=raw, tr=line.trim(); let m;
    if(/^B\d+$/.test(tr)){ closeLists(); out.push(tr); continue; }
    if((m=line.match(/^(#{1,6})\s+(.*)$/))){ closeLists(); const n=m[1].length; out.push(`<h${n} class="cc-h">${_mdInline(m[2])}</h${n}>`); continue; }
    if(/^\s*([-*_])\s*\1\s*\1\s*$/.test(line)){ closeLists(); out.push('<hr class="cc-hr">'); continue; }
    if((m=line.match(/^\s*&gt;\s?(.*)$/))){ closeLists(); out.push(`<blockquote class="cc-bq">${_mdInline(m[1])}</blockquote>`); continue; }
    if((m=line.match(/^\s*[-*+]\s+(.*)$/))){ if(inOl){out.push('</ol>');inOl=false;} if(!inUl){out.push('<ul class="cc-ul">');inUl=true;} out.push(`<li>${_mdInline(m[1])}</li>`); continue; }
    if((m=line.match(/^\s*\d+\.\s+(.*)$/))){ if(inUl){out.push('</ul>');inUl=false;} if(!inOl){out.push('<ol class="cc-ol">');inOl=true;} out.push(`<li>${_mdInline(m[1])}</li>`); continue; }
    if(tr===''){ closeLists(); continue; }
    closeLists(); out.push(`<p class="cc-p">${_mdInline(line)}</p>`);
  }
  closeLists();
  let html=out.join('');
  html=html.replace(/B(\d+)/g,(m,i)=>blocks[+i]||'');
  return html;
}
/* 把 markdown 结果渲染进回复区(带 .cc-md 容器,关掉 pre-wrap)*/
export function renderReply(md){
  const rp=document.getElementById('ccReply');
  rp.innerHTML='<div class="cc-md">'+renderMarkdown(md)+'</div>';
  rp.scrollTop=rp.scrollHeight;
}
