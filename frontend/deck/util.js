/* 安全:HTML 转义 —— 模型输出/用户输入/外部数据写入 innerHTML 前必须转义,防 XSS
   (模型输出不可信,可能被提示注入产出 <img onerror> 等)。*/
export function escapeHtml(s){
  return String(s==null?'':s).replace(/[&<>"']/g,c=>(
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
