/**
 * Gemini Web2API - Cloudflare Workers 完整并发安全修复版（打字机效果）
 * 
 * ============================================================================
 * 项目说明
 * ============================================================================
 * 本程序将 Google Gemini 的 Web 界面转换为 OpenAI 兼容的 API 接口。
 * 部署于 Cloudflare Workers 边缘计算平台，无需服务器即可运行。
 * 支持流式输出（SSE 打字机效果）、非流式输出、工具调用（Function Calling）。
 * 
 * ============================================================================
 * 重大修复与增强 (并发安全 & 生产可用):
 * ============================================================================
 * 
 * 1. 彻底消除了全局 CONFIG 被异步请求并发篡改/串扰的严重隐患。
 *    根本原因分析：
 *    - Cloudflare Workers 使用 Isolate（隔离环境）处理请求
 *    - 冷启动时，全局作用域代码会重新执行，CONFIG 回到初始值
 *    - 热启动时（Isolate 复用），全局作用域代码不会重新执行
 *    - 当 WorkBuddy 等客户端在极短时间内发送多个并发请求时，
 *      它们会共享同一个全局 CONFIG 对象（因为复用同一个 Isolate）
 *    - 请求 A 修改了 CONFIG.cookieString = "cookie_a"
 *    - 请求 B 紧接着修改了 CONFIG.cookieString = "cookie_b"
 *    - 请求 A 后续使用的是 cookie_b，导致认证信息串扰
 *    - 这在 WorkBuddy 的多模型并发调用场景下尤为严重
 *    解决方案：
 *    - 每次请求通过 getRequestConfig(env) 创建全新的独立配置副本
 *    - 所有函数通过参数接收配置对象，完全不依赖全局可变状态
 *    - 使用 Object.freeze() 冻结默认配置模板，防止意外修改
 * 
 * 2. 实现了基于请求上下文 (Request-scoped) 的不可变配置机制。
 *    - DEFAULT_CONFIG 作为只读模板，使用 Object.freeze() 冻结
 *    - getRequestConfig(env) 为每个请求创建独立的配置副本
 *    - 从 env（环境变量，每个请求独立）加载定制配置
 *    - 所有函数签名都包含 config 参数，完全消除全局状态依赖
 *    - 支持从 COOKIE_STRING 环境变量自动提取 SAPISID 值
 * 
 * 3. 修复全局 rateLimitStore 在 Serverless 环境下的隐式内存泄露问题。
 *    - Serverless 环境下 Isolate 可能长时间存活
 *    - 如果不清理过期记录，Map 会无限增长导致内存泄漏
 *    - 使用随机概率清理机制（5% 概率触发全局清理）
 *    - 每次清理遍历所有键，删除过期或空的记录
 *    - 确保长期运行后内存使用保持稳定
 * 
 * 4. 增加了从 COOKIE_STRING 自动提取 SAPISID 的防御性逻辑。
 *    - 用户通常从浏览器复制完整 Cookie 字符串
 *    - Cookie 格式: "__Secure-1PSID=xxx; SAPISID=yyy; ..."
 *    - 如果用户设置了 COOKIE_STRING 但忘记单独设置 SAPISID
 *    - 程序会自动从 Cookie 字符串中正则提取 SAPISID 值
 *    - 提取逻辑：匹配 "SAPISID=" 后跟非分号字符的部分
 *    - 提升用户体验，减少配置错误
 * 
 * 5. OPTIONS 预检优先处理、SSE 打字机增量实时输出、心跳保活完全保留。
 *    - OPTIONS 预检在所有其他逻辑之前处理，确保 CORS 正常
 *    - SSE 格式严格符合 OpenAI 标准：
 *      - 首块: delta: { role: 'assistant' }（只含 role，不含 content）
 *      - 内容块: delta: { content: '增量文本' }（实时增量输出）
 *      - 结束块: delta: { content: "" }, finish_reason: 'stop'
 *    - 心跳保活：每 2 秒发送 ": heartbeat\n\n" SSE 注释
 *    - 打字机效果：实时计算 Gemini 响应的增量文本并立即推送
 * 
 * 6. 保留完整功能：工具调用、速率限制、API认证、Google原生API、Responses API。
 *    - 工具调用：支持 OpenAI Function Calling 格式
 *    - 速率限制：滑动窗口算法，可配置阈值和时间窗口
 *    - API 认证：支持 Bearer Token、x-api-key、x-goog-api-key、URL 参数
 *    - Google 原生 API：支持 Gemini CLI 的 generateContent 格式
 *    - Responses API：支持 OpenAI Codex CLI 的新格式
 * 
 * ============================================================================
 * 部署说明:
 * ============================================================================
 * 1. 登录 Cloudflare Dashboard -> Workers & Pages
 * 2. 创建 Worker -> 粘贴此代码 -> 保存并部署
 * 3. 配置环境变量(可选):
 *    - COOKIE_STRING: 完整的 Cookie 字符串（解决 429 限流）
 *      从浏览器 F12 -> Application -> Cookies 中复制
 *    - SAPISID: SAPISID 值
 *      如果未设置，会自动从 COOKIE_STRING 中提取
 *    - API_KEYS: API 密钥 JSON 数组，如 ["sk-gemini", "sk-my-key"]
 *      留空或设为 [] 表示不验证密钥
 *    - GEMINI_BL: Gemini 构建标签
 *      遇到 405 错误时需要更新此值
 *      获取方法：浏览器打开 gemini.google.com -> F12 -> Network -> 搜索 "boq_assistant"
 *    - DEFAULT_MODEL: 默认模型名称，如 "gemini-3.6-flash"
 *    - AUTH_USER: 多账户索引，0=第一个账户，1=第二个账户
 *    - RATE_LIMIT_MAX: 速率限制最大请求数，默认 3000
 *    - RATE_LIMIT_WINDOW: 速率限制时间窗口(秒)，默认 60
 *    - RETRY_ATTEMPTS: 重试次数，默认 3
 *    - RETRY_DELAY_SEC: 重试间隔(秒)，默认 2
 *    - REQUEST_TIMEOUT_SEC: 请求超时(秒)，默认 28
 * 
 * 客户端配置:
 *   基础URL: https://你的worker.workers.dev/v1
 *   API密钥: sk-gemini (或你在配置中设置的密钥)
 *   模型: gemini-3.6-flash
 * 
 * 基于原项目 gemini-web2api v1.1.0 移植
 * 原作者项目: https://github.com/your-repo/gemini-web2api
 */

// ============================================================================
// 🔒 默认配置 - 仅作为只读模板
// ============================================================================
// 这是所有请求配置的"蓝图"（Blueprint），用于生成每个请求的独立配置副本。
// 这个对象永远不会被修改，所有修改都在请求级的 config 副本中进行。
// 使用 Object.freeze() 确保不可变性，防止意外修改。

var DEFAULT_CONFIG = {
  // ---- 重试配置 ----
  // 当请求失败时，自动重试的次数
  // 每次重试使用指数退避策略：延迟时间 = retryDelaySec * 2^attempt
  retryAttempts: 3,
  // 重试间隔的基础时间（秒）
  // 第一次重试延迟 2 秒，第二次 4 秒，第三次 8 秒
  retryDelaySec: 2,

  // ---- 请求超时 ----
  // 单次 HTTP 请求的超时时间（秒）
  // 注意：CF Workers 免费版有 30 秒 CPU 时间限制
  // 流式请求的 CPU 时间在数据到达时重置，所以不受此限制
  requestTimeoutSec: 28,

  // ---- Gemini 构建标签 ----
  // Gemini 前端的版本标识，用于 API 请求的 URL 参数
  // 如果遇到 405 Method Not Allowed 错误，说明此值已过期
  // 更新方法：浏览器打开 gemini.google.com，按 F12 -> Network 标签
  // 在任意请求的 URL 中搜索 "boq_assistant"，复制最新版本号
  geminiBl: 'boq_assistant-bard-web-server_20260716.08_p0',

  // ---- 多账户支持 ----
  // Google 支持在同一个浏览器中登录多个账户
  // null 或 "" 表示使用默认账户（第一个登录的账户）
  // "0" 表示第一个账户，"1" 表示第二个账户，以此类推
  authUser: null,

  // ---- XSRF 令牌 ----
  // 跨站请求伪造保护令牌
  // Gemini Web 前端会使用此令牌，但 API 调用通常不需要
  // 如果遇到 403 错误，可以尝试从浏览器中提取此值
  xsrfToken: null,

  // ---- 默认模型 ----
  // 当客户端请求未指定模型时使用的默认模型
  // 可选值参考上方 MODELS 字典的键名
  defaultModel: 'gemini-3.6-flash',

  // ---- API 密钥白名单 ----
  // 用于验证客户端请求的密钥列表
  // 空数组 [] 表示不验证，所有请求都可以访问
  // 设置后，客户端必须在请求头中提供有效的密钥
  // 示例: ["sk-gemini", "sk-my-custom-key"]
  apiKeys: ['sk-gemini'],

  // ---- Cookie 认证 ----
  // Gemini 对匿名请求有严格的速率限制（容易触发 429）
  // 提供有效的 Cookie 可以大幅提升稳定性
  // cookieString: 从浏览器复制的完整 Cookie 字符串
  //   格式: "__Secure-1PSID=xxx; __Secure-3PSID=xxx; SAPISID=xxx; ..."
  cookieString: null,
  // sapisid: 从 Cookie 中提取的 SAPISID 值
  //   用于生成 Google API 所需的 SAPISIDHASH 认证头
  //   如果设置了 cookieString 但未设置 sapisid，程序会自动提取
  sapisid: null,

  // ---- 日志开关 ----
  // 是否在控制台输出请求日志
  // 生产环境建议保持开启，便于排查问题
  logRequests: true,

  // ---- 速率限制 ----
  // Cloudflare Workers 级别的请求频率控制
  // 用于防止滥用和保护上游 Gemini API
  rateLimit: {
    // 是否启用速率限制
    enabled: true,
    // 时间窗口内的最大请求数
    // 默认 3000，设置为较高值以避免正常使用被限制
    // 如果遇到滥用，可以调低此值
    maxRequests: 3000,
    // 时间窗口大小（秒）
    // 60 表示每分钟最多允许 maxRequests 个请求
    windowSec: 60,
  },
};

