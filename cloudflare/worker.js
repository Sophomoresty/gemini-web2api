/**
 * Gemini Web2API - Cloudflare Workers 完整修复版（打字机效果）
 * 
 * 修复项：
 * 1. OPTIONS 预检在入口最顶部优先处理，完善 CORS
 * 2. SSE 格式严格符合 OpenAI 标准：首块只有 role、末块 content: ""
 * 3. 实时转发 Gemini 增量数据，实现打字机逐字输出效果
 * 4. 心跳保活机制，防止连接超时
 * 5. 保留完整功能：工具调用、速率限制、API认证、Google原生API、Responses API
 * 
 * 基于原项目 gemini-web2api v1.1.0 移植
 * 直接复制此文件到 Cloudflare Workers 编辑器即可部署
 * 
 * 部署步骤:
 * 1. 登录 Cloudflare Dashboard -> Workers & Pages
 * 2. 创建 Worker -> 粘贴此代码 -> 保存并部署
 * 3. 配置环境变量(可选) -> 绑定自定义域名(可选)
 * 
 * 环境变量（在 CF Dashboard 设置）：
 *   GEMINI_BL - Gemini 构建标签
 *   COOKIE_STRING - 完整 Cookie 字符串（解决 429 限流）
 *   SAPISID - SAPISID 值（用于生成认证哈希）
 *   API_KEYS - API 密钥 JSON 数组，如 ["sk-gemini"]
 *   AUTH_USER - 多账户索引
 *   XSRF_TOKEN - XSRF 令牌
 *   RETRY_ATTEMPTS - 重试次数
 *   RETRY_DELAY_SEC - 重试间隔(秒)
 *   REQUEST_TIMEOUT_SEC - 请求超时(秒)
 *   RATE_LIMIT_MAX - 速率限制最大请求数
 *   RATE_LIMIT_WINDOW - 速率限制时间窗口(秒)
 * 
 * 客户端配置:
 *   基础URL: https://你的worker.workers.dev/v1
 *   API密钥: sk-gemini (或你在配置中设置的密钥)
 */

// ============================================================================
// 配置 - 与原项目 config.json 对应
// ============================================================================

const CONFIG = {
  // 服务器配置 (CF Workers 不需要 port/host)
  // port: 8081,        // ❌ CF Workers 不需要
  // host: "0.0.0.0",   // ❌ CF Workers 不需要

  // 重试配置 (对应原项目 retry_attempts / retry_delay_sec)
  retryAttempts: 3,        // 对应 retry_attempts: 3
  retryDelaySec: 2,        // 对应 retry_delay_sec: 2

  // 请求超时 (对应原项目 request_timeout_sec: 180)
  // CF Workers 免费版最长 30 秒 CPU 时间，付费版 60 秒
  // 注意：流式请求不受 30 秒 CPU 限制，但初始 fetch 必须在超时内完成
  requestTimeoutSec: 28,   // 已适配 CF Workers 免费版

  // Gemini 构建标签 (对应原项目 gemini_bl)
  // 如果遇到 405 错误，需要更新此值
  // 获取方法：浏览器打开 gemini.google.com，F12 -> Network -> 搜索 "boq_assistant"
  geminiBl: 'boq_assistant-bard-web-server_20260716.08_p0',

  // 多账户支持 (对应原项目 auth_user: null)
  // null 或 "" 表示默认账户
  // "0" 表示第一个账户，"1" 表示第二个账户，以此类推
  authUser: null,

  // XSRF 令牌 (对应原项目 xsrf_token: null)
  // 通常不需要设置
  xsrfToken: null,

  // 默认模型 (对应原项目 default_model: "gemini-3.6-flash")
  defaultModel: 'gemini-3.6-flash',

  // API 密钥 (对应原项目 api_keys: ["sk-gemini"])
  // 空数组表示不验证 API Key，所有请求都可以访问
  // 设置后，客户端需要在 Authorization 头中提供 Bearer token
  apiKeys: ['sk-gemini'],

  // Cookie 配置 (对应原项目 cookie_file: null)
  // 原项目从文件读取，CF Workers 改为环境变量或直接配置
  // 添加有效 Cookie 可以解决 429 限流问题
  cookieString: null,      // 完整的 Cookie 字符串，如 "__Secure-1PSID=xxx; SAPISID=xxx"
  sapisid: null,           // SAPISID 值，用于生成认证哈希

  // 代理 (对应原项目 proxy: null)
  // proxy: null,          // ❌ CF Workers 不需要代理，自动使用全球网络

  // 日志 (对应原项目 log_requests: true)
  logRequests: true,

  // 速率限制 (CF Workers 额外添加的保护措施)
  // 原项目没有此功能，这里是为防止滥用而添加
  rateLimit: {
    enabled: true,           // 是否启用速率限制
    maxRequests: 30,         // 每分钟最大请求数
    windowSec: 60,           // 时间窗口(秒)
  },
};

