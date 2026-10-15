// Background service worker: auto-syncs rotated Google/Gemini session cookies to local gemini-web2api server
const EXPORT_ORDER = [
  "SID",
  "HSID",
  "SSID",
  "APISID",
  "SAPISID",
  "LSID",
  "OSID",
  "SIDCC",
  "AEC",
  "NID",
  "COMPASS",
  "__Secure-1PAPISID",
  "__Secure-1PSID",
  "__Secure-1PSIDTS",
  "__Secure-1PSIDCC",
  "__Secure-1PSIDRTS",
  "__Secure-3PAPISID",
  "__Secure-3PSID",
  "__Secure-3PSIDTS",
  "__Secure-3PSIDCC",
  "__Secure-3PSIDRTS",
  "__Secure-OSID",
  "__Host-1PLSID",
  "__Host-3PLSID"
];

const KEY_COOKIES = [
  "SID",
  "HSID",
  "SSID",
  "APISID",
  "SAPISID",
  "__Secure-1PSID",
  "__Secure-3PSID",
  "__Secure-1PSIDTS",
  "__Secure-3PSIDTS"
];

const SESSION_COOKIES = ["__Secure-1PSID", "__Secure-3PSID", "SID"];
const LOCAL_ENDPOINT = "http://127.0.0.1:8081/v1/cookies";
let debounceTimer = null;
let lastKeySignature = "";
let lastCookieSignature = "";
let cachedTickets = null;
let ticketWriteQueue = Promise.resolve();

function resolveModelsForMode(mode, variant) {
  switch (mode) {
    case 1:
      return ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"];
    case 2:
      return variant === 2
        ? ["gemini-3.8-flash-thinking"]
        : ["gemini-3.5-flash-thinking"];
    case 3:
      return ["gemini-3.1-pro", "gemini-3.1-pro-enhanced"];
    case 4:
      return ["gemini-auto"];
    case 5:
      return ["gemini-3.8-flash-thinking-lite", "gemini-3.5-flash-thinking-lite"];
    case 6:
      return ["gemini-flash-lite", "gemini-3.5-flash-lite"];
    case 8:
      return ["gemini-4-argon"];
    default:
      return [];
  }
}

function parseTicketHeader(headerVal) {
  if (!headerVal || typeof headerVal !== "string") return null;
  try {
    const arr = JSON.parse(headerVal);
    if (!Array.isArray(arr) || typeof arr[4] !== "string") return null;
    const ticket = arr[4].trim().toLowerCase();
    if (!/^[0-9a-f]{16}$/.test(ticket) || !Number.isInteger(arr[14])) return null;
    const models = resolveModelsForMode(arr[14], arr[15]);
    if (models.length === 0) return null;
    return { ticket, models };
  } catch {
    return null;
  }
}

async function getStoredModelTickets() {
  if (cachedTickets) return { ...cachedTickets };
  try {
    const res = await chrome.storage.local.get("model_tickets");
    const stored = res?.model_tickets;
    cachedTickets = stored && typeof stored === "object" && !Array.isArray(stored) ? { ...stored } : {};
  } catch {
    cachedTickets = {};
  }
  return { ...cachedTickets };
}

function scheduleCookieSync(force = false) {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    void syncCookiesToLocalServer(force);
  }, 2000);
}

function recordCapturedTicket(headerVal) {
  const parsed = parseTicketHeader(headerVal);
  if (!parsed) return;
  ticketWriteQueue = ticketWriteQueue
    .then(async () => {
      const current = await getStoredModelTickets();
      let changed = false;
      for (const m of parsed.models) {
        if (current[m] !== parsed.ticket) {
          current[m] = parsed.ticket;
          changed = true;
        }
      }
      if (!changed) return;
      await chrome.storage.local.set({ model_tickets: current });
      cachedTickets = current;
      scheduleCookieSync(false);
    })
    .catch(() => {});
}

function normalizeDomain(domain = "") {
  return domain.replace(/^\./, "").toLowerCase();
}

function isGoogleCookie(cookie) {
  const domain = normalizeDomain(cookie.domain);
  return domain === "google.com" || domain.endsWith(".google.com");
}

function scoreCookie(cookie) {
  const domain = (cookie.domain || "").toLowerCase();
  let score = 0;
  if (domain === ".google.com") score += 120;
  else if (domain === "google.com") score += 110;
  else if (domain === ".gemini.google.com") score += 100;
  else if (domain === "gemini.google.com") score += 95;
  else if (domain === ".accounts.google.com") score += 80;
  else if (domain.endsWith(".google.com")) score += 40;
  if (cookie.path === "/") score += 10;
  if (cookie.secure) score += 3;
  if (cookie.httpOnly) score += 2;
  return score;
}