// ============================================================================
// 🤖 模型定义
// ============================================================================
// 映射自 Gemini Web 前端 JS 源码中的 MODE_CATEGORY 枚举
// 枚举值含义：
//   1 = FAST（快速模式）- Gemini Flash 系列
//   2 = THINKING（深度思考）- 启用深度推理
//   3 = PRO（专业版）- 需要有效 Cookie 才能正确路由
//   4 = AUTO（自动选择）- 由 Gemini 自动选择模型
//   5 = FAST_DYNAMIC_THINKING（动态思考）- 自适应思考深度
//   6 = FLASH_LITE（轻量快速）- 最轻量的模型
// 
// think 字段含义（思考模式）：
//   0 = 启用深度思考
//   4 = AUTO（自动选择思考深度）

var MODELS = {
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
// 🔑 核心：请求级配置生成器（解决并发串扰的核心函数）
// ============================================================================

/**
 * 为当前请求创建独立的配置副本
 * 
 * 【为什么需要这个函数？】
 * Cloudflare Workers 在处理请求时使用 Isolate（隔离环境）。
 * 冷启动时全局代码会重新执行，但热启动（Isolate 复用）时不会。
 * 如果多个并发请求复用了同一个 Isolate，它们会共享全局变量（如 CONFIG）。
 * 当 WorkBuddy 等客户端在极短时间内发送 5-20 个并发请求时，
 * 这些请求可能被分配到同一个 Isolate，导致配置串扰。
 * 
 * 【如何解决？】
 * 每次请求调用此函数，从冻结的 DEFAULT_CONFIG 模板创建一个全新的配置对象。
 * 然后用环境变量（env，每个请求独立）覆盖需要定制的字段。
 * 所有后续函数都通过 config 参数接收配置，完全不依赖全局状态。
 * 
 * 【配置项说明】
 * - 字符串类型（geminiBl, defaultModel）：有 env 就用，没有用默认值
 * - 认证类型（cookieString, sapisid 等）：可能为 null，必须显式覆盖防止残留
 * - 数字类型（retryAttempts 等）：需要 parseInt 转换
 * - 嵌套对象（rateLimit）：需要从冻结模板展开创建新的可变对象
 * 
 * @param {Object} env - Cloudflare Worker 环境变量（每个请求独立）
 * @returns {Object} 专属于当前请求的配置副本
 */
function getRequestConfig(env) {
  // 从默认模板创建全新的配置对象
  // 注意：不使用展开运算符 (...DEFAULT_CONFIG)，而是逐字段拷贝
  // 这样确保每个字段都是基本类型的独立副本
  var config = {
    // ---- 基本配置字段 ----
    retryAttempts: DEFAULT_CONFIG.retryAttempts,
    retryDelaySec: DEFAULT_CONFIG.retryDelaySec,
    requestTimeoutSec: DEFAULT_CONFIG.requestTimeoutSec,
    geminiBl: DEFAULT_CONFIG.geminiBl,
    authUser: DEFAULT_CONFIG.authUser,
    xsrfToken: DEFAULT_CONFIG.xsrfToken,
    defaultModel: DEFAULT_CONFIG.defaultModel,
    apiKeys: DEFAULT_CONFIG.apiKeys,
    cookieString: DEFAULT_CONFIG.cookieString,
    sapisid: DEFAULT_CONFIG.sapisid,
    logRequests: DEFAULT_CONFIG.logRequests,

    // ---- 嵌套对象：rateLimit 需要深拷贝 ----
    // 因为 rateLimit 本身是一个对象，直接赋值会导致引用共享
    // 这里创建一个新的对象，从 DEFAULT_CONFIG.rateLimit 复制所有属性
    rateLimit: {
      enabled: DEFAULT_CONFIG.rateLimit.enabled,
      maxRequests: DEFAULT_CONFIG.rateLimit.maxRequests,
      windowSec: DEFAULT_CONFIG.rateLimit.windowSec,
    },
  };

  // ================================================================
  // 环境变量覆盖（env 是 Cloudflare 为每个请求提供的独立环境变量）
  // ================================================================

  // ---- 字符串类型：有值才覆盖 ----
  // Gemini 构建标签
  if (env.GEMINI_BL) {
    config.geminiBl = env.GEMINI_BL;
  }
  // 默认模型
  if (env.DEFAULT_MODEL) {
    config.defaultModel = env.DEFAULT_MODEL;
  }

  // ---- 认证相关字段：使用 || 操作符确保显式覆盖 ----
  // 这些字段可能为 null 或空字符串
  // 使用 || null 确保即使 env 值为 undefined，也会显式设置为 null
  // 这防止了 Isolate 复用时，上次请求的值残留到本次请求
  config.cookieString = env.COOKIE_STRING || null;
  config.sapisid = env.SAPISID || null;
  config.authUser = env.AUTH_USER || null;
  config.xsrfToken = env.XSRF_TOKEN || null;

  // ================================================================
  // 🛡️ 智能兼容：自动从 COOKIE_STRING 提取 SAPISID
  // ================================================================
  // 如果用户设置了完整的 Cookie 字符串但忘记单独设置 SAPISID
  // 程序自动从 Cookie 中正则匹配提取 SAPISID 值
  // Cookie 格式示例:
  //   "__Secure-1PSID=AJDrVf...; __Secure-3PSID=AJDrVf...; SAPISID=abc123/def456; ..."
  // 正则 /SAPISID=([^;]+)/ 匹配 "SAPISID=" 后面的非分号字符
  if (!config.sapisid && config.cookieString) {
    var match = config.cookieString.match(/SAPISID=([^;]+)/);
    if (match) {
      // match[1] 是第一个捕获组，即 SAPISID 的值
      // trim() 去除可能的空白字符
      config.sapisid = match[1].trim();
    }
  }

  // ---- API 密钥：JSON 数组格式，需要特殊解析 ----
  // env.API_KEYS 是字符串，如 '["sk-gemini", "sk-my-key"]'
  if (env.API_KEYS) {
    try {
      config.apiKeys = JSON.parse(env.API_KEYS);
    } catch (e) {
      // JSON 解析失败时保留默认值，并输出错误日志
      console.error('[ERROR] API_KEYS 解析失败: ' + e.message + '，使用默认值');
    }
  }

  // ---- 数字类型字段：需要 parseInt 转换 ----
  // env 中的环境变量都是字符串类型，需要转换为数字
  // 使用 parseInt(value, 10) 确保十进制转换
  // 使用 isNaN() 检查转换结果，防止无效值

  // 重试次数
  if (env.RETRY_ATTEMPTS) {
    var ra = parseInt(env.RETRY_ATTEMPTS, 10);
    if (!isNaN(ra)) config.retryAttempts = ra;
  }
  // 重试延迟
  if (env.RETRY_DELAY_SEC) {
    var rd = parseInt(env.RETRY_DELAY_SEC, 10);
    if (!isNaN(rd)) config.retryDelaySec = rd;
  }
  // 请求超时
  if (env.REQUEST_TIMEOUT_SEC) {
    var rt = parseInt(env.REQUEST_TIMEOUT_SEC, 10);
    if (!isNaN(rt)) config.requestTimeoutSec = rt;
  }

  // ---- 速率限制配置 ----
  // 最大请求数
  if (env.RATE_LIMIT_MAX) {
    var rlmax = parseInt(env.RATE_LIMIT_MAX, 10);
    if (!isNaN(rlmax)) config.rateLimit.maxRequests = rlmax;
  }
  // 时间窗口
  if (env.RATE_LIMIT_WINDOW) {
    var rlwin = parseInt(env.RATE_LIMIT_WINDOW, 10);
    if (!isNaN(rlwin)) config.rateLimit.windowSec = rlwin;
  }

  // 返回请求专属的配置副本
  return config;
}

// ============================================================================
// 🛠 工具函数
// ============================================================================

/**
 * 日志记录函数
 * 
 * 使用请求级配置中的 logRequests 开关控制是否输出日志。
 * 如果没有传入 config 参数，使用默认配置。
 * 日志格式: [HH:MM:SS] [LEVEL] message
 * 
 * @param {string} msg - 要记录的日志消息
 * @param {string} [level] - 日志级别，默认 'INFO'。可选: INFO/WARN/ERROR
 * @param {Object} [config] - 请求级配置对象（可选，用于并发安全）
 */
function log(msg, level, config) {
  // 如果未指定级别，默认使用 INFO
  level = level || 'INFO';
  // 根据 config 参数决定是否输出日志
  // 有 config 时使用 config.logRequests，没有时使用默认配置
  var shouldLog = config ? config.logRequests : DEFAULT_CONFIG.logRequests;
  if (shouldLog) {
    // 生成时间戳，格式: HH:MM:SS
    var ts = new Date().toISOString().split('T')[1].split('.')[0];
    console.log('[' + ts + '] [' + level + '] ' + msg);
  }
}

/**
 * 生成 UUID v4（通用唯一标识符）
 * 
 * Cloudflare Workers 环境优先使用内置的 crypto.randomUUID() 方法。
 * 如果不可用（老版本或其他环境），使用回退方案手动生成。
 * 
 * @returns {string} UUID v4 格式的字符串，如 "550e8400-e29b-41d4-a716-446655440000"
 */
function generateUUID() {
  // 优先使用 CF Workers 内置方法（性能更好，随机性更强）
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // 回退方案：手动生成符合 UUID v4 规范的字符串
  // 格式: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    // 生成 0-15 的随机整数
    var r = Math.random() * 16 | 0;
    // x 位置直接使用随机值
    // y 位置确保高位为 10xx（符合 UUID v4 规范）
    var v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

/**
 * 生成短 ID
 * 
 * 从 UUID 中提取前 length 个十六进制字符（去掉连字符）。
 * 用于生成聊天补全 ID、工具调用 ID 等。
 * 
 * @param {number} [length] - 需要的 ID 长度，默认 12
 * @returns {string} 短 ID 字符串
 */
function generateShortId(length) {
  var len = length || 12;
  return generateUUID().replace(/-/g, '').substring(0, len);
}

/**
 * 获取当前 Unix 时间戳（秒）
 * 
 * @returns {number} 从 Unix 纪元（1970-01-01）开始的秒数
 */
function timestamp() {
  return Math.floor(Date.now() / 1000);
}

/**
 * 估算文本的 Token 数量
 * 
 * 使用简单的启发式算法：英文约 4 字符 = 1 token，中文约 1.5 字符 = 1 token。
 * 这里使用统一的 4 字符/token 估算，不是精确计算但足以用于资源预估。
 * 
 * @param {string} text - 要估算的文本
 * @returns {number} 估算的 token 数量，至少为 1
 */
function estimateTokens(text) {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

/**
 * 生成 SAPISID 认证哈希
 * 
 * Google API 使用基于时间的 SHA1 哈希进行认证。
 * 格式: SAPISIDHASH {timestamp}_{sha1_hex}
 * 
 * 算法步骤:
 * 1. 获取当前 Unix 时间戳
 * 2. 构造输入: "{timestamp} {sapisid} https://gemini.google.com"
 * 3. 使用 SHA-1 算法对输入进行哈希
 * 4. 返回格式化字符串: "SAPISIDHASH {ts}_{hex}"
 * 
 * @param {string} sapisid - 从 Google Cookie 中提取的 SAPISID 值
 * @returns {Promise<string>} 认证哈希字符串
 */
async function makeSapisidHash(sapisid) {
  // 获取当前时间戳
  var ts = timestamp();
  // 构造哈希输入（与 Google Web 前端完全一致）
  var input = ts + ' ' + sapisid + ' https://gemini.google.com';

  // 将输入字符串编码为 UTF-8 字节数组
  var encoder = new TextEncoder();
  var data = encoder.encode(input);

  // 使用 Web Crypto API 进行 SHA-1 哈希
  var hashBuffer = await crypto.subtle.digest('SHA-1', data);

  // 将哈希结果转换为十六进制字符串
  var hashArray = Array.from(new Uint8Array(hashBuffer));
  var hashHex = hashArray.map(function (b) {
    return b.toString(16).padStart(2, '0');
  }).join('');

  // 返回格式化的认证字符串
  return 'SAPISIDHASH ' + ts + '_' + hashHex;
}

/**
 * 获取多账户 URL 前缀
 * 
 * Google 支持在同一个浏览器中登录多个账户。
 * 当使用非默认账户时，Gemini 的 URL 路径会包含账户索引。
 * - 默认账户: https://gemini.google.com/app
 * - 第二个账户: https://gemini.google.com/u/1/app
 * - 第三个账户: https://gemini.google.com/u/2/app
 * 
 * @param {Object} config - 请求级配置对象
 * @returns {string} URL 前缀，如 "/u/1"，默认账户返回空字符串
 */
function getAccountPrefix(config) {
  var authUser = config.authUser;
  // 如果 authUser 为 null、undefined 或空字符串，使用默认账户
  if (authUser === null || authUser === undefined || authUser === '') {
    return '';
  }
  // 返回带前导斜杠的账户前缀
  return '/u/' + authUser;
}

// ============================================================================
// 📡 Gemini API 请求构建
// ============================================================================
// Gemini 的内部 API 使用复杂的嵌套数组结构。
// 以下函数负责构建与 Gemini Web 前端完全一致的请求负载和请求头。

/**
 * 构建 Gemini API 请求负载
 * 
 * Gemini 内部使用 80 个元素的嵌套数组作为请求体。
 * 关键字段说明:
 *   inner[0]: 用户消息和元数据 [prompt, index, image, attachment, metadata, context_id, is_new]
 *   inner[1]: 语言设置 ["en"]
 *   inner[2]: 对话上下文 [conv_id, resp_id, option_id, ...]
 *   inner[6]: 连续对话标志 [0]
 *   inner[7]: 流式输出标志 1
 *   inner[10]: 流式输出标志 1
 *   inner[11]: 安全过滤级别 0（基础过滤）
 *   inner[17]: 思考模式 [[thinkMode]]
 *   inner[18]: 扩展思考标志 0
 *   inner[30]: 输出格式 [4]
 *   inner[41]: 响应类型 [2]
 *   inner[59]: 唯一请求 ID（UUID）
 *   inner[61]: 附件列表 []
 *   inner[79]: 模型选择（MODE_CATEGORY 枚举值）⭐ 最关键
 * 
 * @param {string} prompt - 用户输入的提示文本
 * @param {number} modelId - 模型类别 ID（MODE_CATEGORY 枚举值: 1-6）
 * @param {number} thinkMode - 思考模式设置（0=深度思考, 4=自动）
 * @param {Object} config - 请求级配置对象
 * @returns {string} URL 编码的请求体字符串，格式为 "f.req=..."
 */
function buildPayload(prompt, modelId, thinkMode, config) {
  // 创建 80 个元素的数组，所有元素初始化为 null
  // 这是 Gemini Web 前端实际使用的数据结构
  var inner = new Array(80).fill(null);

  // --- 用户消息 ---
  // [prompt, 0, None, None, None, None, 0]
  // prompt: 用户输入的文本
  // 0: 消息索引/序列号
  // None: 图片数据（可选）
  // None: 附件信息（可选）
  // None: 元数据（可选）
  // None: 上下文 ID（可选）
  // 0: 是否为新对话的标志
  inner[0] = [prompt, 0, null, null, null, null, 0];

  // --- 语言设置 ---
  inner[1] = ['en'];

  // --- 对话上下文 ---
  // 空字符串表示新对话，null 表示未设置
  inner[2] = ['', '', '', null, null, null, null, null, null, ''];

  // --- 连续对话标志 ---
  inner[6] = [0];

  // --- 流式输出标志 ---
  inner[7] = 1;    // 启用流式
  inner[10] = 1;   // 流式输出

  // --- 安全过滤级别 ---
  // 0 = 基础过滤（推荐）
  // 1 = 严格过滤
  // 2 = 最严格过滤
  inner[11] = 0;

  // --- 思考模式配置 ---
  // 双层嵌套数组: [[thinkMode]]
  inner[17] = [[thinkMode]];

  // --- 扩展思考标志 ---
  inner[18] = 0;

  // --- 各种内部参数 ---
  inner[27] = 1;   // 未知标志
  inner[30] = [4]; // 输出格式
  inner[41] = [2]; // 响应类型
  inner[53] = 0;   // 未知标志

  // --- 唯一请求 ID ---
  // 使用 UUID v4 确保每次请求都有唯一标识
  inner[59] = generateUUID();

  // --- 附件列表 ---
  // 空数组表示没有附件
  inner[61] = [];

  // --- 其他设置 ---
  inner[68] = 1;   // 未知标志

  // ⭐ 模型选择（最关键字段）
  // MODE_CATEGORY 枚举值:
  //   1=FAST, 2=THINKING, 3=PRO, 4=AUTO
  //   5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE
  inner[79] = modelId;

  // --- 外层包装 ---
  // Gemini 的请求体是双层嵌套 JSON:
  // 外层: [null, inner_json_string]
  var outer = [null, JSON.stringify(inner)];

  // --- 构建 URL 编码参数 ---
  var params = new URLSearchParams();
  // 主要数据放在 f.req 参数中
  params.append('f.req', JSON.stringify(outer));

  // 可选：添加 XSRF 令牌
  // 通常不需要，但某些情况下可能需要
  if (config.xsrfToken) {
    params.append('at', config.xsrfToken);
  }

  // 返回 URL 编码的字符串
  return params.toString();
}

/**
 * 构建 Gemini API 请求 URL
 * 
 * URL 格式:
 * https://gemini.google.com{prefix}/_/BardChatUi/data/
 *   assistant.lamda.BardFrontendService/StreamGenerate
 *   ?bl={build_label}&hl=en&_reqid={request_id}&rt=c
 * 
 * 参数说明:
 * - bl (build label): Gemini 前端构建版本标识
 * - hl (host language): 界面语言，固定为 en
 * - _reqid: 请求 ID，使用时间戳后 6 位
 * - rt: 请求类型，c 表示普通请求
 * 
 * @param {Object} config - 请求级配置对象
 * @returns {string} 完整的请求 URL
 */
function buildUrl(config) {
  // 获取多账户 URL 前缀
  var prefix = getAccountPrefix(config);
  // 生成请求 ID（使用时间戳的后 6 位数字）
  var reqid = timestamp() % 1000000;
  // 拼接完整 URL
  return 'https://gemini.google.com' + prefix +
    '/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate' +
    '?bl=' + config.geminiBl +
    '&hl=en' +
    '&_reqid=' + reqid +
    '&rt=c';
}

/**
 * 构建 Gemini API 请求头
 * 
 * 包含完整的浏览器伪装头，让请求看起来像从 Gemini 网页内部发出的。
 * 支持 Cookie 认证和 SAPISID 哈希认证。
 * 
 * 请求头说明:
 * - Content-Type: 标准表单提交格式
 * - Origin/Referer: 声明请求来源
 * - X-Same-Domain: 告诉后端这是同域请求
 * - User-Agent: 伪装成 Chrome 浏览器
 * - Sec-* 系列: 浏览器安全策略头
 * - Cookie: 可选的认证 Cookie
 * - Authorization: 可选的 SAPISID 认证哈希
 * 
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Object>} HTTP 请求头对象
 */
async function buildHeaders(config) {
  // 获取多账户 URL 前缀
  var prefix = getAccountPrefix(config);

  // --- 基础请求头 ---
  var headers = {
    // 标准表单提交格式
    'Content-Type': 'application/x-www-form-urlencoded',
    // 声明请求来源域
    'Origin': 'https://gemini.google.com',
    // 声明引用页面
    'Referer': 'https://gemini.google.com' + prefix + '/app',
    // 同域请求标志
    'X-Same-Domain': '1',
    // 浏览器伪装（Chrome 127）
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    // 接受任意响应类型
    'Accept': '*/*',
    // 接受的语言
    'Accept-Language': 'en-US,en;q=0.9',
    // 浏览器安全策略头
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
  };

  // --- 多账户支持 ---
  // 如果使用了非默认账户，添加认证用户头
  if (prefix) {
    headers['X-Goog-AuthUser'] = String(config.authUser);
  }

  // --- Cookie 认证 ---
  // 如果配置了 Cookie 字符串，添加到请求头中
  // Cookie 可以显著提升请求的稳定性和路由质量
  if (config.cookieString) {
    headers['Cookie'] = config.cookieString;
  }

  // --- SAPISID 认证哈希 ---
  // 如果配置了 SAPISID，生成基于时间的认证哈希
  // 这个哈希验证请求来自合法的 Google 用户会话
  if (config.sapisid) {
    headers['Authorization'] = await makeSapisidHash(config.sapisid);
  }

  return headers;
}

// ============================================================================
// 📡 非流式 API 调用
// ============================================================================

/**
 * 非流式调用 Gemini API
 * 
 * 发送请求到 Gemini StreamGenerate 端点并等待完整响应。
 * 支持自动重试、指数退避、详细的错误处理。
 * 
 * 重试策略:
 * - 使用指数退避算法
 * - 第一次重试: 等待 retryDelaySec 秒
 * - 第二次重试: 等待 retryDelaySec * 2 秒
 * - 第三次重试: 等待 retryDelaySec * 4 秒
 * 
 * 错误处理:
 * - 405: BL 版本过期，需要更新 geminiBl 配置
 * - 429: 请求频率超限，等待 Retry-After 秒后重试
 * - 403: 需要有效的 Cookie 认证
 * - 其他: 记录错误信息并重试
 * 
 * @param {string} prompt - 用户输入的提示文本
 * @param {number} modelId - 模型类别 ID
 * @param {number} thinkMode - 思考模式设置
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<string>} API 原始响应文本（包含嵌套 JSON）
 * @throws {Error} 所有重试失败后抛出最后的错误
 */
async function geminiStreamGenerate(prompt, modelId, thinkMode, config) {
  // 构建请求负载
  var body = buildPayload(prompt, modelId, thinkMode, config);
  // 构建请求头
  var headers = await buildHeaders(config);
  // 构建请求 URL
  var url = buildUrl(config);

  // 保存最后一次错误，所有重试失败后抛出
  var lastError;

  // 重试循环
  for (var attempt = 0; attempt < config.retryAttempts; attempt++) {
    try {
      // 创建 AbortController 用于超时控制
      var controller = new AbortController();
      // 设置超时定时器
      var timeout = setTimeout(function () {
        controller.abort();  // 超时后中止请求
      }, config.requestTimeoutSec * 1000);

      // 发送 HTTP POST 请求
      var response = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: body,
        signal: controller.signal,  // 关联中止信号
      });

      // 请求成功，清除超时定时器
      clearTimeout(timeout);

      // ============================================================
      // 错误状态码处理
      // ============================================================

      // 405 Method Not Allowed: BL 版本过期
      // Gemini 更新了前端，需要同步更新 geminiBl 配置
      if (response.status === 405) {
        throw new Error('HTTP 405: Method Not Allowed - 可能 BL 版本过期，请更新 geminiBl');
      }

      // 429 Too Many Requests: 请求频率超限
      // 等待服务器指定的时间后重试
      if (response.status === 429) {
        // 从响应头获取重试等待时间（秒）
        var retryAfter = parseInt(response.headers.get('Retry-After') || '5', 10);
        log('收到 429 限流，等待 ' + retryAfter + ' 秒后重试...', 'WARN', config);
        // 如果还有重试机会，等待后继续
        if (attempt < config.retryAttempts - 1) {
          await new Promise(function (resolve) {
            setTimeout(resolve, retryAfter * 1000);
          });
          continue;  // 跳过本次，进入下一次重试
        }
        // 没有重试机会了，抛出错误
        throw new Error('HTTP 429: Too Many Requests - 请添加有效的 Cookie 或降低请求频率');
      }

      // 403 Forbidden: 需要认证
      if (response.status === 403) {
        throw new Error('HTTP 403: Forbidden - 可能需要有效的 Cookie 认证');
      }

      // 其他 HTTP 错误
      if (!response.ok) {
        var errorText = '';
        try {
          errorText = await response.text();
        } catch (e) {
          errorText = '无法读取错误信息';
        }
        throw new Error('HTTP ' + response.status + ': ' + errorText.substring(0, 200));
      }

      // 请求成功，返回响应文本
      return await response.text();

    } catch (error) {
      // 保存错误信息
      lastError = error;

      // 如果还有重试机会，等待后重试
      if (attempt < config.retryAttempts - 1) {
        log('重试 ' + (attempt + 1) + '/' + config.retryAttempts + ': ' + error.message, 'WARN', config);
        // 指数退避: 延迟时间 = 基础延迟 * 2^attempt
        var delay = config.retryDelaySec * Math.pow(2, attempt) * 1000;
        await new Promise(function (resolve) {
          setTimeout(resolve, delay);
        });
      }
    }
  }

  // 所有重试都失败，抛出最后的错误
  throw lastError;
}

// ============================================================================
// 📝 文本处理
// ============================================================================

/**
 * 清理 Gemini 响应中的代码执行痕迹
 * 
 * Gemini 有时会在响应中包含代码执行参考和输出块，格式如下:
 * ```python?code_reference&code_event_index=0
 * ...代码...
 * ```
 * ```javascript?code_stdout&code_event_index=1
 * ...输出...
 * ```
 * 这些应该被移除以获得干净的响应文本。
 * 
 * @param {string} text - 原始响应文本
 * @param {boolean} [strip] - 是否去除首尾空白，默认 true
 * @returns {string} 清理后的文本
 */
function cleanGeminiText(text, strip) {
  // 如果未指定 strip 参数，默认值为 true
  if (strip === undefined) strip = true;

  // 移除代码执行块
  // 正则说明:
  // - ```(?:python|javascript|text): 匹配代码块开始
  // - \?code_(?:reference|stdout)&code_event_index=\d+: 匹配代码执行参数
  // - \n[\s\S]*?```: 匹配代码块内容（非贪婪）到结束标记
  // - \n?: 匹配可能存在的换行
  text = text.replace(
    /```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n[\s\S]*?```\n?/g,
    ''
  );

  // 根据 strip 参数决定是否去除首尾空白
  return strip ? text.trim() : text;
}

/**
 * 从 Gemini API 原始响应中提取最终文本
 * 
 * 解析逻辑:
 * 1. 检查是否有 BardErrorInfo 错误
 * 2. 按行分割原始响应
 * 3. 跳过不相关的行（不含 "wrb.fr" 或太短的行）
 * 4. 解析每行的 JSON 数据（双层嵌套）
 * 5. 从 inner[4] 中提取文本内容
 * 6. 返回最后一个非空文本（通常是最终的完整响应）
 * 
 * 数据结构说明:
 * 每行是一个 JSON 数组: [["wrb.fr", "[[...]]", ...], ...]
 * 其中第二个元素是内层 JSON 字符串: "[[...]]"
 * 内层 JSON 的 inner[4] 包含对话内容
 * inner[4] 的每个元素是 [type, [text1, text2, ...]]
 * 
 * @param {string} raw - API 原始响应文本
 * @returns {string} 提取的最终文本
 * @throws {Error} 如果检测到 BardErrorInfo 错误
 */
function extractResponseText(raw) {
  // 检查 BardErrorInfo 错误
  // 格式: BardErrorInfo [错误代码]
  var bardErr = raw.match(/BardErrorInfo\s*\[(\d+)\]/);
  if (bardErr) {
    throw new Error('Gemini upstream rejected request: BardErrorInfo [' + bardErr[1] + ']');
  }

  // 收集所有提取到的文本片段
  var texts = [];

  // 按行分割原始响应
  var lines = raw.split('\n');
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];

    // 跳过不包含 "wrb.fr" 的行（不是数据行）
    // 跳过长度小于 200 的行（太短，不包含有效数据）
    if (line.indexOf('"wrb.fr"') === -1 || line.length < 200) continue;

    try {
      // 解析外层 JSON
      var arr = JSON.parse(line);
      // 提取内层 JSON 字符串
      var innerStr = arr[0][2];

      // 跳过空的或太短的内层 JSON
      if (!innerStr || innerStr.length < 50) continue;

      // 解析内层 JSON
      var inner = JSON.parse(innerStr);

      // 检查 inner[4] 是否存在且包含内容
      if (Array.isArray(inner) && inner.length > 4 && inner[4]) {
        var parts = inner[4];
        // 遍历 inner[4] 的每个部分
        for (var j = 0; j < parts.length; j++) {
          var part = parts[j];
          // part[1] 包含文本数据
          if (Array.isArray(part) && part.length > 1 && part[1]) {
            if (Array.isArray(part[1])) {
              var textItems = part[1];
              // 遍历文本项
              for (var k = 0; k < textItems.length; k++) {
                var t = textItems[k];
                // 收集非空字符串
                if (typeof t === 'string' && t.length > 0) {
                  texts.push(t);
                }
              }
            }
          }
        }
      }
    } catch (e) {
      // JSON 解析错误，可能是响应不完整，继续处理下一行
    }
  }

  // 获取最后一个非空文本
  // Gemini 的响应是逐步累积的，最后一个通常包含完整文本
  var text = '';
  for (var m = texts.length - 1; m >= 0; m--) {
    if (texts[m].trim()) {
      text = texts[m];
      break;
    }
  }

  // 清理代码执行痕迹并返回
  return cleanGeminiText(text);
}

