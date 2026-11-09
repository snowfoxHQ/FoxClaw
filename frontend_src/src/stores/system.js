/* /api/system/info 的共享缓存:顶栏要 version / update,设置页也要,别各拉一次。
   并发调用复用同一个 in-flight promise。 */
import { reactive } from 'vue'
import { getJSON } from '../api.js'

export const system = reactive({
  version: '',
  update: null,   // { enabled, current, latest, update_available, checked_at, error }
  loaded: false,
})

let inflight = null

export function loadSystemInfo(force = false) {
  if (system.loaded && !force) return Promise.resolve(system)
  if (inflight) return inflight
  inflight = getJSON('/system/info')
    .then((d) => {
      system.version = d.version || ''
      system.update = d.update || null
      system.loaded = true
      return system
    })
    .catch(() => system)      // 拿不到就维持旧值:顶栏不显示版本,不显示更新
    .finally(() => { inflight = null })
  return inflight
}