// ============================================================================
// 模型定义 (对应原项目 MODELS 字典)
// ============================================================================

// 映射自 JS 源码: MODE_CATEGORY 枚举
// 1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE
const MODELS = {
  'gemini-3.6-flash': {
    mode: 1,        // FAST - 快速模式
    think: 4,       // AUTO - 自动选择思考深度
    desc: 'Latest all-around model (Gemini 3.6 Flash)',
  },
  'gemini-3.5-flash': {
    mode: 1,        // FAST
    think: 4,       // AUTO
    desc: 'Alias for gemini-3.6-flash (backend upgraded)',
  },
  'gemini-3.5-flash-thinking': {
    mode: 2,        // THINKING - 深度思考模式
    think: 0,       // 启用深度思考
    desc: 'Deep thinking mode, longest output (~20k chars)',
  },
  'gemini-3.1-pro': {
    mode: 3,        // PRO - 专业版
    think: 4,       // AUTO
    desc: 'Pro model (requires cookie for real routing)',
  },
  'gemini-auto': {
    mode: 4,        // AUTO - 自动模型选择
    think: 4,       // AUTO
    desc: 'Auto model selection',
  },
  'gemini-3.5-flash-thinking-lite': {
    mode: 5,        // FAST_DYNAMIC_THINKING - 动态思考
    think: 0,       // 启用思考
    desc: 'Dynamic thinking with adaptive depth',
  },
  'gemini-flash-lite': {
    mode: 6,        // FLASH_LITE - 轻量快速
    think: 4,       // AUTO
    desc: 'Lightweight fast model',
  },
};

// ============================================================================
// 工具函数 (对应原项目 utilities)
// ============================================================================

/**
 * 日志记录
 * 对应原项目 log 函数
 * 输出格式: [HH:MM:SS] [LEVEL] message
 * @param {string} msg - 日志消息
 * @param {string} level - 日志级别 (INFO/WARN/ERROR)
 */
function log(msg, level = 'INFO') {
  if (CONFIG.logRequests) {
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    console.log(`[${timestamp}] [${level}] ${msg}`);
  }
}

/**
 * 生成 UUID v4
 * 对应原项目 uuid.uuid4()
 * CF Workers 环境优先使用 crypto.randomUUID()
 * @returns {string} UUID v4 字符串
 */