// ============================================================================
// 🔄 OpenAI 格式转换
// ============================================================================

/**
 * 将 OpenAI 消息列表转换为 Gemini 提示文本
 * 
 * 转换规则:
 * - system 角色 -> "[System instruction]: {content}"
 * - assistant 角色 -> "[Assistant]: {content}"
 * - tool 角色 -> "[Tool result for {name}]: {content}"
 * - user 角色 -> 直接使用 {content}
 * - 工具调用 -> ```tool_call\n{json}\n``` 代码块格式
 * 
 * 多条消息之间使用双换行（\n\n）分隔。
 * 
 * @param {Array} messages - OpenAI 格式的消息列表
 *   每条消息格式: { role: string, content: string|array }
 * @param {Array} [tools] - 可用的工具/函数定义列表
 *   每个工具格式: { type: "function", function: { name, description, parameters } }
 * @returns {string} 转换后的提示文本
 */
function messagesToPrompt(messages, tools) {
  // 存储各个消息段的数组
  var parts = [];

  // ================================================================
  // 添加工具使用说明
  // ================================================================
  if (tools && tools.length > 0) {
    // 标准化工具定义格式
    var toolDefs = [];
    for (var ti = 0; ti < tools.length; ti++) {
      var tool = tools[ti];
      // 兼容两种格式: { type: "function", function: {...} } 和 { name: "...", ... }
      var fn = (tool.type === 'function') ? (tool.function || tool) : tool;
      toolDefs.push({
        name: fn.name || tool.name || '',
        description: fn.description || tool.description || '',
        parameters: fn.parameters || tool.parameters || {},
      });
    }

    // 构建工具使用说明
    parts.push(
      '[System instruction]: You have access to tools. ' +
      'To call a tool, respond with:\n' +
      '```tool_call\n{"name": "func_name", "arguments": {...}}\n```\n' +
      'Only use tool_call blocks when needed.\n\n' +
      'Available tools:\n' + JSON.stringify(toolDefs, null, 2)
    );
  }

  // ================================================================
  // 处理每条消息
  // ================================================================
  for (var mi = 0; mi < messages.length; mi++) {
    var msg = messages[mi];
    var role = msg.role || 'user';
    var content = msg.content || '';

    // 如果内容是数组（多模态消息），提取文本部分
    if (Array.isArray(content)) {
      var textParts = [];
      for (var ci = 0; ci < content.length; ci++) {
        var c = content[ci];
        // 只提取文本类型的内容
        if (c.type === 'text' || c.type === 'input_text') {
          textParts.push(c.text || '');
        }
      }
      content = textParts.join(' ');
    }

    // 根据角色进行不同的格式化
    if (role === 'system') {
      // 系统消息：添加指令前缀
      parts.push('[System instruction]: ' + content);
    } else if (role === 'assistant') {
      // 助手消息：检查是否包含工具调用
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        // 将工具调用转换为代码块格式
        var tcStrs = [];
        for (var tci = 0; tci < msg.tool_calls.length; tci++) {
          var tc = msg.tool_calls[tci];
          var fn = tc.function || {};
          tcStrs.push(
            '```tool_call\n' +
            '{"name": "' + fn.name + '", "arguments": ' + (fn.arguments || '{}') + '}\n' +
            '```'
          );
        }
        parts.push('[Assistant]: ' + (content || '') + '\n' + tcStrs.join('\n'));
      } else {
        parts.push('[Assistant]: ' + content);
      }
    } else if (role === 'tool') {
      // 工具响应：添加结果前缀
      parts.push('[Tool result for ' + (msg.name || 'unknown') + ']: ' + content);
    } else {
      // 用户消息：直接使用内容
      parts.push(content || '');
    }
  }

  // 用双换行连接所有部分，过滤空字符串
  return parts.filter(function (p) { return p; }).join('\n\n');
}

