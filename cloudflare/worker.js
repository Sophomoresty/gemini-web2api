/**
 * Gemini Web2API - Cloudflare Workers 单文件部署版
 * 
 * 基于原项目 gemini-web2api v1.1.0 移植
 * 直接复制此文件到 Cloudflare Workers 编辑器即可部署
 * 
 * 部署步骤:
 * 1. 登录 Cloudflare Dashboard -> Workers & Pages
 * 2. 创建 Worker -> 粘贴此代码 -> 保存并部署
 * 3. 配置环境变量(可选) -> 绑定自定义域名(可选)
 * 
 * 客户端配置:
 *   基础URL: https://你的worker.workers.dev/v1
 *   API密钥: sk-gemini (或你在配置中设置的密钥)
 */

// ============================================================================
// 📋 配置 - 与原项目 config.json 对应
// ============================================================================

const CONFIG = {
  // 服务器配置 (CF Workers 不需要 port/host)
  // port: 8081,        // ❌ CF Workers 不需要
  // host: "0.0.0.0",   // ❌ CF Workers 不需要
  
  // 重试配置 (对应原项目)
  retryAttempts: 3,        // 对应 retry_attempts
  retryDelaySec: 2,        // 对应 retry_delay_sec
  
  // 请求超时 (对应原项目 request_timeout_sec: 180)
  // CF Workers 免费版最长 30 秒，付费版 60 秒
  // 超长请求可能失败，建议保持在 25 秒以内
  requestTimeoutSec: 30,   // 对应 request_timeout_sec，已适配 CF Workers
  
  // Gemini 构建标签 (对应原项目 gemini_bl)
  geminiBl: 'boq_assistant-bard-web-server_20260716.08_p0',
  
  // 多账户支持 (对应原项目 auth_user: null)
  authUser: null,          // null 或 "" 表示默认账户，多账户填 "0", "1" 等
  
  // XSRF 令牌 (对应原项目 xsrf_token: null)
  xsrfToken: null,         // 通常不需要
  
  // 默认模型 (对应原项目 default_model)
  defaultModel: 'gemini-3.6-flash',
  
  // API 密钥 (对应原项目 api_keys: ["sk-gemini"])
  apiKeys: ['sk-gemini'],  // 空数组表示不验证，设置后需要客户端提供
  
  // Cookie 配置 (对应原项目 cookie_file: null)
  // 原项目支持从文件读取，CF Workers 改为环境变量或直接配置
  cookieString: null,      // 完整的 Cookie 字符串
  sapisid: null,           // SAPISID 值，用于生成认证哈希
  
  // 代理 (对应原项目 proxy: null)
  // proxy: null,          // ❌ CF Workers 不需要代理
  
  // 日志 (对应原项目 log_requests: true)
  logRequests: true,
  
  // 速率限制 (原项目没有，CF Workers 额外添加的保护)
  rateLimit: {
    enabled: true,
    maxRequests: 30,       // 每分钟最大请求数
    windowSec: 60,         // 时间窗口(秒)
  },
};

// ============================================================================
// 🤖 模型定义 (对应原项目 MODELS 字典)
// ============================================================================

// 映射自 JS 源码: MODE_CATEGORY 枚举
// 1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE
const MODELS = {
  'gemini-3.6-flash': {
    mode: 1,        // FAST
    think: 4,       // AUTO
    desc: 'Latest all-around model (Gemini 3.6 Flash)',
  },
  'gemini-3.5-flash': {
    mode: 1,        // FAST
    think: 4,       // AUTO
    desc: 'Alias for gemini-3.6-flash (backend upgraded)',
  },
  'gemini-3.5-flash-thinking': {
    mode: 2,        // THINKING
    think: 0,       // 深度思考启用
    desc: 'Deep thinking mode, longest output (~20k chars)',
  },
  'gemini-3.1-pro': {
    mode: 3,        // PRO
    think: 4,       // AUTO
    desc: 'Pro model (requires cookie for real routing)',
  },
  'gemini-auto': {
    mode: 4,        // AUTO
    think: 4,       // AUTO
    desc: 'Auto model selection',
  },
  'gemini-3.5-flash-thinking-lite': {
    mode: 5,        // FAST_DYNAMIC_THINKING
    think: 0,       // 深度思考启用
    desc: 'Dynamic thinking with adaptive depth',
  },
  'gemini-flash-lite': {
    mode: 6,        // FLASH_LITE
    think: 4,       // AUTO
    desc: 'Lightweight fast model',
  },
};

// ============================================================================
// 🛠 工具函数 (对应原项目 utilities)
// ============================================================================

/**
 * 日志记录 (对应原项目 log 函数)
 */
function log(msg, level = 'INFO') {
  if (CONFIG.logRequests) {
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    console.log(`[${timestamp}] [${level}] ${msg}`);
  }
}

/**
 * 生成 UUID v4 (对应原项目 uuid.uuid4())
 */