function generateUUID() {
  // CF Workers 支持 crypto.randomUUID()
  if (crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // 回退方案：手动生成 UUID v4
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

/**
 * 生成短 ID
 * 对应原项目 uuid.uuid4().hex[:n]
 * 取 UUID 的前 length 个字符（去掉连字符）
 * @param {number} length - ID 长度，默认 12
 * @returns {string} 短 ID 字符串
 */
function generateShortId(length = 12) {
  return generateUUID().replace(/-/g, '').substring(0, length);
}

/**
 * 获取当前 Unix 时间戳（秒）
 * 对应原项目 time.time()
 * @returns {number} Unix 时间戳
 */
function timestamp() {
  return Math.floor(Date.now() / 1000);
}

/**
 * 估算 Token 数量
 * 对应原项目 len(prompt)//4
 * 使用简单的启发式算法：约 4 字符 = 1 token
 * @param {string} text - 输入文本
 * @returns {number} 估算的 token 数量
 */
function estimateTokens(text) {
  if (!text) return 0;
  // 对应原项目 len(prompt)//4
  return Math.max(1, Math.ceil(text.length / 4));
}

/**
 * 生成 SAPISID 认证哈希
 * 对应原项目 make_sapisidhash 函数
 * Google API 使用基于时间的 SHA1 哈希进行认证
 * 格式: SAPISIDHASH {timestamp}_{sha1_hash}
 * @param {string} sapisid - SAPISID 值
 * @returns {Promise<string>} 认证哈希字符串
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
 * 获取多账户 URL 前缀
 * 对应原项目 account_prefix 函数
 * 多账户时 URL 为 /u/0, /u/1 等
 * @returns {string} URL 前缀，默认账户返回空字符串
 */
function getAccountPrefix() {
  const authUser = CONFIG.authUser;
  if (authUser === null || authUser === undefined || authUser === '') {
    return '';
  }
  return `/u/${authUser}`;
}

// ============================================================================
// Gemini API 请求构建 (对应原项目 gemini_stream_generate 中的构建逻辑)
// ============================================================================

/**
 * 构建 Gemini API 请求负载
 * 对应原项目 inner 数组构建逻辑
 * 
 * Gemini 内部 API 使用 80 个元素的嵌套数组结构：
 * - inner[0]: 用户消息和上下文
 * - inner[1]: 语言设置
 * - inner[17]: 思考模式配置
 * - inner[79]: 模型选择 (MODE_CATEGORY)
 * - 其他索引: 各种内部参数和标志
 * 
 * @param {string} prompt - 用户输入的提示文本
 * @param {number} modelId - 模型类别 ID
 * @param {number} thinkMode - 思考模式设置
 * @returns {string} URL 编码的请求体字符串
 */
function buildPayload(prompt, modelId, thinkMode) {
  // 创建 80 个元素的列表，初始化为 null
  // 对应原项目 inner = [None] * 80
  const inner = new Array(80).fill(null);

  // 对应原项目 inner[0] = [prompt, 0, None, None, None, None, 0]
  // 用户输入的消息和元数据
  inner[0] = [prompt, 0, null, null, null, null, 0];

  // 对应原项目 inner[1] = ["en"]
  // 语言设置为英语
  inner[1] = ['en'];

  // 对应原项目 inner[2] = ["", "", "", None, None, None, None, None, None, ""]
  // 对话上下文信息（空表示新对话）
  inner[2] = ['', '', '', null, null, null, null, null, null, ''];

  // 对应原项目 inner[6] = [0]
  // 连续对话标志
  inner[6] = [0];

  // 对应原项目 inner[7] = 1
  // 启用/禁用流式输出标志
  inner[7] = 1;

  // 对应原项目 inner[10] = 1
  // 启用流式输出
  inner[10] = 1;

  // 对应原项目 inner[11] = 0
  // 安全过滤级别（0=基础过滤）
  inner[11] = 0;

  // 对应原项目 inner[17] = [[think_mode]]
  // 思考模式设置
  inner[17] = [[thinkMode]];

  // 对应原项目 inner[18] = 0
  // 扩展思考标志
  inner[18] = 0;

  // 对应原项目 inner[27] = 1
  // 未知标志
  inner[27] = 1;

  // 对应原项目 inner[30] = [4]
  // 输出格式设置
  inner[30] = [4];

  // 对应原项目 inner[41] = [2]
  // 响应类型
  inner[41] = [2];

  // 对应原项目 inner[53] = 0
  // 未知标志
  inner[53] = 0;

  // 对应原项目 inner[59] = str(uuid.uuid4())
  // 唯一请求 ID
  inner[59] = generateUUID();

  // 对应原项目 inner[61] = []
  // 附件列表
  inner[61] = [];

  // 对应原项目 inner[68] = 1
  // 未知标志
  inner[68] = 1;

  // 对应原项目 inner[79] = model_id
  // 🔑 模型选择（关键字段）
  inner[79] = modelId;

  // 对应原项目 outer = [None, json.dumps(inner)]
  // 外层包装
  const outer = [null, JSON.stringify(inner)];

  // 对应原项目 params = {"f.req": json.dumps(outer)}
  // 构建 URL 参数
  const params = new URLSearchParams();
  params.append('f.req', JSON.stringify(outer));

  // 对应原项目 if CONFIG.get("xsrf_token"): params["at"] = CONFIG["xsrf_token"]
  // 可选：添加 XSRF 令牌
  if (CONFIG.xsrfToken) {
    params.append('at', CONFIG.xsrfToken);
  }

  return params.toString();
}

/**
 * 构建 Gemini API 请求 URL
 * 对应原项目 url 构建逻辑
 * 
 * URL 格式:
 * https://gemini.google.com{prefix}/_/BardChatUi/data/
 *   assistant.lamda.BardFrontendService/StreamGenerate
 *   ?bl={build_label}&hl=en&_reqid={request_id}&rt=c
 * 
 * @returns {string} 完整的请求 URL
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
 * 构建 Gemini API 请求头
 * 对应原项目 headers 构建逻辑
 * 
 * 包含浏览器伪装头、Cookie 认证、SAPISID 哈希等
 * 
 * @returns {Promise<Object>} HTTP 请求头对象
 */
async function buildHeaders() {
  const prefix = getAccountPrefix();

  // 对应原项目 headers 字典
  const headers = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'https://gemini.google.com',
    'Referer': `https://gemini.google.com${prefix}/app`,
    'X-Same-Domain': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
  };

  // 对应原项目 if prefix: headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
  // 多账户支持
  if (prefix) {
    headers['X-Goog-AuthUser'] = String(CONFIG.authUser);
  }

  // 对应原项目 cookie_str, sapisid = load_cookie()
  // if cookie_str: headers["Cookie"] = cookie_str
  // 添加 Cookie 认证
  if (CONFIG.cookieString) {
    headers['Cookie'] = CONFIG.cookieString;
  }

  // 对应原项目 if sapisid: headers["Authorization"] = make_sapisidhash(sapisid)
  // 添加 SAPISID 认证哈希
  if (CONFIG.sapisid) {
    headers['Authorization'] = await makeSapisidHash(CONFIG.sapisid);
  }

  return headers;
}

// ============================================================================
// 非流式 API 调用 (对应原项目 gemini_stream_generate)
// ============================================================================

/**
 * 非流式调用 Gemini API
 * 对应原项目 gemini_stream_generate 函数
 * 
 * 发送请求到 Gemini StreamGenerate 端点并获取完整响应
 * 支持自动重试、指数退避、错误处理
 * 
 * @param {string} prompt - 用户输入的提示文本
 * @param {number} modelId - 模型类别 ID
 * @param {number} thinkMode - 思考模式设置
 * @returns {Promise<string>} API 原始响应文本
 * @throws {Error} 所有重试失败后抛出异常
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
      const timeout = setTimeout(
        () => controller.abort(),
        CONFIG.requestTimeoutSec * 1000
      );

      const response = await fetch(url, {
        method: 'POST',
        headers,
        body,
        signal: controller.signal,
      });

      clearTimeout(timeout);

      // 处理特定 HTTP 状态码
      if (response.status === 405) {
        throw new Error('HTTP 405: Method Not Allowed - 可能 BL 版本过期，请更新 geminiBl');
      }
      if (response.status === 429) {
        const retryAfter = parseInt(response.headers.get('Retry-After') || '5');
        log(`收到 429 限流，等待 ${retryAfter} 秒后重试...`, 'WARN');
        if (attempt < CONFIG.retryAttempts - 1) {
          await new Promise(resolve => setTimeout(resolve, retryAfter * 1000));
          continue;
        }
        throw new Error('HTTP 429: Too Many Requests - 请添加有效的 Cookie 或降低请求频率');
      }
      if (response.status === 403) {
        throw new Error('HTTP 403: Forbidden - 可能需要有效的 Cookie 认证');
      }
      if (!response.ok) {
        const errorText = await response.text().catch(() => '');
        throw new Error(`HTTP ${response.status}: ${response.statusText} - ${errorText.substring(0, 200)}`);
      }

      // 对应原项目 resp.read().decode("utf-8", errors="replace")
      return await response.text();

    } catch (error) {
      lastError = error;

      // 对应原项目 if attempt < CONFIG["retry_attempts"] - 1: time.sleep(...)
      // 指数退避重试
      if (attempt < CONFIG.retryAttempts - 1) {
        log(`重试 ${attempt + 1}/${CONFIG.retryAttempts}: ${error.message}`, 'WARN');
        const delay = CONFIG.retryDelaySec * Math.pow(2, attempt) * 1000;
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }

  // 对应原项目 raise last_err
  throw lastError;
}

// ============================================================================
// 文本处理 (对应原项目 clean_gemini_text, extract_response_text)
// ============================================================================

/**
 * 清理 Gemini 响应中的代码执行痕迹
 * 对应原项目 clean_gemini_text 函数
 * 
 * Gemini 有时会在响应中包含代码执行参考和输出，
 * 这些应该被移除以获得干净的响应文本。
 * 
 * @param {string} text - 原始响应文本
 * @param {boolean} strip - 是否去除首尾空白，默认 true
 * @returns {string} 清理后的文本
 */
function cleanGeminiText(text, strip = true) {
  // 对应原项目 re.sub 清除代码执行块
  // 匹配格式: ```python?code_reference&code_event_index=0\n...```\n
  text = text.replace(
    /```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n[\s\S]*?```\n?/g,
    ''
  );

  // 对应原项目 return text.strip() if strip else text
  return strip ? text.trim() : text;
}

/**
 * 从 Gemini API 原始响应中提取最终文本
 * 对应原项目 extract_response_text 函数
 * 
 * 解析逻辑：
 * 1. 检查是否有 BardErrorInfo 错误
 * 2. 按行解析 JSON 数据
 * 3. 从嵌套的 JSON 结构中提取文本
 * 4. 返回最后一个非空文本（通常是完整的响应）
 * 
 * @param {string} raw - API 原始响应文本
 * @returns {string} 提取的最终文本
 * @throws {Error} 如果检测到 BardErrorInfo 错误
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
    // 跳过不相关的行
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
      // JSON 解析错误，继续处理下一行
    }
  }

  // 对应原项目 for t in reversed(texts): if t.strip(): text = t; break
  // 获取最后一个非空文本
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
// OpenAI 格式转换 (对应原项目 messages_to_prompt, parse_tool_calls)
// ============================================================================

/**
 * 将 OpenAI 消息列表转换为 Gemini 提示文本
 * 对应原项目 messages_to_prompt 函数
 * 
 * 转换规则：
 * - system 消息 -> [System instruction]: 前缀
 * - assistant 消息 -> [Assistant]: 前缀
 * - tool 消息 -> [Tool result for {name}]: 前缀
 * - user 消息 -> 直接使用内容
 * - 工具调用 -> tool_call 代码块格式
 * - 多条消息用双换行分隔
 * 
 * @param {Array} messages - OpenAI 格式的消息列表
 * @param {Array} tools - 可用的工具/函数定义列表，默认 null
 * @returns {string} 转换后的提示文本
 */
function messagesToPrompt(messages, tools = null) {
  const parts = [];

  // 对应原项目 if tools: 添加工具使用说明
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
    // 处理多模态消息（提取文本部分）
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
      // 处理工具调用
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
 * 从响应文本中解析工具调用
 * 对应原项目 parse_tool_calls 函数
 * 
 * 工具调用格式:
 * ```tool_call
 * {"name": "函数名", "arguments": {...}}
 * ```
 * 
 * @param {string} text - 可能包含工具调用的响应文本
 * @returns {Object} { cleanText: 清理后的文本, toolCalls: 工具调用数组 }
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
      // JSON 解析失败，跳过
    }
  }

  // 对应原项目 clean = re.sub(pattern, '', text, flags=re.DOTALL).strip()
  const cleanText = text.replace(pattern, '').trim();

  return { cleanText, toolCalls };
}