/**
 * 从响应文本中解析工具调用
 * 
 * 工具调用格式:
 * ```tool_call
 * {"name": "函数名", "arguments": {"参数名": "参数值"}}
 * ```
 * 
 * @param {string} text - 可能包含工具调用的响应文本
 * @returns {Object} { cleanText: string, toolCalls: Array }
 *   - cleanText: 移除工具调用块后的纯文本
 *   - toolCalls: 解析出的工具调用对象数组
 */
function parseToolCalls(text) {
  var toolCalls = [];

  // 正则匹配 tool_call 代码块
  // /```tool_call\s*\n(.*?)\n```/gs
  // g: 全局匹配（查找所有匹配项）
  // s: 允许 . 匹配换行符
  var pattern = /```tool_call\s*\n(.*?)\n```/gs;
  var match;

  // 循环提取所有工具调用
  while ((match = pattern.exec(text)) !== null) {
    try {
      // 解析 JSON 数据
      var data = JSON.parse(match[1].trim());

      // 构建 OpenAI 格式的工具调用对象
      toolCalls.push({
        id: 'call_' + generateShortId(8),  // 生成唯一调用 ID
        type: 'function',
        function: {
          name: data.name,
          arguments: JSON.stringify(data.arguments || {}),
        },
      });
    } catch (e) {
      // JSON 解析失败，跳过格式有误的块
    }
  }

  // 从文本中移除所有 tool_call 块
  var cleanText = text.replace(pattern, '').trim();

  return {
    cleanText: cleanText,
    toolCalls: toolCalls
  };
}