function generateUUID() {
  // CF Workers 支持 crypto.randomUUID()
  if (crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // 回退方案
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

/**
 * 生成短 ID (对应原项目 uuid.uuid4().hex[:n])
 */
function generateShortId(length = 12) {
  return generateUUID().replace(/-/g, '').substring(0, length);
}

/**
 * 获取当前时间戳 (对应原项目 time.time())
 */
function timestamp() {
  return Math.floor(Date.now() / 1000);
}

/**
 * 估算 Token 数量 (对应原项目 len(prompt)//4)
 */
function estimateTokens(text) {
  if (!text) return 0;
  // 对应原项目 len(prompt)//4
  return Math.max(1, Math.ceil(text.length / 4));
}

/**
 * 生成 SAPISID 认证哈希 (对应原项目 make_sapisidhash)
 */
async function makeSapisidHash(sapisid) {
  const ts = timestamp();
  const input = `${ts} ${sapisid} https://gemini.google.com`;
  const encoder = new TextEncoder();
  const data = encoder.encode(input);
  const hashBuffer = await crypto.subtle.digest('SHA-1', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  return `SAPISIDHASH ${ts}_${hashHex}`;
}

/**
 * 获取账户 URL 前缀 (对应原项目 account_prefix)
 */
function getAccountPrefix() {
  const authUser = CONFIG.authUser;
  if (authUser === null || authUser === undefined || authUser === '') {
    return '';
  }
  return `/u/${authUser}`;
}

// ============================================================================
// 📡 Gemini API 调用 (对应原项目 gemini_stream_generate 等)
// ============================================================================

/**
 * 构建 Gemini 请求负载 (对应原项目 gemini_stream_generate 中的 inner 构建)
 */
function buildPayload(prompt, modelId, thinkMode) {
  // 创建 80 个元素的列表 (对应原项目 inner = [None] * 80)
  const inner = new Array(80).fill(null);
  
  // 对应原项目 inner[0] = [prompt, 0, None, None, None, None, 0]
  inner[0] = [prompt, 0, null, null, null, null, 0];
  
  // 对应原项目 inner[1] = ["en"]
  inner[1] = ['en'];
  
  // 对应原项目 inner[2] = ["", "", "", None, None, None, None, None, None, ""]
  inner[2] = ['', '', '', null, null, null, null, null, null, ''];
  
  // 对应原项目 inner[6] = [0]
  inner[6] = [0];
  
  // 对应原项目 inner[7] = 1
  inner[7] = 1;
  
  // 对应原项目 inner[10] = 1
  inner[10] = 1;
  
  // 对应原项目 inner[11] = 0
  inner[11] = 0;
  
  // 对应原项目 inner[17] = [[think_mode]]
  inner[17] = [[thinkMode]];
  
  // 对应原项目 inner[18] = 0
  inner[18] = 0;
  
  // 对应原项目 inner[27] = 1
  inner[27] = 1;
  
  // 对应原项目 inner[30] = [4]
  inner[30] = [4];
  
  // 对应原项目 inner[41] = [2]
  inner[41] = [2];
  
  // 对应原项目 inner[53] = 0
  inner[53] = 0;
  
  // 对应原项目 inner[59] = str(uuid.uuid4())
  inner[59] = generateUUID();
  
  // 对应原项目 inner[61] = []
  inner[61] = [];
  
  // 对应原项目 inner[68] = 1
  inner[68] = 1;
  
  // 对应原项目 inner[79] = model_id
  inner[79] = modelId;
  
  // 对应原项目 outer = [None, json.dumps(inner)]
  const outer = [null, JSON.stringify(inner)];
  
  // 对应原项目 params = {"f.req": json.dumps(outer)}
  const params = new URLSearchParams();
  params.append('f.req', JSON.stringify(outer));
  
  // 对应原项目 if CONFIG.get("xsrf_token"): params["at"] = CONFIG["xsrf_token"]
  if (CONFIG.xsrfToken) {
    params.append('at', CONFIG.xsrfToken);
  }
  
  return params.toString();
}

/**
 * 构建请求 URL (对应原项目 url 构建)
 */
function buildUrl() {
  const prefix = getAccountPrefix();
  const reqid = timestamp() % 1000000;
  
  // 对应原项目:
  // url = (f"https://gemini.google.com{prefix}/_/BardChatUi/data/"
  //        "assistant.lamda.BardFrontendService/StreamGenerate"
  //        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c")
  return `https://gemini.google.com${prefix}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?bl=${CONFIG.geminiBl}&hl=en&_reqid=${reqid}&rt=c`;
}

/**
 * 构建请求头 (对应原项目 headers 构建)
 */
async function buildHeaders() {
  const prefix = getAccountPrefix();
  
  // 对应原项目 headers 字典
  const headers = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'https://gemini.google.com',
    'Referer': `https://gemini.google.com${prefix}/app`,
    'X-Same-Domain': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
  };
  
  // 对应原项目 if prefix: headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
  if (prefix) {
    headers['X-Goog-AuthUser'] = String(CONFIG.authUser);
  }
  
  // 对应原项目 cookie_str, sapisid = load_cookie()
  // if cookie_str: headers["Cookie"] = cookie_str
  if (CONFIG.cookieString) {
    headers['Cookie'] = CONFIG.cookieString;
  }
  
  // 对应原项目 if sapisid: headers["Authorization"] = make_sapisidhash(sapisid)
  if (CONFIG.sapisid) {
    headers['Authorization'] = await makeSapisidHash(CONFIG.sapisid);
  }
  
  return headers;
}

/**
 * 非流式调用 (对应原项目 gemini_stream_generate)
 * 发送请求到 Gemini StreamGenerate 端点并获取完整响应
 */
async function geminiStreamGenerate(prompt, modelId, thinkMode) {
  const body = buildPayload(prompt, modelId, thinkMode);
  const headers = await buildHeaders();
  const url = buildUrl();
  
  // 对应原项目重试循环
  let lastError;
  for (let attempt = 0; attempt < CONFIG.retryAttempts; attempt++) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), CONFIG.requestTimeoutSec * 1000);
      
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body,
        signal: controller.signal,
      });
      
      clearTimeout(timeout);
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      // 对应原项目 resp.read().decode("utf-8", errors="replace")
      const text = await response.text();
      return text;
      
    } catch (error) {
      lastError = error;
      
      // 对应原项目 if attempt < CONFIG["retry_attempts"] - 1: time.sleep(...)
      if (attempt < CONFIG.retryAttempts - 1) {
        log(`Retry ${attempt + 1}/${CONFIG.retryAttempts}: ${error.message}`, 'WARN');
        await new Promise(resolve => setTimeout(resolve, CONFIG.retryDelaySec * 1000));
      }
    }
  }
  
  // 对应原项目 raise last_err
  throw lastError;
}

