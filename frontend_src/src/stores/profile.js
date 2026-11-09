/* 用户档案:目前只有「称呼」一个字段。
   后端存的是一条 preference(每轮注入、跨会话),所以这里只是 /api/profile 的薄封装,
   不做本地持久化 —— 换浏览器打开也应该还是同一个称呼。 */
import { reactive } from 'vue'
import { getJSON, putJSON } from '../api.js'

export const profile = reactive({
  nickname: '',
  loaded: false,
  saving: false,
})

export async function loadProfile(force = false) {
  if (profile.loaded && !force) return profile
  try {
    const d = await getJSON('/profile')
    profile.nickname = d.nickname || ''
    profile.loaded = true
  } catch {
    /* 后端没起来:保持 loaded=false,首次引导不显示(不拿一个假的空状态骚扰用户) */
  }
  return profile
}

export async function saveNickname(nick) {
  profile.saving = true
  try {
    const d = await putJSON('/profile', { nickname: nick || '' })
    profile.nickname = d.nickname || ''
    profile.loaded = true
    return profile.nickname
  } finally {
    profile.saving = false
  }
}