/**
 * Google 原生 API 格式转换为提示文本
 * 
 * 支持 Google Gemini CLI 的原生 API 格式。
 * 格式:
 * {
 *   "systemInstruction": { "parts": [{"text": "..."}] },
 *   "contents": [
 *     { "role": "user", "parts": [{"text": "..."}] },
 *     { "role": "model", "parts": [{"text": "..."}] }
 *   ]
 * }
 * 
 * @param {Object} req - Google API 格式的请求对象
 * @returns {string} 转换后的提示文本
 */
function googleContentsToPrompt(req) {
  var parts = [];

  // 处理系统指令
  var sysInst = req.systemInstruction;
  if (sysInst && sysInst.parts) {
    var sysTextParts = [];
    for (var si = 0; si < sysInst.parts.length; si++) {
      var sp = sysInst.parts[si];
      if (sp.text) sysTextParts.push(sp.text);
    }
    var sysText = sysTextParts.join(' ');
    if (sysText) {
      parts.push('[System instruction]: ' + sysText);
    }
  }

  // 处理对话内容
  var contents = req.contents || [];
  for (var ci = 0; ci < contents.length; ci++) {
    var content = contents[ci];
    var role = content.role || 'user';
    var textParts = [];
    var partsArr = content.parts || [];
    for (var pi = 0; pi < partsArr.length; pi++) {
      if (partsArr[pi].text) textParts.push(partsArr[pi].text);
    }
    var text = textParts.join(' ');

    // model 角色转换为 Assistant 前缀
    if (role === 'model') {
      parts.push('[Assistant]: ' + text);
    } else {
      parts.push(text);
    }
  }

  return parts.filter(function (p) { return p; }).join('\n\n');
}