/**
 * 流式调用 (对应原项目 gemini_stream_generate_iter)
 * 使用 ReadableStream 逐步返回增量文本
 */
async function* geminiStreamGenerateIter(prompt, modelId, thinkMode) {
  const body = buildPayload(prompt, modelId, thinkMode);
  const headers = await buildHeaders();
  const url = buildUrl();
  
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), CONFIG.requestTimeoutSec * 1000);
  
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body,
      signal: controller.signal,
    });
    
    clearTimeout(timeout);
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    // 对应原项目 httpx 流式读取
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let prevText = '';
    
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      // 对应原项目 buf += chunk
      buffer += decoder.decode(value, { stream: true });
      
      // 对应原项目 if "BardErrorInfo" in buf
      if (buffer.includes('BardErrorInfo')) {
        const match = buffer.match(/BardErrorInfo\s*\[(\d+)\]/);
        if (match) {
          throw new Error(`Gemini upstream rejected request: BardErrorInfo [${match[1]}]`);
        }
      }
      
      // 对应原项目 while "\n" in buf
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      
      for (const line of lines) {
        // 对应原项目 if '"wrb.fr"' not in line or len(line) < 200: continue
        if (!line.includes('"wrb.fr"') || line.length < 200) continue;
        
        try {
          // 对应原项目 arr = json.loads(line)
          const arr = JSON.parse(line);
          const innerStr = arr[0][2];
          
          // 对应原项目 if not inner_str or len(inner_str) < 50: continue
          if (!innerStr || innerStr.length < 50) continue;
          
          // 对应原项目 inner2 = json.loads(inner_str)
          const inner2 = JSON.parse(innerStr);
          
          if (Array.isArray(inner2) && inner2.length > 4 && inner2[4]) {
            for (const part of inner2[4]) {
              if (Array.isArray(part) && part.length > 1 && part[1] && Array.isArray(part[1])) {
                for (const t of part[1]) {
                  // 对应原项目 if isinstance(t, str) and len(t) > len(prev_text)
                  if (typeof t === 'string' && t.length > prevText.length) {
                    // 对应原项目 delta = t[len(prev_text):]
                    const delta = t.slice(prevText.length);
                    // 对应原项目 delta = clean_gemini_text(delta, strip=False)
                    const cleaned = cleanGeminiText(delta, false);
                    if (cleaned) {
                      yield cleaned;
                    }
                    prevText = t;
                  }
                }
              }
            }
          }
        } catch (e) {
          // JSON 解析错误，继续处理
        }
      }
    }
  } finally {
    clearTimeout(timeout);
  }
}

// ============================================================================
// 📝 文本处理 (对应原项目 clean_gemini_text, extract_response_text)
// ============================================================================

/**
 * 清理 Gemini 响应中的代码执行痕迹
 * 对应原项目 clean_gemini_text
 */
