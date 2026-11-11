import { reactive } from 'vue'
import zh from './locales/zh.js'
import en from './locales/en.js'

const DICTS = { zh, en }
const STORE_KEY = 'foxclawLang'

export const LOCALES = [
  { id: 'zh', label: '中文', tag: 'Chinese', htmlLang: 'zh-CN', locale: 'zh-CN' },
  { id: 'en', label: 'English', tag: 'English', htmlLang: 'en', locale: 'en-US' },
]

const stored = (() => {
  try {
    return localStorage.getItem(STORE_KEY)
  } catch {
    return null
  }
})()

export const i18n = reactive({ locale: DICTS[stored] ? stored : 'zh' })

export const currentLocale = () => LOCALES.find((l) => l.id === i18n.locale) || LOCALES[0]

/** 数字/日期格式化用的 BCP-47 标签,跟随界面语言 */
export const numberLocale = () => currentLocale().locale

export function applyLangTag() {
  document.documentElement.lang = currentLocale().htmlLang
}

export function setLocale(id) {
  if (!DICTS[id] || id === i18n.locale) return
  i18n.locale = id
  try {
    localStorage.setItem(STORE_KEY, id)
  } catch {
    /* 隐私模式下写不进去,不影响本次会话 */
  }
  applyLangTag()
}

/** 取文案。读 i18n.locale 使其在模板中被 Vue 追踪,切换语言即时生效。 */
export function t(key, vars) {
  const dict = DICTS[i18n.locale] || DICTS.zh
  let s = dict[key]
  if (s === undefined) s = DICTS.zh[key]
  if (s === undefined) return key
  if (vars) {
    for (const k of Object.keys(vars)) s = s.split(`{${k}}`).join(String(vars[k]))
  }
  return s
}