// ============================================================================
// 🚦 速率限制（Serverless 安全的内存存储）
// ============================================================================

// 使用 Map 数据结构存储每个 IP 的请求历史
// Map 支持高效的增删改查操作
var rateLimitStore = new Map();

/**
 * 检查请求是否超过速率限制
 * 
 * 使用滑动窗口算法:
 * 1. 获取当前时间和该 IP 的历史请求记录
 * 2. 过滤出时间窗口内的请求
 * 3. 如果请求数超过阈值，拒绝
 * 4. 否则记录本次请求并允许
 * 
 * 内存管理:
 * - 每次检查时有 5% 的概率触发全局清理
 * - 清理所有过期或空的记录
 * - 防止长时间运行后内存无限增长
 * 
 * @param {string} clientIP - 客户端 IP 地址
 * @param {Object} config - 请求级配置对象
 * @returns {boolean} true 表示允许请求，false 表示被限流
 */
function checkRateLimit(clientIP, config) {
  // 如果速率限制未启用，直接允许
  if (!config.rateLimit || !config.rateLimit.enabled) return true;

  var now = Date.now();
  // 计算时间窗口的毫秒数
  var windowMs = config.rateLimit.windowSec * 1000;
  // 生成存储键
  var key = 'rl:' + clientIP;

  // 获取该 IP 的历史记录，并过滤出当前窗口内的请求
  var timestamps = (rateLimitStore.get(key) || []).filter(function (t) {
    return now - t < windowMs;
  });

  // 如果窗口内的请求数达到或超过阈值，拒绝
  if (timestamps.length >= config.rateLimit.maxRequests) {
    return false;
  }

  // 记录本次请求的时间戳
  timestamps.push(now);
  rateLimitStore.set(key, timestamps);

  // ================================================================
  // 🛡️ 随机概率清理过期键（5% 概率触发）
  // ================================================================
  // 防止长期高并发运行后，大量冷 IP 的记录残留内存
  // 5% 的概率确保不会频繁执行清理操作
  if (Math.random() < 0.05) {
    // 遍历所有 IP 的记录
    rateLimitStore.forEach(function (v, k) {
      // 过滤出有效的（未过期的）记录
      var valid = v.filter(function (t) {
        return now - t < windowMs;
      });
      if (valid.length === 0) {
        // 如果该 IP 已没有任何有效记录，删除整个条目
        rateLimitStore.delete(k);
      } else {
        // 更新为只包含有效记录的数组
        rateLimitStore.set(k, valid);
      }
    });
  }

  return true;
}

// ============================================================================
// 🔐 API 密钥验证
// ============================================================================

/**
 * 验证 API 密钥
 * 
 * 支持多种认证方式（按优先级）:
 * 1. Authorization: Bearer <key> 标准 Bearer Token 认证
 * 2. x-api-key: <key> 自定义请求头
 * 3. x-goog-api-key: <key> Google 风格请求头
 * 4. ?key=<key> URL 查询参数
 * 
 * 如果 apiKeys 为空数组，表示不验证，所有请求都允许。
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} config - 请求级配置对象
 * @returns {boolean} true 表示通过认证，false 表示认证失败
 */
function checkApiKey(request, config) {
  // 获取 API 密钥白名单
  var keys = config.apiKeys || [];

  // 如果未配置密钥，允许所有请求
  if (keys.length === 0) return true;

  // --- 方式 1: Authorization: Bearer <key> ---
  var auth = request.headers.get('Authorization') || '';
  // 检查是否以 "Bearer " 开头，且后续的 token 在白名单中
  if (auth.indexOf('Bearer ') === 0) {
    var token = auth.slice(7);  // 去掉 "Bearer " 前缀
    if (keys.indexOf(token) !== -1) return true;
  }

  // --- 方式 2 & 3: x-api-key / x-goog-api-key ---
  var headerNames = ['x-api-key', 'x-goog-api-key'];
  for (var i = 0; i < headerNames.length; i++) {
    var value = request.headers.get(headerNames[i]) || '';
    if (keys.indexOf(value) !== -1) return true;
  }

  // --- 方式 4: URL 查询参数 ?key= ---
  var url = new URL(request.url);
  var keyParam = url.searchParams.get('key');
  if (keyParam && keys.indexOf(keyParam) !== -1) return true;

  // 所有认证方式都失败
  return false;
}

// ============================================================================
// 📤 HTTP 响应构建
// ============================================================================

/**
 * 发送 JSON 格式的 HTTP 响应
 * 
 * 自动设置 CORS 头，允许跨域访问。
 * 
 * @param {Object} data - 要发送的响应数据
 * @param {number} [status] - HTTP 状态码，默认 200
 * @returns {Response} HTTP 响应对象
 */
function sendJSON(data, status) {
  if (status === undefined) status = 200;
  // 将数据序列化为 JSON 字符串
  var body = JSON.stringify(data);
  // 构建响应对象
  return new Response(body, {
    status: status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',           // 允许所有域
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',  // 允许的 HTTP 方法
      'Access-Control-Allow-Headers': '*',           // 允许所有请求头
    },
  });
}

/**
 * 发送 SSE（Server-Sent Events）流式响应
 * 
 * SSE 是一种服务器向客户端推送实时数据的协议。
 * 格式: "data: {json}\n\n"
 * 
 * @param {ReadableStream} stream - 可读流对象
 * @returns {Response} HTTP 流式响应对象
 */
function sendSSE(stream) {
  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',  // SSE 内容类型
      'Cache-Control': 'no-cache',                          // 禁用缓存
      'Connection': 'keep-alive',                           // 保持连接
      'X-Accel-Buffering': 'no',                            // 禁用 nginx 缓冲
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': '*',
    },
  });
}

// ============================================================================
// 🎯 模型解析
// ============================================================================

/**
 * 解析模型名称，获取对应的配置参数
 * 
 * 支持 @think= 参数来覆盖默认的思考模式。
 * 例如: "gemini-3.6-flash@think=0" 表示使用 Flash 模型但启用深度思考。
 * 
 * @param {string} modelName - 模型名称，如 "gemini-3.6-flash" 或 "gemini-3.6-flash@think=0"
 * @returns {Object} { modelName, modelId, thinkMode, error }
 */
function resolveModel(modelName) {
  var thinkOverride = null;
  var actualModelName = modelName;

  // 检查是否包含 @think= 参数
  if (modelName.indexOf('@think=') !== -1) {
    var parts = modelName.split('@think=');
    actualModelName = parts[0];           // 提取真正的模型名称
    thinkOverride = parseInt(parts[1], 10);  // 提取思考模式覆盖值
    if (isNaN(thinkOverride)) {
      return { error: '无效的 think 参数: ' + parts[1] };
    }
  }

  // 查找模型配置
  var cfg = MODELS[actualModelName];
  if (!cfg) {
    return { error: '未知模型: ' + actualModelName };
  }

  // 返回解析结果
  return {
    modelName: actualModelName,
    modelId: cfg.mode,                                        // 模型类别 ID
    thinkMode: thinkOverride !== null ? thinkOverride : cfg.think,  // 使用覆盖值或默认值
    error: null,
  };
}

// ============================================================================
// 📋 核心请求处理
// ============================================================================

/**
 * 处理 /v1/chat/completions 请求
 * 
 * 这是 OpenAI 兼容 API 的核心端点，处理聊天补全请求。
 * 
 * 支持两种模式:
 * 1. 非流式（stream=false）: 等待完整响应后一次性返回 JSON
 * 2. 流式（stream=true）: 实时转发 Gemini 的增量数据，实现打字机效果
 * 
 * 也支持工具调用（Function Calling）: 当提供 tools 参数时，自动切换到非流式模式。
 * 
 * SSE 格式严格符合 OpenAI 标准:
 * - 首块: { delta: { role: 'assistant' } }（只含 role，不含 content）
 * - 内容块: { delta: { content: '增量文本' } }（实时增量输出）
 * - 结束块: { delta: { content: "" }, finish_reason: 'stop' }
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Response>} HTTP 响应对象
 */