function cleanGeminiText(text, strip = true) {
  // 对应原项目 re.sub 清除代码执行块
  text = text.replace(
    /```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n[\s\S]*?```\n?/g,
    ''
  );
  
  // 对应原项目 return text.strip() if strip else text
  return strip ? text.trim() : text;
}

/**
 * 从原始响应提取最终文本
 * 对应原项目 extract_response_text
 */
function extractResponseText(raw) {
  // 对应原项目 bard_err = re.search(r'BardErrorInfo\s*\[(\d+)\]', raw)
  const bardErr = raw.match(/BardErrorInfo\s*\[(\d+)\]/);
  if (bardErr) {
    throw new Error(`Gemini upstream rejected request: BardErrorInfo [${bardErr[1]}]`);
  }
  
  const texts = [];
  
  // 对应原项目 for line in raw.split("\n")
  for (const line of raw.split('\n')) {
    if (!line.includes('"wrb.fr"') || line.length < 200) continue;
    
    try {
      const arr = JSON.parse(line);
      const innerStr = arr[0][2];
      
      if (!innerStr || innerStr.length < 50) continue;
      
      const inner = JSON.parse(innerStr);
      
      // 对应原项目 if isinstance(inner, list) and len(inner) > 4 and inner[4]
      if (Array.isArray(inner) && inner.length > 4 && inner[4]) {
        for (const part of inner[4]) {
          if (Array.isArray(part) && part.length > 1 && part[1]) {
            if (Array.isArray(part[1])) {
              for (const t of part[1]) {
                if (typeof t === 'string' && t.length > 0) {
                  texts.push(t);
                }
              }
            }
          }
        }
      }
    } catch (e) {
      // 继续处理
    }
  }
  
  // 对应原项目 for t in reversed(texts): if t.strip(): text = t; break
  let text = '';
  for (let i = texts.length - 1; i >= 0; i--) {
    if (texts[i].trim()) {
      text = texts[i];
      break;
    }
  }
  
  // 对应原项目 return clean_gemini_text(text)
  return cleanGeminiText(text);
}

// ============================================================================
// 🔄 OpenAI 格式转换 (对应原项目 messages_to_prompt, parse_tool_calls)
// ============================================================================

/**
 * 将 OpenAI 消息转换为 Gemini 提示
 * 对应原项目 messages_to_prompt
 */
function messagesToPrompt(messages, tools = null) {
  const parts = [];
  
  // 对应原项目 if tools: 添加工具说明
  if (tools && tools.length > 0) {
    const toolDefs = tools.map(tool => {
      const fn = (tool.type === 'function') ? (tool.function || tool) : tool;
      return {
        name: fn.name || tool.name || '',
        description: fn.description || tool.description || '',
        parameters: fn.parameters || tool.parameters || {},
      };
    });
    
    parts.push(
      '[System instruction]: You have access to tools. ' +
      'To call a tool, respond with:\n' +
      '```tool_call\n{"name": "func_name", "arguments": {...}}\n```\n' +
      'Only use tool_call blocks when needed.\n\n' +
      `Available tools:\n${JSON.stringify(toolDefs, null, 2)}`
    );
  }
  
  // 对应原项目 for msg in messages
  for (const msg of messages) {
    const role = msg.role || 'user';
    let content = msg.content || '';
    
    // 对应原项目 if isinstance(content, list)
    if (Array.isArray(content)) {
      content = content
        .filter(c => c.type === 'text' || c.type === 'input_text')
        .map(c => c.text || '')
        .join(' ');
    }
    
    // 对应原项目 if role == "system"
    if (role === 'system') {
      parts.push(`[System instruction]: ${content}`);
    }
    // 对应原项目 elif role == "assistant"
    else if (role === 'assistant') {
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        const tcStrs = msg.tool_calls.map(tc => {
          const fn = tc.function || {};
          return `\`\`\`tool_call\n{"name": "${fn.name}", "arguments": ${fn.arguments || '{}'}}\n\`\`\``;
        });
        parts.push(`[Assistant]: ${content || ''}\n${tcStrs.join('\n')}`);
      } else {
        parts.push(`[Assistant]: ${content}`);
      }
    }
    // 对应原项目 elif role == "tool"
    else if (role === 'tool') {
      parts.push(`[Tool result for ${msg.name || 'unknown'}]: ${content}`);
    }
    // 对应原项目 else: parts.append(content)
    else {
      parts.push(content || '');
    }
  }
  
  // 对应原项目 return "\n\n".join(p for p in parts if p)
  return parts.filter(p => p).join('\n\n');
}

/**
 * 从响应中解析工具调用
 * 对应原项目 parse_tool_calls
 */
