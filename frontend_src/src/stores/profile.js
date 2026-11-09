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