/**
 * Google 原生 API 格式转换为提示文本
 * 对应原项目 _google_contents_to_prompt 函数
 * 
 * 支持 Google Gemini CLI 的原生 API 格式
 * 
 * @param {Object} req - Google API 格式的请求对象
 * @returns {string} 转换后的提示文本
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
// 速率限制 (额外添加的保护措施，原项目没有)
// ============================================================================

// 内存存储，CF Workers 重启后重置
const rateLimitStore = {};

/**
 * 检查请求是否超过速率限制
 * 使用滑动窗口算法
 * 
 * @param {string} clientIP - 客户端 IP 地址
 * @returns {boolean} 是否允许请求
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
// API 密钥验证 (对应原项目 _authorized 方法)
// ============================================================================

/**
 * 验证 API 密钥
 * 对应原项目 _authorized 方法
 * 
 * 认证方式（按优先级）：
 * 1. Authorization: Bearer <key> 头
 * 2. x-api-key 头
 * 3. x-goog-api-key 头
 * 4. URL 查询参数 ?key=
 * 5. 如果未配置 api_keys，所有请求都通过
 * 
 * @param {Request} request - HTTP 请求对象
 * @returns {boolean} 是否通过认证
 */
function checkApiKey(request) {
  // 对应原项目 keys = CONFIG.get("api_keys") or []
  const keys = CONFIG.apiKeys || [];

  // 对应原项目 if not keys: return True
  // 未配置密钥时允许所有请求
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
// HTTP 响应构建 (对应原项目 GeminiHandler)
// ============================================================================

// CORS 响应头
const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

/**
 * 发送 JSON 响应
 * 对应原项目 send_json 方法
 * 
 * @param {Object} data - 响应数据
 * @param {number} status - HTTP 状态码，默认 200
 * @returns {Response} HTTP 响应对象
 */
function sendJSON(data, status = 200) {
  const body = JSON.stringify(data);
  return new Response(body, {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...corsHeaders,
    },
  });
}