function parseToolCalls(text) {
  const toolCalls = [];
  
  // 对应原项目 pattern = r'```tool_call\s*\n(.*?)\n```'
  const pattern = /```tool_call\s*\n(.*?)\n```/gs;
  let match;
  
  while ((match = pattern.exec(text)) !== null) {
    try {
      const data = JSON.parse(match[1].trim());
      
      // 对应原项目 tool_calls.append({...})
      toolCalls.push({
        id: `call_${generateShortId(8)}`,
        type: 'function',
        function: {
          name: data.name,
          arguments: JSON.stringify(data.arguments || {}),
        },
      });
    } catch (e) {
      // 对应原项目 except (json.JSONDecodeError, KeyError): pass
    }
  }
  
  // 对应原项目 clean = re.sub(pattern, '', text, flags=re.DOTALL).strip()
  const cleanText = text.replace(pattern, '').trim();
  
  return { cleanText, toolCalls };
}

/**
 * Google 原生格式转提示
 * 对应原项目 _google_contents_to_prompt
 */
function googleContentsToPrompt(req) {
  const parts = [];
  
  // 对应原项目 if sys_inst
  const sysInst = req.systemInstruction;
  if (sysInst && sysInst.parts) {
    const sysText = sysInst.parts
      .filter(p => p.text)
      .map(p => p.text)
      .join(' ');
    if (sysText) {
      parts.push(`[System instruction]: ${sysText}`);
    }
  }
  
  // 对应原项目 for content in req.get("contents", [])
  for (const content of req.contents || []) {
    const role = content.role || 'user';
    const textParts = (content.parts || [])
      .filter(p => p.text)
      .map(p => p.text);
    const text = textParts.join(' ');
    
    // 对应原项目 if role == "model": parts.append(f"[Assistant]: {text}")
    if (role === 'model') {
      parts.push(`[Assistant]: ${text}`);
    } else {
      parts.push(text);
    }
  }
  
  return parts.filter(p => p).join('\n\n');
}

// ============================================================================
// 🚦 速率限制 (额外添加的保护措施)
// ============================================================================

// 内存存储
const rateLimitStore = {};

/**
 * 检查速率限制
 */
function checkRateLimit(clientIP) {
  if (!CONFIG.rateLimit || !CONFIG.rateLimit.enabled) return true;
  
  const now = Date.now();
  const windowMs = CONFIG.rateLimit.windowSec * 1000;
  const key = `rl:${clientIP}`;
  
  // 清理过期记录
  if (!rateLimitStore[key]) {
    rateLimitStore[key] = [];
  }
  rateLimitStore[key] = rateLimitStore[key].filter(t => now - t < windowMs);
  
  // 检查是否超限
  if (rateLimitStore[key].length >= CONFIG.rateLimit.maxRequests) {
    return false;
  }
  
  // 记录本次请求
  rateLimitStore[key].push(now);
  return true;
}

// ============================================================================
// 🔐 API 密钥验证 (对应原项目 _authorized)
// ============================================================================

/**
 * 验证 API 密钥
 * 对应原项目 _authorized 方法
 */
function checkApiKey(request) {
  // 对应原项目 keys = CONFIG.get("api_keys") or []
  const keys = CONFIG.apiKeys || [];
  
  // 对应原项目 if not keys: return True
  if (keys.length === 0) return true;
  
  // 对应原项目 auth = self.headers.get("Authorization", "")
  const auth = request.headers.get('Authorization') || '';
  
  // 对应原项目 if auth.startswith("Bearer ") and auth[7:] in keys
  if (auth.startsWith('Bearer ') && keys.includes(auth.slice(7))) {
    return true;
  }
  
  // 对应原项目 for h in ("x-api-key", "x-goog-api-key")
  for (const h of ['x-api-key', 'x-goog-api-key']) {
    const value = request.headers.get(h) || '';
    if (keys.includes(value)) return true;
  }
  
  // 对应原项目 URL 查询参数检查
  const url = new URL(request.url);
  const keyParam = url.searchParams.get('key');
  if (keyParam && keys.includes(keyParam)) return true;
  
  return false;
}

// ============================================================================
// 📤 HTTP 响应 (对应原项目 GeminiHandler)
// ============================================================================

/**
 * 发送 JSON 响应
 * 对应原项目 send_json
 */
function sendJSON(data, status = 200) {
  const body = JSON.stringify(data);
  return new Response(body, {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': '*',
    },
  });
}

/**
 * 发送 SSE 流式响应
 * 对应原项目流式处理
 */
function sendSSE(stream) {
  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'Access-Control-Allow-Origin': '*',
      'X-Accel-Buffering': 'no',  // 禁用 nginx 缓冲
    },
  });
}

// ============================================================================
// 🎯 模型解析 (对应原项目 _resolve_model)
// ============================================================================

/**
 * 解析模型名称
 * 对应原项目 _resolve_model
 */