async function postPayloadToServer(payload) {
  try {
    const resp = await fetch(LOCAL_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Gemini-Cookie-Sync": "1"
      },
      body: JSON.stringify(payload)
    });
    return { ok: resp.ok, status: resp.status };
  } catch {
    return { ok: false, status: 0 };
  }
}

async function fetchGeminiPageMeta() {
  try {
    const resp = await fetch("https://gemini.google.com/app", {
      method: "GET",
      credentials: "include",
      cache: "no-store"
    });
    if (!resp.ok) return {};
    const html = await resp.text();
    const xsrfMatch = html.match(/"SNlM0e"\s*:\s*"([^"\\]+)"/);
    const blMatch =
      html.match(/"cfb2h"\s*:\s*"([^"\\]+)"/) ||
      html.match(/(boq_gemini-web-uiserver_\d+\.\d+_p\d+|boq_assistant-bard-web-server_\d+\.\d+_p\d+)/);
    return {
      xsrf_token: xsrfMatch ? xsrfMatch[1] : undefined,
      gemini_bl: blMatch ? blMatch[1] : undefined
    };
  } catch {
    return {};
  }
}

async function syncCookiesToLocalServer(force = false) {
  try {
    const meta = force ? await fetchGeminiPageMeta() : {};
    const [allCookies, modelTickets] = await Promise.all([
      chrome.cookies.getAll({ domain: "google.com" }),
      getStoredModelTickets()
    ]);
    const googleCookies = allCookies.filter((c) => isGoogleCookie(c) && Boolean(c.value));
    const selected = new Map();

    for (const name of EXPORT_ORDER) {
      const candidates = googleCookies
        .filter((c) => c.name === name)
        .sort((a, b) => scoreCookie(b) - scoreCookie(a));
      if (candidates.length > 0) {
        selected.set(name, candidates[0]);
      }
    }

    const sapisid = selected.get("SAPISID")?.value;
    const hasSession = SESSION_COOKIES.some((name) => selected.has(name));
    if (!sapisid || !hasSession) return false;

    const cookieSig = KEY_COOKIES.map((k) => `${k}=${selected.get(k)?.value || ""}`).join(";");
    const ticketKeys = Object.keys(modelTickets).sort();
    const ticketSig = ticketKeys.map((k) => `${k}:${modelTickets[k]}`).join(",");
    const sig = `${cookieSig}|t=${ticketSig}`;
    if (!force && sig === lastKeySignature) return true;

    const cookiesChanged = cookieSig !== lastCookieSignature;
    const refreshedMeta = meta.xsrf_token
      ? meta
      : (force || cookiesChanged)
        ? await fetchGeminiPageMeta()
        : {};

    const cookieString = EXPORT_ORDER
      .filter((name) => selected.has(name))
      .map((name) => `${name}=${selected.get(name).value}`)
      .join("; ");

    const payload = { cookie: cookieString };
    if (refreshedMeta.xsrf_token) payload.xsrf_token = refreshedMeta.xsrf_token;
    if (refreshedMeta.gemini_bl) payload.gemini_bl = refreshedMeta.gemini_bl;
    if (ticketKeys.length > 0) payload.model_tickets = modelTickets;

    const res = await postPayloadToServer(payload);
    if (res.ok) {
      lastKeySignature = sig;
      lastCookieSignature = cookieSig;
      return true;
    }
  } catch {
    // Ignore background sync errors when local server is offline.
  }
  return false;
}

function ensureAlarm() {
  chrome.alarms.create("gemini-cookie-sync", { periodInMinutes: 10 });
}

chrome.runtime.onInstalled.addListener(() => {
  ensureAlarm();
  void syncCookiesToLocalServer(true);
});

chrome.runtime.onStartup.addListener(() => {
  ensureAlarm();
  void syncCookiesToLocalServer(true);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm?.name === "gemini-cookie-sync") {
    void syncCookiesToLocalServer(true);
  }
});

chrome.cookies.onChanged.addListener((changeInfo) => {
  if (changeInfo.removed) return;
  const { cookie } = changeInfo;
  if (!cookie || !isGoogleCookie(cookie) || !KEY_COOKIES.includes(cookie.name)) return;
  scheduleCookieSync(false);
});

if (chrome.webRequest?.onBeforeSendHeaders) {
  chrome.webRequest.onBeforeSendHeaders.addListener(
    (details) => {
      const headers = details?.requestHeaders || [];
      for (const h of headers) {
        if (h?.name && h.name.toLowerCase() === "x-goog-ext-525001261-jspb" && h.value) {
          recordCapturedTicket(h.value);
          break;
        }
      }
    },
    { urls: ["https://gemini.google.com/*StreamGenerate*"] },
    ["requestHeaders"]
  );
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "sync-cookies" && msg.payload) {
    void postPayloadToServer(msg.payload).then(sendResponse);
    return true;
  }
  return false;
});
