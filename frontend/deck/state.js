/* 跨模块共享:URL 常量 + 内核数据缓存(零构建,显式 live-binding 导出) */
const _origin=(location.protocol==='http:'||location.protocol==='https:')?location.origin:'http://localhost:8000';
const API=_origin+'/api';
const WS=_origin.replace(/^http/,'ws')+'/ws/harness';
let _lastKernelData={};   // 缓存各 Agent 最近一次内核数据,供全屏复用
export { _origin, API, WS, _lastKernelData };