function resolveModel(modelName) {
  let thinkOverride = null;
  
  // 对应原项目 if "@think=" in model_name
  if (modelName.includes('@think=')) {
    const parts = modelName.split('@think=');
    modelName = parts[0];
    thinkOverride = parseInt(parts[1]);
    if (isNaN(thinkOverride)) {
      return { error: `无效的 think 参数: ${parts[1]}` };
    }
  }
  
  // 对应原项目 cfg = MODELS.get(model_name)
  const cfg = MODELS[modelName];
  if (!cfg) {
    return { error: `未知模型: ${modelName}` };
  }
  
  // 对应原项目 return model_name, cfg["mode"], (think_override if ... else cfg["think"]), None
  return {
    modelName,
    modelId: cfg.mode,
    thinkMode: thinkOverride !== null ? thinkOverride : cfg.think,
    error: null,
  };
}

// ============================================================================
// 📋 请求处理 (对应原项目 do_GET, do_POST)
// ============================================================================

/**
 * 处理 /v1/chat/completions
 * 对应原项目 handle_chat
 */
async function handleChatCompletions(request, body) {
  // 对应原项目 model_name, model_id, think_mode, err = self._resolve_model(...)
  const resolved = resolveModel(body.model || CONFIG.defaultModel);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }
  
  const { modelName, modelId, thinkMode } = resolved;
  const tools = body.tools || null;
  
  // 对应原项目 prompt = messages_to_prompt(req.get("messages", []), tools)
  const prompt = messagesToPrompt(body.messages || [], tools);
  
  // 对应原项目 if not prompt.strip()
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty prompt' } }, 400);
  }
  
  const stream = body.stream || false;
  // 对应原项目 cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
  const chatId = `chatcmpl-${generateShortId(12)}`;
  
  log(`Chat: model=${modelName}, stream=${stream}, tokens≈${estimateTokens(prompt)}`);
  
  // 流式处理 (对应原项目 if stream and not tools)
  if (stream && !tools) {
    const encoder = new TextEncoder();
    const streamBody = new ReadableStream({
      async start(controller) {
        try {
          // 对应原项目 for delta_text in gemini_stream_generate_iter(...)
          for await (const deltaText of geminiStreamGenerateIter(prompt, modelId, thinkMode)) {
            const chunk = {
              id: chatId,
              object: 'chat.completion.chunk',
              created: timestamp(),
              model: modelName,
              choices: [{ index: 0, delta: { content: deltaText }, finish_reason: null }],
            };
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`));
          }
          
          // 对应原项目最终块
          const finish = {
            id: chatId,
            object: 'chat.completion.chunk',
            created: timestamp(),
            model: modelName,
            choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
          };
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(finish)}\n\n`));
          controller.enqueue(encoder.encode('data: [DONE]\n\n'));
          controller.close();
        } catch (error) {
          log(`Stream error: ${error.message}`, 'ERROR');
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ error: { message: error.message } })}\n\n`));
          controller.enqueue(encoder.encode('data: [DONE]\n\n'));
          controller.close();
        }
      },
    });
    
    return sendSSE(streamBody);
  }
  
  // 非流式处理
  try {
    // 对应原项目 raw = gemini_stream_generate(prompt, model_id, think_mode)
    const raw = await geminiStreamGenerate(prompt, modelId, thinkMode);
    
    // 对应原项目 text = extract_response_text(raw)
    let text = extractResponseText(raw);
    let toolCalls = null;
    
    // 对应原项目 if tools and text: text, tool_calls = parse_tool_calls(text)
    if (tools && text) {
      const parsed = parseToolCalls(text);
      text = parsed.cleanText;
      toolCalls = parsed.toolCalls.length > 0 ? parsed.toolCalls : null;
    }
    
    // 对应原项目 msg = {"role": "assistant", "content": text or None}
    const msg = { role: 'assistant', content: text || null };
    if (toolCalls) {
      msg.tool_calls = toolCalls;
    }
    
    // 对应原项目 finish = "tool_calls" if tool_calls else "stop"
    const finishReason = toolCalls ? 'tool_calls' : 'stop';
    
    // 流式模式但使用了工具 (对应原项目特殊处理)
    if (stream) {
      const encoder = new TextEncoder();
      const streamBody = new ReadableStream({
        start(controller) {
          const chunk = {
            id: chatId,
            object: 'chat.completion.chunk',
            created: timestamp(),
            model: modelName,
            choices: [{ index: 0, delta: msg, finish_reason: finishReason }],
          };
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`));
          controller.enqueue(encoder.encode('data: [DONE]\n\n'));
          controller.close();
        },
      });
      return sendSSE(streamBody);
    }
    
    // 对应原项目 self.send_json({...})
    return sendJSON({
      id: chatId,
      object: 'chat.completion',
      created: timestamp(),
      model: modelName,
      choices: [{ index: 0, message: msg, finish_reason: finishReason }],
      usage: {
        prompt_tokens: estimateTokens(prompt),
        completion_tokens: estimateTokens(text),
        total_tokens: estimateTokens(prompt + text),
      },
    });
    
  } catch (error) {
    log(`Upstream error: ${error.message}`, 'ERROR');
    return sendJSON({ error: { message: `upstream error: ${error.message}` } }, 502);
  }
}