/**
 * 发送 SSE 流式响应
 * 对应原项目流式处理
 * 
 * @param {ReadableStream} stream - 可读流对象
 * @returns {Response} HTTP 流式响应对象
 */
function sendSSE(stream) {
  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',  // 禁用 nginx 缓冲
      ...corsHeaders,
    },
  });
}

// ============================================================================
// 模型解析 (对应原项目 _resolve_model 方法)
// ============================================================================

/**
 * 解析模型名称，获取对应的配置参数
 * 对应原项目 _resolve_model 方法
 * 
 * 支持 @think= 参数覆盖思考模式
 * 例如: gemini-3.6-flash@think=0
 * 
 * @param {string} modelName - 模型名称
 * @returns {Object} { modelName, modelId, thinkMode, error }
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
// 核心请求处理 (对应原项目 handle_chat, handle_responses, _handle_google_generate)
// ============================================================================

/**
 * 处理 /v1/chat/completions 请求
 * 对应原项目 handle_chat 方法
 * 
 * 支持：
 * - 流式输出（SSE 打字机效果）
 * - 非流式输出
 * - 工具调用
 * 
 * SSE 格式严格符合 OpenAI 标准：
 * - 首块: delta: { role: 'assistant' }（不含 content）
 * - 内容块: delta: { content: '增量文本' }
 * - 结束块: delta: { content: "" }, finish_reason: 'stop'
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体
 * @returns {Promise<Response>} HTTP 响应对象
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

  const stream = body.stream === true;
  // 对应原项目 cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
  const chatId = `chatcmpl-${generateShortId(12)}`;

  log(`Chat: model=${modelName}, stream=${stream}, tokens≈${estimateTokens(prompt)}`);

  // ========================================================================
  // 非流式或带工具调用处理
  // ========================================================================
  if (!stream || tools) {
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

  // ========================================================================
  // 🔑 流式修复版（打字机效果）
  // ========================================================================
  // 直接转发 Gemini 的增量数据，实现逐字输出
  // 包含心跳保活机制，防止连接超时
  // ========================================================================

  const encoder = new TextEncoder();

  const streamBody = new ReadableStream({
    async start(controller) {
      // ---- 状态管理 ----
      let heartbeatTimer = null;
      let isFinished = false;

      /**
       * 清理心跳定时器
       */
      const clearHeartbeat = () => {
        if (heartbeatTimer) {
          clearInterval(heartbeatTimer);
          heartbeatTimer = null;
        }
      };

      /**
       * 安全结束流
       * 确保发送结束块和 [DONE] 标记
       * @param {string} reason - 结束原因 (stop/error)
       */
      const finishStream = (reason) => {
        if (isFinished) return;
        clearHeartbeat();
        isFinished = true;
        try {
          // 发送符合 OpenAI 标准的结束块
          // delta.content 必须为 "" 而非空对象 {}
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({
            id: chatId,
            object: 'chat.completion.chunk',
            created: timestamp(),
            model: modelName,
            choices: [{ index: 0, delta: { content: "" }, finish_reason: reason || 'stop' }],
          })}\n\n`));
          controller.enqueue(encoder.encode('data: [DONE]\n\n'));
          controller.close();
        } catch (e) {
          log(`Failed to finish stream: ${e.message}`, 'ERROR');
        }
      };

      try {
        // ---- 1. 发送 role 声明块 ----
        // 符合 OpenAI 标准：首块只包含 role，不含 content
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({
          id: chatId,
          object: 'chat.completion.chunk',
          created: timestamp(),
          model: modelName,
          choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }],
        })}\n\n`));

        // ---- 2. 启动心跳定时器 ----
        // 每 2 秒发送一次心跳注释，防止连接超时
        heartbeatTimer = setInterval(() => {
          if (!isFinished) {
            try {
              // SSE 注释格式：以冒号开头
              controller.enqueue(encoder.encode(': heartbeat\n\n'));
            } catch (e) {
              clearHeartbeat();
            }
          } else {
            clearHeartbeat();
          }
        }, 2000);

        // ---- 3. 构建 Gemini 请求 ----
        const body = buildPayload(prompt, modelId, thinkMode);
        const headers = await buildHeaders();
        const url = buildUrl();

        const fetchController = new AbortController();
        const fetchTimeout = setTimeout(
          () => fetchController.abort(),
          (CONFIG.requestTimeoutSec - 2) * 1000
        );

        try {
          // ---- 4. 发起请求 ----
          const response = await fetch(url, {
            method: 'POST',
            headers,
            body,
            signal: fetchController.signal,
          });
          clearTimeout(fetchTimeout);

          if (!response.ok) {
            const errorText = await response.text().catch(() => '');
            throw new Error(`HTTP ${response.status}: ${errorText.substring(0, 200)}`);
          }

          // ---- 5. 读取流式响应并实时转发增量数据 ----
          // 这是实现打字机效果的关键部分
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let prevText = '';  // 记录之前的完整文本，用于计算增量

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            // 解码新数据并追加到缓冲区
            buffer += decoder.decode(value, { stream: true });

            // 检查 Gemini 错误信息
            if (buffer.includes('BardErrorInfo')) {
              const match = buffer.match(/BardErrorInfo\s*\[(\d+)\]/);
              if (match) {
                throw new Error(`Gemini upstream rejected request: BardErrorInfo [${match[1]}]`);
              }
            }

            // 按行分割处理
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';  // 保留不完整的最后一行

            for (const line of lines) {
              // 跳过不相关的行
              if (!line.includes('"wrb.fr"') || line.length < 200) continue;

              try {
                // 解析 Gemini 的嵌套 JSON 响应
                const arr = JSON.parse(line);
                const innerStr = arr[0][2];
                if (!innerStr || innerStr.length < 50) continue;

                const inner2 = JSON.parse(innerStr);

                // 提取文本内容
                if (Array.isArray(inner2) && inner2.length > 4 && inner2[4]) {
                  for (const part of inner2[4]) {
                    if (Array.isArray(part) && part.length > 1 && part[1] && Array.isArray(part[1])) {
                      for (const t of part[1]) {
                        if (typeof t === 'string' && t.length > prevText.length) {
                          // 🔑 计算增量文本（新内容 = 当前全量 - 之前全量）
                          const delta = t.slice(prevText.length);
                          // 清理代码执行痕迹
                          const cleaned = cleanGeminiText(delta, false);
                          if (cleaned) {
                            // 🔑 立即发送增量块，实现打字机效果
                            controller.enqueue(encoder.encode(`data: ${JSON.stringify({
                              id: chatId,
                              object: 'chat.completion.chunk',
                              created: timestamp(),
                              model: modelName,
                              choices: [{ index: 0, delta: { content: cleaned }, finish_reason: null }],
                            })}\n\n`));
                          }
                          // 更新已发送的文本记录
                          prevText = t;
                        }
                      }
                    }
                  }
                }
              } catch (e) {
                // JSON 解析错误，继续处理下一行
              }
            }
          }
        } finally {
          clearTimeout(fetchTimeout);
        }

        // ---- 6. 正常结束流 ----
        finishStream('stop');

      } catch (error) {
        log(`Stream error: ${error.message}`, 'ERROR');
        // 尝试发送错误信息给客户端
        try {
          if (!isFinished) {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify({
              error: { message: error.message, type: 'upstream_error' }
            })}\n\n`));
          }
        } catch (e) {
          // 写入失败，忽略
        }
        // 结束流
        finishStream('error');
      }
    },

    /**
     * 客户端断开连接时的回调
     * 清理资源
     */
    cancel() {
      log('Client disconnected from stream');
    },
  });

  return sendSSE(streamBody);
}

/**
 * 处理 /v1/responses (OpenAI Responses API)
 * 对应原项目 handle_responses 方法
 * 
 * 用于 Codex CLI 等工具的兼容
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体
 * @returns {Promise<Response>} HTTP 响应对象
 */
async function handleResponses(request, body) {
  const resolved = resolveModel(body.model || CONFIG.defaultModel);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }

  const { modelName, modelId, thinkMode } = resolved;

  // 构建消息列表 (对应原项目处理逻辑)
  const messages = [];
  if (body.instructions) {
    messages.push({ role: 'system', content: body.instructions });
  }

  // 处理输入项
  const inputs = body.input || [];
  for (const item of (typeof inputs === 'string' ? [inputs] : inputs)) {
    if (typeof item === 'string') {
      // 简单字符串输入
      messages.push({ role: 'user', content: item });
    } else if (item.type === 'function_call_output') {
      // 函数调用输出
      messages.push({
        role: 'tool',
        tool_call_id: item.call_id,
        name: item.name,
        content: item.output,
      });
    } else {
      // 其他格式
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
        return {
          type: 'function',
          function: {
            name: t.name,
            description: t.description,
            parameters: t.parameters,
          },
        };
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
 * 对应原项目 _handle_google_generate 方法
 * 
 * 支持 Google Gemini CLI 的原生格式
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体
 * @param {boolean} stream - 是否使用流式传输
 * @returns {Promise<Response>} HTTP 响应对象
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
// 🔑 主入口 (对应原项目 main 函数和 GeminiHandler 类)
// ============================================================================

export default {
  /**
   * Cloudflare Workers 的 fetch 事件处理器
   * 对应原项目 HTTPServer + GeminiHandler 的功能
   * 
   * @param {Request} request - HTTP 请求对象
   * @param {Object} env - 环境变量
   * @param {Object} ctx - 执行上下文
   * @returns {Promise<Response>} HTTP 响应对象
   */
  async fetch(request, env, ctx) {
    // ========================================================================
    // 🔑 修复 1: OPTIONS CORS 预检请求优先处理
    // ========================================================================
    // 必须在所有其他逻辑之前处理
    // 浏览器在发送跨域 POST 请求前会先发送 OPTIONS 预检
    // 如果这里不处理，预检失败会导致 CORS 错误
    if (request.method === 'OPTIONS') {
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

    // ========================================================================
    // 从环境变量加载配置 (对应原项目 load_config 函数)
    // ========================================================================

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
    if (env.REQUEST_TIMEOUT_SEC) CONFIG.requestTimeoutSec = parseInt(env.REQUEST_TIMEOUT_SEC) || 28;

    // 速率限制配置
    if (env.RATE_LIMIT_MAX) CONFIG.rateLimit.maxRequests = parseInt(env.RATE_LIMIT_MAX) || 30;
    if (env.RATE_LIMIT_WINDOW) CONFIG.rateLimit.windowSec = parseInt(env.RATE_LIMIT_WINDOW) || 60;

    // ========================================================================
    // 请求路由处理
    // ========================================================================

    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    // ---- 速率限制检查 ----
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

    // ---- API 密钥验证 (仅 /v1 路径) ----
    // 对应原项目 _authorized 方法
    if (path.startsWith('/v1') && !checkApiKey(request)) {
      return sendJSON({
        error: { message: 'invalid api key' },
      }, 401);
    }

    // ========================================================================
    // GET 请求处理 (对应原项目 do_GET 方法)
    // ========================================================================

    if (method === 'GET') {
      // ---- 健康检查端点 ----
      if (path === '/' || path === '/health') {
        return sendJSON({
          status: 'ok',
          version: '1.2.0-cf-stream',
          platform: 'Cloudflare Workers',
          models: Object.keys(MODELS),
          defaultModel: CONFIG.defaultModel,
          hasCookie: !!CONFIG.cookieString,
          hasSapisid: !!CONFIG.sapisid,
          rateLimit: CONFIG.rateLimit,
        });
      }

      // ---- OpenAI 格式模型列表 ----
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

      // ---- Google 原生格式模型列表 ----
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

      // ---- 未匹配的 GET 请求 ----
      return sendJSON({ error: { message: 'not found' } }, 404);
    }

    // ========================================================================
    // POST 请求处理 (对应原项目 do_POST 方法)
    // ========================================================================

    if (method === 'POST') {
      // ---- 解析请求体 ----
      let body;
      try {
        body = await request.json();
      } catch (e) {
        return sendJSON({ error: { message: 'invalid JSON' } }, 400);
      }

      // ---- 路由: /v1/chat/completions ----
      // 对应原项目 /v1/chat/completions
      if (path === '/v1/chat/completions') {
        return handleChatCompletions(request, body);
      }

      // ---- 路由: /v1/responses (OpenAI Responses API) ----
      // 对应原项目 /v1/responses
      if (path === '/v1/responses') {
        return handleResponses(request, body);
      }

      // ---- 路由: Google 原生 generateContent ----
      // 对应原项目 :generateContent
      if (path.includes(':generateContent') && !path.includes('stream')) {
        return handleGoogleAPI(request, body, false);
      }

      // ---- 路由: Google 原生 streamGenerateContent ----
      // 对应原项目 :streamGenerateContent
      if (path.includes(':streamGenerateContent')) {
        return handleGoogleAPI(request, body, true);
      }

      // ---- 万能兜底：所有 /v1/ 下的 POST 都转为 chat 处理 ----
      // 兼容 NextChat 等客户端可能发送的不同路径
      if (path.startsWith('/v1/')) {
        return handleChatCompletions(request, body);
      }

      // ---- 未匹配的 POST 请求 ----
      return sendJSON({ error: { message: 'not found' } }, 404);
    }

    // ---- 未支持的方法 ----
    return sendJSON({ error: { message: 'method not allowed' } }, 405);
  },
};
