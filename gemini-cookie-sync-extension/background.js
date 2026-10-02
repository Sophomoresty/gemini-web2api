/**
 * Gemini Cookie Sync — background service worker (v2)
 *
 * 自动同步：监听 Google 域 cookie 变化 → 防抖收集 → 推送 native host 写 gemini-auth.json。
 *
 * 触发逻辑：
 *   - cookies.onChanged 任何 Google 域 cookie 增删改 → 重置防抖计时器（2.5s）
 *   - 防抖到点 → 收集当前全部 Google cookie + 从打开的 Gemini 标签页抓 xsrf/gemini_bl
 *   - 校验关键字段 → connectNative 推送给 native host
 *   - 也监听周期定时器（每 30 分钟强制刷新一次，防漏）
 */

const HOST_NAME = 'gemini_cookie_sync_host'
const GOOGLE_DOMAINS = ['google.com', 'gemini.google.com', 'accounts.google.com']
const TS_COOKIES = ['__Secure-1PSIDTS', '__Secure-3PSIDTS', 'SIDCC', '__Secure-1PSIDCC']
const FORCE_INTERVAL_MS = 30 * 60 * 1000 // 30 分钟强制推送一次

// ── 状态 ─────────────────────────────────────────────
let debounceTimer = null
let lastPushedHash = '' // 避免重复推送相同内容
let port = null // native host 连接（复用）

// ── cookie 收集（与 popup.js 逻辑一致）────────────────
const EXPORT_ORDER = [
  'SID', 'HSID', 'SSID', 'APISID', 'SAPISID',
  'LSID', 'OSID', 'SIDCC', 'AEC', 'NID', 'COMPASS',
  '__Secure-1PAPISID', '__Secure-1PSID', '__Secure-1PSIDTS', '__Secure-1PSIDCC', '__Secure-1PSIDRTS',
  '__Secure-3PAPISID', '__Secure-3PSID', '__Secure-3PSIDTS', '__Secure-3PSIDCC', '__Secure-3PSIDRTS',
  '__Secure-OSID', '__Host-1PLSID', '__Host-3PLSID',
]

function normalizeDomain(d = '') { return d.replace(/^\./, '').toLowerCase() }
function isGoogleCookie(c) {
  const d = normalizeDomain(c.domain)
  return d === 'google.com' || d.endsWith('.google.com')
}
function scoreCookie(c) {
  const d = (c.domain || '').toLowerCase()
  let s = 0
  if (d === '.google.com') s += 120
  else if (d === 'google.com') s += 110
  else if (d === '.gemini.google.com') s += 100
  else if (d === 'gemini.google.com') s += 95
  else if (d === '.accounts.google.com') s += 80
  else if (d.endsWith('.google.com')) s += 40
  if (c.path === '/') s += 10
  if (c.secure) s += 3
  if (c.httpOnly) s += 2
  return s
}

async function collectCookies() {
  const all = await chrome.cookies.getAll({})
  const google = all.filter((c) => isGoogleCookie(c) && c.value)
  const selected = new Map()
  for (const name of EXPORT_ORDER) {
    const candidates = google.filter((c) => c.name === name).sort((a, b) => scoreCookie(b) - scoreCookie(a))
    if (candidates.length) selected.set(name, candidates[0])
  }
  const cookieStr = EXPORT_ORDER.filter((n) => selected.has(n)).map((n) => `${n}=${selected.get(n).value}`).join('; ')
  const sapisid = selected.get('SAPISID')?.value || ''
  return { cookieStr, sapisid, hasTs: TS_COOKIES.some((n) => selected.has(n)) }
}

// ── 从打开的 Gemini 标签页抓 xsrf / gemini_bl ────────
async function collectPageMeta() {
  try {
    const tabs = await chrome.tabs.query({ url: 'https://gemini.google.com/*' })
    if (!tabs.length) return { xsrf: null, bl: null }
    const tab = tabs.find((t) => t.active) || tabs[0]
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      func: () => {
        const wiz = globalThis.WIZ_global_data || {}
        const html = document.documentElement?.innerHTML || ''
        const decode = (v) => {
          if (!v) return null
          try { return JSON.parse(`"${v.replace(/"/g, '\\"')}"`) } catch { return v }
        }
        const regexValue = (name) => {
          const m = html.match(new RegExp(`"${name}"\\s*:\\s*"([^"\\n]+)"`))
          return m ? decode(m[1]) : null
        }
        let resourceBl = null
        try {
          for (const e of performance.getEntriesByType('resource')) {
            if (!e?.name?.includes('gemini.google.com')) continue
            const bl = new URL(e.name).searchParams.get('bl')
            if (bl) { resourceBl = bl; break }
          }
        } catch {}
        return {
          xsrf: wiz.SNlM0e || regexValue('SNlM0e') || regexValue('FdrFJe') || null,
          bl: wiz.cfb2h || resourceBl || regexValue('cfb2h') || null,
        }
      },
    })
    return results?.[0]?.result || { xsrf: null, bl: null }
  } catch { return { xsrf: null, bl: null } }
}

// ── 推送 native host ─────────────────────────────────
function ensurePort() {
  if (port) return port
  try {
    port = chrome.runtime.connectNative(HOST_NAME)
    port.onDisconnect.addListener(() => { port = null })
    return port
  } catch { return null }
}

async function pushToHost(force = false) {
  try {
    const { cookieStr, sapisid, hasTs } = await collectCookies()
    if (!cookieStr || !sapisid) return
    const { xsrf, bl } = await collectPageMeta()

    // 指纹：内容 + 时间，避免频繁重复推送
    const payload = {
      cookie: cookieStr,
      sapisid,
      auth_user: null,
      xsrf_token: xsrf || '',
      gemini_bl: bl || '',
    }
    const fingerprint = `${cookieStr.length}|${sapisid.slice(-8)}|${hasTs ? 'TS' : 'noTS'}|${xsrf ? 'X' : 'nx'}|${bl ? 'B' : 'nb'}`
    if (!force && fingerprint === lastPushedHash) return
    lastPushedHash = fingerprint

    const p = ensurePort()
    if (!p) {
      console.warn('[gemini-cookie-sync] native host 未连接（未安装？），跳过自动推送')
      return
    }
    p.postMessage({ type: 'push', payload })
    console.log('[gemini-cookie-sync] pushed to host, cookieLen=', cookieStr.length, 'hasTs=', hasTs)
  } catch (e) {
    console.error('[gemini-cookie-sync] push error:', e)
  }
}

// ── 事件监听 ─────────────────────────────────────────
chrome.cookies.onChanged.addListener((changeInfo) => {
  const c = changeInfo.cookie
  if (!isGoogleCookie(c)) return
  // 防抖：连续 cookie 轮换时合并为一次推送
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => { void pushToHost() }, 2500)
})

// 周期强制刷新（防漏 + 兜底）
chrome.alarms?.create?.('gemini-cookie-force', { periodInMinutes: 30 })
chrome.alarms?.onAlarm?.addListener((alarm) => {
  if (alarm.name === 'gemini-cookie-force') void pushToHost(true)
})

// 启动时推一次
void pushToHost(true)

// 供 popup 查询最近推送状态
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'get-sync-status') {
    sendResponse({ lastPushFingerprint: lastPushedHash, hostConnected: !!port })
    return true
  }
  if (msg?.type === 'force-sync') {
    void pushToHost(true).then(() => sendResponse({ ok: true }))
    return true
  }
})