/**
 * 处理 /v1/responses (OpenAI Responses API)
 * 对应原项目 handle_responses
 */
async function handleResponses(request, body) {
  const resolved = resolveModel(body.model || CONFIG.defaultModel);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }
  
  const { modelName, modelId, thinkMode } = resolved;
  
  // 构建消息 (对应原项目处理逻辑)
  const messages = [];
  if (body.instructions) {
    messages.push({ role: 'system', content: body.instructions });
  }
  
  const inputs = body.input || [];
  for (const item of (typeof inputs === 'string' ? [inputs] : inputs)) {
    if (typeof item === 'string') {
      messages.push({ role: 'user', content: item });
    } else if (item.type === 'function_call_output') {
      messages.push({
        role: 'tool',
        tool_call_id: item.call_id,
        name: item.name,
        content: item.output,
      });
    } else {
      let content = item.content;
      if (Array.isArray(content)) {
        content = content
          .filter(c => c.type === 'output_text')
          .map(c => c.text)
          .join(' ');
      }
      messages.push({ role: item.role || 'user', content });
    }
  }
  
  // 标准化工具定义
  let tools = body.tools;
  if (tools) {
    tools = tools.map(t => {
      if (t.type === 'function' && !t.function) {
        return { type: 'function', function: { name: t.name, description: t.description, parameters: t.parameters } };
      }
      return t;
    });
  }
  
  const prompt = messagesToPrompt(messages, tools);
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty input' } }, 400);
  }
  
  try {
    const raw = await geminiStreamGenerate(prompt, modelId, thinkMode);
    let text = extractResponseText(raw);
    let toolCalls = null;
    
    if (tools && text) {
      const parsed = parseToolCalls(text);
      text = parsed.cleanText;
      toolCalls = parsed.toolCalls.length > 0 ? parsed.toolCalls : null;
    }
    
    // 构建输出
    const responseId = `resp_${generateShortId(16)}`;
    const messageId = `msg_${generateShortId(12)}`;
    const output = [];
    
    if (toolCalls) {
      for (const tc of toolCalls) {
        output.push({
          type: 'function_call',
          id: tc.id,
          call_id: tc.id,
          name: tc.function.name,
          arguments: tc.function.arguments,
          status: 'completed',
        });
      }
    }
    
    if (text || !toolCalls) {
      output.push({
        type: 'message',
        id: messageId,
        role: 'assistant',
        status: 'completed',
        content: [{ type: 'output_text', text: text || '', annotations: [] }],
      });
    }
    
    return sendJSON({
      id: responseId,
      object: 'response',
      created_at: timestamp(),
      status: 'completed',
      model: modelName,
      output,
      usage: {
        input_tokens: estimateTokens(prompt),
        output_tokens: estimateTokens(text),
        total_tokens: estimateTokens(prompt + text),
      },
    });
  } catch (error) {
    return sendJSON({ error: { message: `upstream error: ${error.message}` } }, 502);
  }
}

/**
 * 处理 Google 原生 API
 * 对应原项目 _handle_google_generate
 */
async function handleGoogleAPI(request, body, stream) {
  // 对应原项目 _parse_google_model_from_path
  const url = new URL(request.url);
  const match = url.pathname.match(/\/v1beta\/models\/([^:]+)/);
  const modelName = match ? match[1] : null;
  
  if (!modelName) {
    return sendJSON({ error: { message: 'model not specified in path' } }, 400);
  }
  
  const resolved = resolveModel(modelName);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }
  
  const { modelId, thinkMode } = resolved;
  
  // 对应原项目 prompt = self._google_contents_to_prompt(req)
  const prompt = googleContentsToPrompt(body);
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty content' } }, 400);
  }
  
  try {
    const raw = await geminiStreamGenerate(prompt, modelId, thinkMode);
    const text = extractResponseText(raw);
    
    // 对应原项目构建响应
    const response = {
      candidates: [{
        content: { parts: [{ text: text || '' }], role: 'model' },
        finishReason: 'STOP',
        index: 0,
      }],
      usageMetadata: {
        promptTokenCount: estimateTokens(prompt),
        candidatesTokenCount: estimateTokens(text),
        totalTokenCount: estimateTokens(prompt + text),
      },
      modelVersion: modelName,
    };
    
    if (stream) {
      return new Response(`data: ${JSON.stringify(response)}\n\n`, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }
    
    return sendJSON(response);
  } catch (error) {
    return sendJSON({ error: { message: `upstream error: ${error.message}` } }, 502);
  }
}

// ============================================================================
// 🚀 主入口 (对应原项目 main 和 GeminiHandler)
// ============================================================================