async function handleChatCompletions(request, body, config) {
  // ---- 解析模型 ----
  var resolved = resolveModel(body.model || config.defaultModel);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }

  var modelName = resolved.modelName;
  var modelId = resolved.modelId;
  var thinkMode = resolved.thinkMode;
  var tools = body.tools || null;

  // ---- 转换消息为提示文本 ----
  var prompt = messagesToPrompt(body.messages || [], tools);
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty prompt' } }, 400);
  }

  var stream = body.stream === true;
  var chatId = 'chatcmpl-' + generateShortId(12);

  log('Chat: model=' + modelName + ', stream=' + stream + ', tokens≈' + estimateTokens(prompt), 'INFO', config);

  // ================================================================
  // 非流式或带工具调用处理
  // ================================================================
  if (!stream || tools) {
    try {
      // 调用 Gemini API 获取原始响应
      var raw = await geminiStreamGenerate(prompt, modelId, thinkMode, config);

      // 提取响应文本
      var text = extractResponseText(raw);
      var toolCalls = null;

      // 如果启用了工具，解析工具调用
      if (tools && text) {
        var parsed = parseToolCalls(text);
        text = parsed.cleanText;
        toolCalls = parsed.toolCalls.length > 0 ? parsed.toolCalls : null;
      }

      // 构建响应消息
      var msg = { role: 'assistant', content: text || null };
      if (toolCalls) {
        msg.tool_calls = toolCalls;
      }

      var finishReason = toolCalls ? 'tool_calls' : 'stop';

      // 如果要求流式但有工具调用，发送单块的 SSE
      if (stream) {
        var encoder = new TextEncoder();
        var nonStreamSSE = new ReadableStream({
          start: function (controller) {
            var chunk = {
              id: chatId,
              object: 'chat.completion.chunk',
              created: timestamp(),
              model: modelName,
              choices: [{ index: 0, delta: msg, finish_reason: finishReason }],
            };
            controller.enqueue(encoder.encode('data: ' + JSON.stringify(chunk) + '\n\n'));
            controller.enqueue(encoder.encode('data: [DONE]\n\n'));
            controller.close();
          },
        });
        return sendSSE(nonStreamSSE);
      }

      // 非流式 JSON 响应
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
      log('Upstream error: ' + error.message, 'ERROR', config);
      return sendJSON({ error: { message: 'upstream error: ' + error.message } }, 502);
    }
  }

  // ================================================================
  // 🔑 流式打字机响应（实时转发 Gemini 增量数据）
  // ================================================================
  var streamEncoder = new TextEncoder();

  var streamBody = new ReadableStream({
    start: function (controller) {
      // ---- 状态管理 ----
      var heartbeatTimer = null;  // 心跳定时器
      var isFinished = false;      // 流是否已结束

      /**
       * 清理心跳定时器
       */
      var clearHeartbeat = function () {
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
      var finishStream = function (reason) {
        // 防止重复结束
        if (isFinished) return;
        clearHeartbeat();
        isFinished = true;
        try {
          // 发送符合 OpenAI 标准的结束块
          // delta.content 必须为 "" 而非空对象 {}
          controller.enqueue(streamEncoder.encode('data: ' + JSON.stringify({
            id: chatId,
            object: 'chat.completion.chunk',
            created: timestamp(),
            model: modelName,
            choices: [{
              index: 0,
              delta: { content: "" },
              finish_reason: reason || 'stop'
            }],
          }) + '\n\n'));
          // 发送 [DONE] 标记
          controller.enqueue(streamEncoder.encode('data: [DONE]\n\n'));
          controller.close();
        } catch (e) {
          log('Failed to finish stream: ' + e.message, 'ERROR', config);
        }
      };

      // 使用异步立即执行函数（IIFE）处理流式逻辑
      (async function () {
        try {
          // ---- 1. 发送 role 声明块 ----
          // 符合 OpenAI 标准：首块只包含 role，不含 content
          controller.enqueue(streamEncoder.encode('data: ' + JSON.stringify({
            id: chatId,
            object: 'chat.completion.chunk',
            created: timestamp(),
            model: modelName,
            choices: [{
              index: 0,
              delta: { role: 'assistant' },
              finish_reason: null
            }],
          }) + '\n\n'));

          // ---- 2. 启动心跳定时器 ----
          // 每 2 秒发送一次心跳注释，防止连接超时
          // SSE 注释格式：以冒号开头，客户端会忽略
          heartbeatTimer = setInterval(function () {
            if (!isFinished) {
              try {
                controller.enqueue(streamEncoder.encode(': heartbeat\n\n'));
              } catch (e) {
                clearHeartbeat();  // 写入失败，停止心跳
              }
            } else {
              clearHeartbeat();
            }
          }, 2000);

          // ---- 3. 构建并发送 Gemini 请求 ----
          var reqBody = buildPayload(prompt, modelId, thinkMode, config);
          var headers = await buildHeaders(config);
          var url = buildUrl(config);

          // 创建独立的 AbortController 用于超时控制
          var fetchController = new AbortController();
          var fetchTimeout = setTimeout(function () {
            fetchController.abort();
          }, (config.requestTimeoutSec - 2) * 1000);

          try {
            // 发送请求到 Gemini
            var response = await fetch(url, {
              method: 'POST',
              headers: headers,
              body: reqBody,
              signal: fetchController.signal,
            });
            clearTimeout(fetchTimeout);

            // 检查响应状态
            if (!response.ok) {
              var errorText = '';
              try {
                errorText = await response.text();
              } catch (e) {
                errorText = '无法读取错误信息';
              }
              throw new Error('HTTP ' + response.status + ': ' + errorText.substring(0, 200));
            }

            // ---- 4. 读取流式响应并实时转发增量数据 ----
            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';      // 行缓冲
            var prevText = '';    // 记录之前的完整文本，用于计算增量

            while (true) {
              var readResult = await reader.read();
              if (readResult.done) break;  // 流结束

              // 解码新数据并追加到缓冲区
              buffer += decoder.decode(readResult.value, { stream: true });

              // 检查 Gemini 错误信息
              if (buffer.indexOf('BardErrorInfo') !== -1) {
                var match = buffer.match(/BardErrorInfo\s*\[(\d+)\]/);
                if (match) {
                  throw new Error('Gemini upstream rejected request: BardErrorInfo [' + match[1] + ']');
                }
              }

              // 按行分割处理
              var lines = buffer.split('\n');
              buffer = lines.pop() || '';  // 保留不完整的最后一行

              // 遍历每一行
              for (var li = 0; li < lines.length; li++) {
                var line = lines[li];
                // 跳过不包含数据标记的行
                if (line.indexOf('"wrb.fr"') === -1 || line.length < 200) continue;

                try {
                  // 解析 Gemini 的嵌套 JSON 响应
                  var arr = JSON.parse(line);
                  var innerStr = arr[0][2];
                  if (!innerStr || innerStr.length < 50) continue;

                  var inner2 = JSON.parse(innerStr);

                  // 提取文本内容
                  if (Array.isArray(inner2) && inner2.length > 4 && inner2[4]) {
                    var parts = inner2[4];
                    for (var pi = 0; pi < parts.length; pi++) {
                      var part = parts[pi];
                      if (Array.isArray(part) && part.length > 1 && part[1] && Array.isArray(part[1])) {
                        var textItems = part[1];
                        for (var ti = 0; ti < textItems.length; ti++) {
                          var t = textItems[ti];
                          // 检查是否有新内容（文本长度增加了）
                          if (typeof t === 'string' && t.length > prevText.length) {
                            // 🔑 计算增量文本（新内容 = 当前全量 - 之前全量）
                            var delta = t.slice(prevText.length);
                            // 清理代码执行痕迹
                            var cleaned = cleanGeminiText(delta, false);
                            if (cleaned) {
                              // 立即发送增量块，实现打字机效果
                              controller.enqueue(streamEncoder.encode('data: ' + JSON.stringify({
                                id: chatId,
                                object: 'chat.completion.chunk',
                                created: timestamp(),
                                model: modelName,
                                choices: [{
                                  index: 0,
                                  delta: { content: cleaned },
                                  finish_reason: null
                                }],
                              }) + '\n\n'));
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
            // 确保清除超时定时器
            clearTimeout(fetchTimeout);
          }

          // ---- 5. 正常结束流 ----
          finishStream('stop');

        } catch (error) {
          // 错误处理：记录错误并尝试通知客户端
          log('Stream error: ' + error.message, 'ERROR', config);
          try {
            if (!isFinished) {
              controller.enqueue(streamEncoder.encode('data: ' + JSON.stringify({
                error: { message: error.message, type: 'upstream_error' }
              }) + '\n\n'));
            }
          } catch (e) {
            // 发送错误失败，忽略
          }
          finishStream('error');
        }
      })();  // 立即执行异步函数
    },

    /**
     * 客户端断开连接时的回调
     * 清理资源
     */
    cancel: function () {
      log('Client disconnected from stream', 'INFO', config);
    },
  });

  return sendSSE(streamBody);
}

/**
 * 处理 /v1/responses (OpenAI Responses API)
 * 
 * 用于 Codex CLI 等工具的兼容。
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Response>} HTTP 响应对象
 */
async function handleResponses(request, body, config) {
  var resolved = resolveModel(body.model || config.defaultModel);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }

  var modelName = resolved.modelName;
  var modelId = resolved.modelId;
  var thinkMode = resolved.thinkMode;
  var messages = [];

  // 添加系统指令
  if (body.instructions) {
    messages.push({ role: 'system', content: body.instructions });
  }

  // 处理输入项
  var inputs = body.input || [];
  if (typeof inputs === 'string') {
    inputs = [inputs];
  }
  for (var i = 0; i < inputs.length; i++) {
    var item = inputs[i];
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
      var content = item.content;
      if (Array.isArray(content)) {
        var textParts = [];
        for (var j = 0; j < content.length; j++) {
          var c = content[j];
          if (c.type === 'output_text') textParts.push(c.text || '');
        }
        content = textParts.join(' ');
      }
      messages.push({ role: item.role || 'user', content: content });
    }
  }

  // 标准化工具定义
  var tools = body.tools;
  if (tools) {
    var normalizedTools = [];
    for (var ti = 0; ti < tools.length; ti++) {
      var t = tools[ti];
      if (t.type === 'function' && !t.function) {
        normalizedTools.push({
          type: 'function',
          function: { name: t.name, description: t.description || '', parameters: t.parameters || {} },
        });
      } else {
        normalizedTools.push(t);
      }
    }
    tools = normalizedTools;
  }

  var prompt = messagesToPrompt(messages, tools);
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty input' } }, 400);
  }

  try {
    var raw = await geminiStreamGenerate(prompt, modelId, thinkMode, config);
    var text = extractResponseText(raw);
    var toolCalls = null;

    if (tools && text) {
      var parsed = parseToolCalls(text);
      text = parsed.cleanText;
      toolCalls = parsed.toolCalls.length > 0 ? parsed.toolCalls : null;
    }

    var responseId = 'resp_' + generateShortId(16);
    var messageId = 'msg_' + generateShortId(12);
    var output = [];

    if (toolCalls) {
      for (var tci = 0; tci < toolCalls.length; tci++) {
        var tc = toolCalls[tci];
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
      output: output,
      usage: {
        input_tokens: estimateTokens(prompt),
        output_tokens: estimateTokens(text),
        total_tokens: estimateTokens(prompt + text),
      },
    });
  } catch (error) {
    return sendJSON({ error: { message: 'upstream error: ' + error.message } }, 502);
  }
}

/**
 * 处理 Google 原生 API（Gemini CLI 兼容）
 * 
 * 支持 Google Gemini CLI 的原生格式。
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体
 * @param {boolean} stream - 是否使用流式传输
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Response>} HTTP 响应对象
 */
async function handleGoogleAPI(request, body, stream, config) {
  var requestUrl = new URL(request.url);
  var match = requestUrl.pathname.match(/\/v1beta\/models\/([^:]+)/);
  var modelName = match ? match[1] : null;

  if (!modelName) {
    return sendJSON({ error: { message: 'model not specified in path' } }, 400);
  }

  var resolved = resolveModel(modelName);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }

  var modelId = resolved.modelId;
  var thinkMode = resolved.thinkMode;
  var prompt = googleContentsToPrompt(body);

  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty content' } }, 400);
  }

  try {
    var raw = await geminiStreamGenerate(prompt, modelId, thinkMode, config);
    var text = extractResponseText(raw);

    var response = {
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
      return new Response('data: ' + JSON.stringify(response) + '\n\n', {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    return sendJSON(response);
  } catch (error) {
    return sendJSON({ error: { message: 'upstream error: ' + error.message } }, 502);
  }
}

// ============================================================================
// 🚀 主入口
// ============================================================================

export default {
  /**
   * Cloudflare Workers 的 fetch 事件处理器
   * 
   * 这是整个 Worker 的入口函数，所有 HTTP 请求都会经过这里。
   * 每个请求在独立的 Isolate 中运行（冷启动），
   * 或者复用已有 Isolate（热启动）。
   * 
   * 处理流程:
   * 1. OPTIONS 预检 -> 返回 CORS 头
   * 2. 创建请求级配置 -> getRequestConfig(env)
   * 3. 速率限制检查 -> checkRateLimit()
   * 4. API 密钥验证 -> checkApiKey()
   * 5. 路由分发:
   *    GET  /health     -> 健康检查
   *    GET  /v1/models  -> 模型列表
   *    POST /v1/chat/completions -> 聊天补全
   *    POST /v1/responses        -> Responses API
   *    POST ...:generateContent  -> Google 原生 API
   *    POST /v1/*                -> 万能兜底
   * 
   * @param {Request} request - HTTP 请求对象
   * @param {Object} env - 环境变量（每个请求独立）
   * @param {Object} ctx - 执行上下文
   * @returns {Promise<Response>} HTTP 响应对象
   */
  async fetch(request, env, ctx) {
    // ================================================================
    // 1. OPTIONS CORS 预检请求优先处理
    // ================================================================
    // 浏览器在发送跨域 POST 请求前会先发送 OPTIONS 预检请求
    // 必须返回正确的 CORS 头，否则浏览器会阻止实际请求
    // 必须在所有其他逻辑之前处理
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,  // No Content
        headers: {
          'Access-Control-Allow-Origin': '*',              // 允许所有域
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',  // 允许的方法
          'Access-Control-Allow-Headers': '*',             // 允许所有请求头
          'Access-Control-Max-Age': '86400',               // 预检结果缓存 24 小时
        },
      });
    }

    // ================================================================
    // 2. 🔑 为当前请求创建独立的配置副本（解决并发串扰的核心）
    // ================================================================
    // 不修改任何全局变量，每个请求都有自己专属的 config 对象
    // env 参数是 Cloudflare 为每个请求独立提供的环境变量
    var config = getRequestConfig(env);

    // 解析请求 URL
    var requestUrl = new URL(request.url);
    var path = requestUrl.pathname;
    var method = request.method;

    // ================================================================
    // 3. 速率限制检查
    // ================================================================
    var clientIP = request.headers.get('CF-Connecting-IP') || '0.0.0.0';
    if (!checkRateLimit(clientIP, config)) {
      log('Rate limit: ' + clientIP, 'WARN', config);
      return sendJSON({
        error: {
          message: '请求过于频繁，请稍后再试',
          type: 'rate_limit_exceeded',
        },
      }, 429);  // 429 Too Many Requests
    }

    // ================================================================
    // 4. API 密钥验证（仅对 /v1 路径生效）
    // ================================================================
    if (path.indexOf('/v1') === 0 && !checkApiKey(request, config)) {
      return sendJSON({
        error: { message: 'invalid api key' },
      }, 401);  // 401 Unauthorized
    }

    // ================================================================
    // 5. GET 请求处理
    // ================================================================
    if (method === 'GET') {
      // ---- 健康检查端点 ----
      if (path === '/' || path === '/health') {
        return sendJSON({
          status: 'ok',
          version: '1.3.0-cf-threadsafe',
          platform: 'Cloudflare Workers',
          models: Object.keys(MODELS),
          defaultModel: config.defaultModel,
          hasCookie: !!config.cookieString,
          hasSapisid: !!config.sapisid,
          rateLimit: config.rateLimit,
        });
      }

      // ---- OpenAI 格式模型列表 ----
      if (path === '/v1/models') {
        var modelList = [];
        var modelKeys = Object.keys(MODELS);
        for (var i = 0; i < modelKeys.length; i++) {
          var id = modelKeys[i];
          var cfg = MODELS[id];
          modelList.push({
            id: id,
            object: 'model',
            created: 1700000000,
            owned_by: 'google',
            description: cfg.desc,
          });
        }
        return sendJSON({ object: 'list', data: modelList });
      }

      // ---- Google 原生格式模型列表 ----
      if (path === '/v1beta/models') {
        var googleModels = [];
        var gKeys = Object.keys(MODELS);
        for (var j = 0; j < gKeys.length; j++) {
          var name = gKeys[j];
          var gCfg = MODELS[name];
          googleModels.push({
            name: 'models/' + name,
            displayName: name,
            description: gCfg.desc,
            supportedGenerationMethods: ['generateContent', 'streamGenerateContent'],
          });
        }
        return sendJSON({ models: googleModels });
      }

      // 未匹配的 GET 请求
      return sendJSON({ error: { message: 'not found' } }, 404);
    }

    // ================================================================
    // 6. POST 请求处理
    // ================================================================
    if (method === 'POST') {
      var body;
      try {
        body = await request.json();
      } catch (e) {
        return sendJSON({ error: { message: 'invalid JSON' } }, 400);
      }

      // ---- OpenAI 聊天补全 ----
      if (path === '/v1/chat/completions') {
        return handleChatCompletions(request, body, config);
      }

      // ---- OpenAI Responses API (Codex CLI) ----
      if (path === '/v1/responses') {
        return handleResponses(request, body, config);
      }

      // ---- Google 原生 generateContent ----
      if (path.indexOf(':generateContent') !== -1 && path.indexOf('stream') === -1) {
        return handleGoogleAPI(request, body, false, config);
      }

      // ---- Google 原生 streamGenerateContent ----
      if (path.indexOf(':streamGenerateContent') !== -1) {
        return handleGoogleAPI(request, body, true, config);
      }

      // ---- 万能兜底：所有 /v1/ 下的 POST 都转为 chat 处理 ----
      // 兼容 NextChat 等客户端可能发送的不同路径
      if (path.indexOf('/v1/') === 0) {
        return handleChatCompletions(request, body, config);
      }

      return sendJSON({ error: { message: 'not found' } }, 404);
    }

    // ================================================================
    // 7. 未支持的 HTTP 方法
    // ================================================================
    return sendJSON({ error: { message: 'method not allowed' } }, 405);
  },
};