export default {
  async fetch(request, env, ctx) {
    // ─── 从环境变量加载配置 (对应原项目 load_config) ───
    
    // 对应原项目 gemini_bl
    if (env.GEMINI_BL) CONFIG.geminiBl = env.GEMINI_BL;
    
    // 对应原项目 default_model
    if (env.DEFAULT_MODEL) CONFIG.defaultModel = env.DEFAULT_MODEL;
    
    // 对应原项目 cookie_file (CF Workers 改用环境变量)
    if (env.COOKIE_STRING) CONFIG.cookieString = env.COOKIE_STRING;
    if (env.SAPISID) CONFIG.sapisid = env.SAPISID;
    
    // 对应原项目 auth_user
    if (env.AUTH_USER) CONFIG.authUser = env.AUTH_USER;
    
    // 对应原项目 xsrf_token
    if (env.XSRF_TOKEN) CONFIG.xsrfToken = env.XSRF_TOKEN;
    
    // 对应原项目 api_keys
    if (env.API_KEYS) {
      try {
        CONFIG.apiKeys = JSON.parse(env.API_KEYS);
      } catch (e) {
        log(`API_KEYS 解析失败: ${e.message}`, 'ERROR');
      }
    }
    
    // 对应原项目 retry_attempts
    if (env.RETRY_ATTEMPTS) CONFIG.retryAttempts = parseInt(env.RETRY_ATTEMPTS) || 3;
    
    // 对应原项目 retry_delay_sec
    if (env.RETRY_DELAY_SEC) CONFIG.retryDelaySec = parseInt(env.RETRY_DELAY_SEC) || 2;
    
    // 对应原项目 request_timeout_sec
    if (env.REQUEST_TIMEOUT_SEC) CONFIG.requestTimeoutSec = parseInt(env.REQUEST_TIMEOUT_SEC) || 30;
    
    // 速率限制配置
    if (env.RATE_LIMIT_MAX) CONFIG.rateLimit.maxRequests = parseInt(env.RATE_LIMIT_MAX) || 30;
    if (env.RATE_LIMIT_WINDOW) CONFIG.rateLimit.windowSec = parseInt(env.RATE_LIMIT_WINDOW) || 60;
    
    // ─── 请求处理 ───
    
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;
    
    // 对应原项目 do_OPTIONS (CORS 预检)
    if (method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Max-Age': '86400',
        },
      });
    }
    
    // 速率限制检查
    const clientIP = request.headers.get('CF-Connecting-IP') || '0.0.0.0';
    if (!checkRateLimit(clientIP)) {
      log(`Rate limit: ${clientIP}`, 'WARN');
      return sendJSON({
        error: {
          message: '请求过于频繁，请稍后再试',
          type: 'rate_limit_exceeded',
        },
      }, 429);
    }
    
    // API 密钥验证 (对应原项目 _authorized)
    if (path.startsWith('/v1') && !checkApiKey(request)) {
      return sendJSON({
        error: { message: 'invalid api key' },
      }, 401);
    }
    
    // ─── GET 请求处理 (对应原项目 do_GET) ───
    
    if (method === 'GET') {
      // 健康检查
      if (path === '/' || path === '/health') {
        return sendJSON({
          status: 'ok',
          version: '1.1.0-cf',
          platform: 'Cloudflare Workers',
          models: Object.keys(MODELS),
          defaultModel: CONFIG.defaultModel,
        });
      }
      
      // 对应原项目 /v1/models
      if (path === '/v1/models') {
        return sendJSON({
          object: 'list',
          data: Object.entries(MODELS).map(([id, cfg]) => ({
            id,
            object: 'model',
            created: 1700000000,
            owned_by: 'google',
            description: cfg.desc,
          })),
        });
      }
      
      // 对应原项目 /v1beta/models
      if (path === '/v1beta/models') {
        return sendJSON({
          models: Object.entries(MODELS).map(([name, cfg]) => ({
            name: `models/${name}`,
            displayName: name,
            description: cfg.desc,
            supportedGenerationMethods: ['generateContent', 'streamGenerateContent'],
          })),
        });
      }
      
      return sendJSON({ error: { message: 'not found' } }, 404);
    }
    
    // ─── POST 请求处理 (对应原项目 do_POST) ───
    
    if (method === 'POST') {
      let body;
      try {
        body = await request.json();
      } catch (e) {
        return sendJSON({ error: { message: 'invalid JSON' } }, 400);
      }
      
      // 对应原项目 /v1/chat/completions
      if (path === '/v1/chat/completions') {
        return handleChatCompletions(request, body);
      }
      
      // 对应原项目 /v1/responses
      if (path === '/v1/responses') {
        return handleResponses(request, body);
      }
      
      // 对应原项目 :generateContent
      if (path.includes(':generateContent') && !path.includes('stream')) {
        return handleGoogleAPI(request, body, false);
      }
      
      // 对应原项目 :streamGenerateContent
      if (path.includes(':streamGenerateContent')) {
        return handleGoogleAPI(request, body, true);
      }
      
      return sendJSON({ error: { message: 'not found' } }, 404);
    }
    
    return sendJSON({ error: { message: 'method not allowed' } }, 405);
  },
};
