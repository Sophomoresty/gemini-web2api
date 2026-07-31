/**
 * Gemini Web2API - Cloudflare Workers 完整并发安全修复版
 * 多指纹轮换 + 多Cookie轮换 + 打字机效果 + 随机延迟
 * 
 * ============================================================================
 * 项目说明
 * ============================================================================
 * 本程序将 Google Gemini 的 Web 界面转换为 OpenAI 兼容的 API 接口。
 * 部署于 Cloudflare Workers 边缘计算平台，无需服务器即可运行。
 * 支持流式输出（SSE 打字机效果）、非流式输出、工具调用（Function Calling）。
 * 
 * ============================================================================
 * 核心功能列表:
 * ============================================================================
 * 
 * 1. 【并发安全】彻底消除了全局 CONFIG 被异步请求并发篡改/串扰的严重隐患。
 *    根本原因：CF Workers 的 Isolate 在热启动（复用）时，全局作用域代码不会重新执行。
 *    当 WorkBuddy 等客户端在极短时间内发送多个并发请求时，
 *    它们会共享同一个全局 CONFIG 对象（因为复用同一个 Isolate）。
 *    请求 A 修改了 CONFIG.cookieString = "cookie_a"，
 *    请求 B 紧接着修改了 CONFIG.cookieString = "cookie_b"，
 *    请求 A 后续使用的却是 cookie_b，导致认证信息串扰。
 *    这在 WorkBuddy 的多模型并发调用场景下尤为严重。
 *    
 *    解决方案：
 *    每次请求通过 getRequestConfig(env) 创建全新的独立配置副本，
 *    所有函数通过参数接收配置对象，完全不依赖全局可变状态。
 * 
 * 2. 【请求级配置隔离】实现了基于每次请求独立创建配置副本的机制。
 *    - DEFAULT_CONFIG 作为只读模板，永远不会被修改
 *    - getRequestConfig(env) 为每个请求创建独立的配置副本
 *    - 从 env（环境变量，每个请求由 CF 平台独立注入）加载定制配置
 *    - 所有函数签名都包含 config 参数，完全消除全局状态依赖
 *    - 使用显式赋值（env.X || null）防止 Isolate 复用时的值残留
 * 
 * 3. 【速率限制内存安全】修复全局 rateLimitStore 在 Serverless 环境下的隐式内存泄露问题。
 *    - Serverless 环境下 Isolate 可能长时间存活（热启动复用）
 *    - 如果不清理过期记录，Map 会无限增长导致内存泄漏
 *    - 使用随机概率清理机制（5% 概率触发全局清理）
 *    - 每次清理遍历所有键，删除过期或空的记录
 *    - 确保长期运行后内存使用保持稳定
 * 
 * 4. 【SAPISID 自动提取】增加了从 COOKIE_STRING 自动提取 SAPISID 的防御性逻辑。
 *    - 用户通常从浏览器复制完整 Cookie 字符串
 *    - Cookie 格式: "__Secure-1PSID=xxx; SAPISID=yyy; ..."
 *    - 如果用户设置了 COOKIE_STRING 但忘记单独设置 SAPISID
 *    - 程序会自动从 Cookie 字符串中正则提取 SAPISID 值
 *    - 正则表达式: /SAPISID=([^;]+)/
 *    - 提升用户体验，减少配置错误
 * 
 * 5. 【多指纹轮换】新增浏览器指纹轮换机制，降低被 Gemini 识别的概率。
 *    - User-Agent 轮换池（8 种真实浏览器 UA，涵盖 Windows/macOS/Linux）
 *    - Accept-Language 轮换池（6 种语言偏好设置）
 *    - Sec-Ch-Ua 轮换池（3 种 Chrome 版本标识）
 *    - Sec-Ch-Ua-Platform 轮换池（3 种操作系统平台）
 *    - 加权随机选择，模拟真实浏览器市场份额分布
 *    - Chrome ~72%（含 Windows/macOS/Linux）、Firefox ~8%、Safari ~8%
 * 
 * 6. 【多 Cookie 轮换】支持配置多个 Google 账号的 Cookie，随机选择使用。
 *    - 环境变量使用 | 分隔多个 Cookie: "cookie1| cookie2| cookie3"
 *    - 环境变量使用 | 分隔多个 SAPISID: "sapisid1| sapisid2| sapisid3"
 *    - 每次请求随机选择一个 Cookie 和对应的 SAPISID
 *    - 如果 SAPISID 数量与 Cookie 数量匹配，使用对应索引的 SAPISID
 *    - 大幅降低单个 Google 账号被限流（429）的概率
 * 
 * 7. 【随机延迟】请求前添加随机微小延迟，模拟人类操作间隔。
 *    - 延迟时间在 0 到 fingerprintJitterMs 之间随机（默认 1500ms）
 *    - 重试时也会添加新的随机延迟
 *    - 可配置：设置环境变量 FINGERPRINT_JITTER_MS=0 可禁用
 *    - 配合指纹轮换使用效果更佳
 * 
 * 8. 【SSE 打字机效果】OPTIONS 预检优先处理、实时增量输出、心跳保活。
 *    SSE 格式严格符合 OpenAI 标准：
 *    - 首块: delta: { role: 'assistant' }（只含 role，不含 content）
 *    - 内容块: delta: { content: '增量文本' }（实时计算并推送增量）
 *    - 结束块: delta: { content: "" }, finish_reason: 'stop'
 *    - 心跳保活：每 2 秒发送 ": heartbeat\n\n" SSE 注释
 * 
 * 9. 【完整功能保留】工具调用（Function Calling）、速率限制、API认证、
 *    Google原生API（Gemini CLI兼容）、Responses API（Codex CLI兼容）。
 * 
 * ============================================================================
 * 部署说明:
 * ============================================================================
 * 1. 登录 Cloudflare Dashboard -> Workers & Pages
 * 2. 创建 Worker -> 粘贴此代码 -> 保存并部署
 * 3. 配置环境变量(可选):
 * 
 *    【认证相关】
 *    - COOKIE_STRING: Cookie 字符串，多个用 | 分隔
 *      格式: "cookie_account1| cookie_account2| cookie_account3"
 *      从浏览器 F12 -> Application -> Cookies 中复制完整 Cookie
 *      包含 __Secure-1PSID、__Secure-3PSID、SAPISID 等
 *    - SAPISID: SAPISID 值，多个用 | 分隔
 *      格式: "sapisid_1| sapisid_2| sapisid_3"
 *      如果未设置，会自动从 COOKIE_STRING 中提取
 * 
 *    【API 安全】
 *    - API_KEYS: API 密钥 JSON 数组，如 ["sk-gemini", "sk-my-key"]
 *      留空或设为 [] 表示不验证密钥
 * 
 *    【Gemini 配置】
 *    - GEMINI_BL: Gemini 构建标签
 *      遇到 405 错误时需要更新此值
 *      获取方法：浏览器打开 gemini.google.com -> F12 -> Network -> 搜索 "boq_assistant"
 *    - DEFAULT_MODEL: 默认模型名称，如 "gemini-3.6-flash"
 *    - AUTH_USER: 多账户索引，0=第一个账户，1=第二个账户
 * 
 *    【性能调优】
 *    - RETRY_ATTEMPTS: 重试次数，默认 3
 *    - RETRY_DELAY_SEC: 重试间隔(秒)，默认 2
 *    - REQUEST_TIMEOUT_SEC: 请求超时(秒)，默认 28
 *    - FINGERPRINT_JITTER_MS: 请求前随机延迟最大值(毫秒)，默认 1500
 *      设为 0 可禁用随机延迟
 *    - RATE_LIMIT_MAX: 速率限制最大请求数，默认 3000
 *    - RATE_LIMIT_WINDOW: 速率限制时间窗口(秒)，默认 60
 * 
 * 客户端配置:
 *   基础URL: https://你的worker.workers.dev/v1
 *   API密钥: sk-gemini (或你在配置中设置的密钥)
 *   模型: gemini-3.6-flash
 * 
 * ============================================================================
 * 技术架构说明:
 * ============================================================================
 * 
 * 【Isolate 模型】
 * Cloudflare Workers 使用 Isolate（隔离环境）处理每个请求：
 * - 冷启动：全局代码重新执行，所有变量重新初始化
 * - 热启动：复用已有 Isolate，全局代码不执行，变量保留上次状态
 * - env 参数：每个请求由 CF 平台独立注入，始终包含最新环境变量
 * 
 * 【配置隔离原理】
 * 1. DEFAULT_CONFIG 作为不可变模板（只读）
 * 2. getRequestConfig(env) 每次创建全新副本
 * 3. 从 env 读取配置，用 || null 显式覆盖所有字段
 * 4. 所有函数通过 config 参数接收配置
 * 5. 不存在任何全局可变状态的依赖
 * 
 * 【为什么需要显式覆盖？】
 * 如果使用 if (env.X) CONFIG.X = env.X 的模式：
 * - 当 env.X 存在时，CONFIG.X 被更新 ✓
 * - 当 env.X 不存在时，if 不执行，CONFIG.X 保留上次值 ✗
 * 使用 CONFIG.X = env.X || null 确保始终显式赋值。
 * 
 * 基于原项目 gemini-web2api v1.1.0 移植
 * 原作者项目: https://github.com/your-repo/gemini-web2api
 */

// ============================================================================
// 🔒 默认配置 - 仅作为只读模板
// ============================================================================
// 这是所有请求配置的"蓝图"（Blueprint），用于生成每个请求的独立配置副本。
// 这个对象永远不会被修改，所有修改都在请求级的 config 副本中进行。
// 使用 Object.freeze() 确保不可变性，防止意外修改导致全局影响。

var DEFAULT_CONFIG = {
  // ---- 重试配置 ----
  // 当请求失败时，自动重试的次数
  // 每次重试使用指数退避策略：延迟时间 = retryDelaySec * 2^attempt
  // 例如：第一次重试延迟 2 秒，第二次 4 秒，第三次 8 秒
  retryAttempts: 3,
  // 重试间隔的基础时间（秒）
  // 实际延迟 = retryDelaySec * 2^attempt（指数退避）
  retryDelaySec: 2,

  // ---- 请求超时 ----
  // 单次 HTTP 请求的超时时间（秒）
  // 注意：CF Workers 免费版有 30 秒 CPU 时间限制
  // 流式请求的 CPU 时间在数据到达时重置，所以不受此严格限制
  // 但初始连接和第一个数据块必须在超时内到达
  requestTimeoutSec: 28,

  // ---- Gemini 构建标签 ----
  // Gemini 前端的版本标识，用于 API 请求的 URL 参数
  // 如果遇到 405 Method Not Allowed 错误，说明此值已过期
  // 更新方法：
  //   1. 浏览器打开 https://gemini.google.com/app
  //   2. 按 F12 打开开发者工具
  //   3. 切换到 Network（网络）标签
  //   4. 在任意请求的 URL 中搜索 "boq_assistant"
  //   5. 复制最新版本号，如 "boq_assistant-bard-web-server_20260730.02_p0"
  geminiBl: 'boq_assistant-bard-web-server_20260716.08_p0',

  // ---- 多账户支持 ----
  // Google 支持在同一个浏览器中登录多个账户
  // null 或 "" 表示使用默认账户（第一个登录的账户）
  // "0" 表示第一个账户，"1" 表示第二个账户，以此类推
  // 使用非默认账户时，Gemini URL 会包含 /u/1 等前缀
  authUser: null,

  // ---- XSRF 令牌 ----
  // 跨站请求伪造保护令牌
  // Gemini Web 前端会使用此令牌，但 API 调用通常不需要
  // 如果遇到 403 错误，可以尝试从浏览器中提取此值
  xsrfToken: null,

  // ---- 默认模型 ----
  // 当客户端请求未指定 model 参数时使用的默认模型
  // 可选值参考 MODELS 字典的键名
  defaultModel: 'gemini-3.6-flash',

  // ---- API 密钥白名单 ----
  // 用于验证客户端请求的密钥列表
  // 空数组 [] 表示不验证，所有请求都可以访问（不推荐用于生产）
  // 设置后，客户端必须在请求头中提供有效的密钥
  // 支持 Bearer Token、x-api-key、x-goog-api-key、URL 参数 ?key=
  // 示例: ["sk-gemini", "sk-my-custom-key"]
  apiKeys: ['sk-gemini'],

  // ---- Cookie 认证 ----
  // Gemini 对匿名请求有严格的速率限制（容易触发 429 Too Many Requests）
  // 提供有效的 Cookie 可以大幅提升稳定性和降低限流概率
  // cookieString: 从浏览器复制的完整 Cookie 字符串
  //   格式: "__Secure-1PSID=xxx; __Secure-3PSID=xxx; SAPISID=xxx; ..."
  //   支持多个 Cookie（用 | 分隔），每次请求随机选择一个
  //   示例: "cookie1| cookie2| cookie3"
  cookieString: null,
  // sapisid: 从 Cookie 中提取的 SAPISID 值
  //   用于生成 Google API 所需的 SAPISIDHASH 认证头
  //   格式: "abc123/def456"
  //   如果设置了 cookieString 但未设置 sapisid，程序会自动提取
  //   支持多个（用 | 分隔），与 Cookie 对应
  //   示例: "sapisid1| sapisid2| sapisid3"
  sapisid: null,

  // ---- 日志开关 ----
  // 是否在控制台输出请求日志
  // 生产环境建议保持开启，便于排查问题
  // 日志格式: [HH:MM:SS] [LEVEL] message
  logRequests: true,

  // ---- 速率限制 ----
  // Cloudflare Workers 级别的请求频率控制
  // 用于防止滥用和保护上游 Gemini API
  rateLimit: {
    // 是否启用速率限制
    enabled: true,
    // 时间窗口内的最大请求数
    // 默认 3000，设置为较高值以避免正常使用被限制
    // 如果遇到滥用，可以调低此值（如 30-100）
    maxRequests: 3000,
    // 时间窗口大小（秒）
    // 60 表示每分钟最多允许 maxRequests 个请求
    windowSec: 60,
  },

  // ---- 指纹轮换配置 ----
  // 请求前随机延迟的最大值（毫秒）
  // 模拟人类操作间隔，降低被检测为自动化请求的概率
  // 默认 1500ms（1.5秒），设为 0 可禁用
  // 配合 User-Agent 轮换使用效果更佳
  fingerprintJitterMs: 1500,
};

// ============================================================================
// 🎭 多指纹轮换池
// ============================================================================
// 以下指纹池用于每次请求时随机选择不同的浏览器标识。
// 目的是让每次请求看起来来自不同的浏览器和设备，
// 降低被 Gemini 服务器识别为自动化脚本的概率。

/**
 * User-Agent 轮换池
 * 
 * 包含 8 种真实浏览器的 User-Agent 字符串。
 * 涵盖 Windows、macOS、Linux 三个操作系统平台。
 * 涵盖 Chrome 125-127、Firefox 128、Safari 17.4 等主流浏览器版本。
 * 
 * 每个 UA 都有对应的权重（UA_WEIGHTS），用于加权随机选择。
 * 权重模拟真实浏览器市场份额分布：
 *   - Chrome Windows: ~65%（两个版本合计）
 *   - Safari macOS: ~8%
 *   - Chrome macOS: ~10%
 *   - Chrome Linux: ~7%
 *   - Firefox 全平台: ~10%（三个版本合计）
 * 
 * 权重数组与 USER_AGENTS 数组一一对应，总和为 100。
 */

var USER_AGENTS = [
  // Chrome 127 on Windows 10/11（占比最高，约 35%）
  // 这是目前最主流的浏览器配置
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
  // Chrome 126 on Windows 10/11（占比约 30%）
  // 上一版本的 Chrome，仍有大量用户未更新
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  // Safari 17.4 on macOS 14.5（占比约 8%）
  // Mac 用户使用系统自带浏览器
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
  // Chrome 127 on macOS 14.5（占比约 10%）
  // Mac 用户安装 Chrome 浏览器
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
  // Chrome 127 on Linux（占比约 7%）
  // Linux 桌面用户（开发者群体）
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
  // Firefox 128 on Windows（占比约 5%）
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0',
  // Firefox 128 on macOS（占比约 3%）
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:128.0) Gecko/20100101 Firefox/128.0',
  // Firefox 128 on Linux（占比约 2%）
  'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
];

// 加权权重数组，与 USER_AGENTS 一一对应
// 总和 = 35 + 30 + 8 + 10 + 7 + 5 + 3 + 2 = 100
// 模拟真实浏览器市场份额：Chrome ~72%, Safari ~8%, Firefox ~10%
var UA_WEIGHTS = [35, 30, 8, 10, 7, 5, 3, 2];

/**
 * 加权随机选择 User-Agent
 * 
 * 算法步骤：
 * 1. 计算所有权重的总和（totalWeight = 100）
 * 2. 生成 0 到 totalWeight 之间的随机浮点数
 * 3. 从头开始累加权重，当累加值超过随机数时
 * 4. 返回当前索引对应的 User-Agent
 * 
 * 这种算法保证了高权重的 UA 有更高的被选中概率。
 * 
 * @returns {string} 随机选择的 User-Agent 字符串
 */
function getRandomUserAgent() {
  // 第一步：计算总权重
  var totalWeight = 0;
  for (var i = 0; i < UA_WEIGHTS.length; i++) {
    totalWeight += UA_WEIGHTS[i];
  }
  // 第二步：生成 0 到总权重的随机数
  var random = Math.random() * totalWeight;
  // 第三步：累加权重，找到随机数落在哪个区间
  var cumulative = 0;
  for (var j = 0; j < USER_AGENTS.length; j++) {
    cumulative += UA_WEIGHTS[j];
    // 当累加值超过随机数时，返回当前 UA
    if (random < cumulative) {
      return USER_AGENTS[j];
    }
  }
  // 兜底：如果因为浮点精度问题没有命中，返回第一个
  return USER_AGENTS[0];
}

/**
 * Accept-Language 轮换池
 * 
 * 包含 6 种不同的浏览器语言偏好设置。
 * 模拟不同地区用户的浏览器配置。
 * 英语为主（美国/英国），部分包含中文、日语、韩语、西班牙语作为第二语言。
 * 
 * q 值表示优先级权重：
 *   - q=0.9 表示第二语言的高优先级
 *   - 第一语言不写 q 值（默认为 1.0）
 */
var ACCEPT_LANGUAGES = [
  'en-US,en;q=0.9',              // 纯英语用户（美国），最常见的配置
  'en-US,en;q=0.9,zh-CN;q=0.8',  // 英语为主，中文为辅（华裔或中国留学生）
  'en-GB,en;q=0.9',              // 英式英语用户（英国/英联邦国家）
  'en-US,en;q=0.9,ja;q=0.8',     // 英语为主，日语为辅（日裔或日语学习者）
  'en-US,en;q=0.9,ko;q=0.8',     // 英语为主，韩语为辅（韩裔或韩语学习者）
  'en-US,en;q=0.9,es;q=0.8',     // 英语为主，西班牙语为辅（拉丁裔）
];

/**
 * 随机选择 Accept-Language
 * 使用均匀随机分布（每种语言偏好被选中的概率相同）
 * @returns {string} 随机选择的 Accept-Language 字符串
 */
function getRandomAcceptLanguage() {
  var idx = Math.floor(Math.random() * ACCEPT_LANGUAGES.length);
  return ACCEPT_LANGUAGES[idx];
}

/**
 * Sec-Ch-Ua 轮换池（Chrome 用户代理客户端提示）
 * 
 * Sec-Ch-Ua 是 Chrome 浏览器（Chromium 内核）发送的额外请求头，
 * 包含浏览器品牌和版本信息。只有 Chrome 系浏览器会发送此头。
 * Firefox 和 Safari 不发送此头。
 * 
 * 格式: "Brand";v="MajorVersion"
 * - "Not)A;Brand";v="99" 是 Chromium 的固定标识
 * - "Google Chrome";v="127" 表示 Chrome 主版本号
 * - "Chromium";v="127" 表示 Chromium 内核版本号
 */
var SEC_CH_UA_POOLS = [
  // Chrome 127
  '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
  // Chrome 126
  '"Not)A;Brand";v="99", "Google Chrome";v="126", "Chromium";v="126"',
  // Chrome 125
  '"Not)A;Brand";v="99", "Google Chrome";v="125", "Chromium";v="125"',
];

/**
 * Sec-Ch-Ua-Platform 轮换池（操作系统平台标识）
 * 
 * 配合 Sec-Ch-Ua 使用，标识浏览器的操作系统平台。
 * 应该与 User-Agent 中的平台信息一致。
 */
var SEC_CH_UA_PLATFORMS = [
  '"Windows"',   // Windows 平台
  '"macOS"',     // macOS 平台（注意大小写）
  '"Linux"',     // Linux 平台
];

/**
 * 随机选择 Sec-Ch-Ua（Chrome 版本标识）
 * @returns {string} 随机选择的 Sec-Ch-Ua 字符串
 */
function getRandomSecChUa() {
  var idx = Math.floor(Math.random() * SEC_CH_UA_POOLS.length);
  return SEC_CH_UA_POOLS[idx];
}

/**
 * 随机选择 Sec-Ch-Ua-Platform（操作系统平台标识）
 * @returns {string} 随机选择的平台字符串
 */
function getRandomSecChUaPlatform() {
  var idx = Math.floor(Math.random() * SEC_CH_UA_PLATFORMS.length);
  return SEC_CH_UA_PLATFORMS[idx];
}

// ============================================================================
// 🤖 模型定义
// ============================================================================
// 映射自 Gemini Web 前端 JS 源码中的 MODE_CATEGORY 枚举
// 
// mode 字段含义（MODE_CATEGORY 枚举值）：
//   1 = FAST（快速模式）- Gemini Flash 系列，速度最快
//   2 = THINKING（深度思考）- 启用深度推理，输出质量更高
//   3 = PRO（专业版）- 最强模型，需要有效 Cookie 才能正确路由
//   4 = AUTO（自动选择）- 由 Gemini 自动选择最合适的模型
//   5 = FAST_DYNAMIC_THINKING（动态思考）- 自适应思考深度
//   6 = FLASH_LITE（轻量快速）- 最轻量模型，速度最快但质量较低
// 
// think 字段含义（思考模式）：
//   0 = 启用深度思考（模型会花更多时间推理）
//   4 = AUTO（自动选择思考深度，由 Gemini 决定）

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
// 🔑 核心：请求级配置生成器（解决并发串扰 + 多Cookie轮换 + 指纹轮换）
// ============================================================================

/**
 * 为当前请求创建独立的配置副本
 * 
 * 【为什么需要这个函数？—— 并发串扰问题】
 * Cloudflare Workers 在处理请求时使用 Isolate（隔离环境）。
 * 冷启动时全局代码会重新执行，变量回到初始值。
 * 但热启动（Isolate 复用）时，全局代码不会重新执行，
 * 全局变量保留上一次请求修改后的值。
 * 
 * 当 WorkBuddy 等客户端在极短时间内发送 5-20 个并发请求时，
 * 这些请求可能被分配到同一个 Isolate，共享全局变量。
 * 
 * 举例说明串扰过程：
 *   请求 A 到达 → CONFIG.cookieString = "cookie_a"
 *   请求 B 到达 → CONFIG.cookieString = "cookie_b"  ← 覆盖了 A 的设置！
 *   请求 A 继续执行 → 使用的是 "cookie_b" ← 串扰！
 * 
 * 【如何解决？—— 请求级配置隔离】
 * 1. 每次请求调用此函数，从 DEFAULT_CONFIG 模板创建全新的配置对象
 * 2. 从 env（环境变量，每个请求由 CF 平台独立注入）读取定制配置
 * 3. 所有后续函数通过 config 参数接收配置，完全不依赖全局状态
 * 4. 使用显式赋值（env.X || null）防止 Isolate 复用时的值残留
 * 
 * 【多 Cookie 轮换】
 * 如果环境变量中使用 | 分隔多个 Cookie 和 SAPISID，
 * 每次请求会随机选择一个组合使用。
 * 这可以大幅降低单个 Google 账号被限流的概率。
 * 
 * 【环境变量格式】
 * COOKIE_STRING = "cookie_account1| cookie_account2| cookie_account3"
 * SAPISID = "sapisid_1| sapisid_2| sapisid_3"
 * 
 * @param {Object} env - Cloudflare Worker 环境变量（每个请求独立）
 * @returns {Object} 专属于当前请求的配置副本
 */
function getRequestConfig(env) {
  // 从默认模板创建全新的配置对象
  // 逐字段手动拷贝，确保每个字段都是独立的基本类型副本
  // 不使用展开运算符 (...DEFAULT_CONFIG)，避免引用共享问题
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
    fingerprintJitterMs: DEFAULT_CONFIG.fingerprintJitterMs,

    // ---- 嵌套对象：rateLimit 需要深拷贝 ----
    // 因为 rateLimit 是一个对象，不能直接赋值（会引用共享）
    // 需要创建一个新对象，逐字段拷贝
    rateLimit: {
      enabled: DEFAULT_CONFIG.rateLimit.enabled,
      maxRequests: DEFAULT_CONFIG.rateLimit.maxRequests,
      windowSec: DEFAULT_CONFIG.rateLimit.windowSec,
    },
  };

  // ================================================================
  // 环境变量覆盖
  // env 是 Cloudflare 为每个请求独立提供的环境变量对象
  // 这些值是在 CF Dashboard 中配置的，修改后自动生效
  // ================================================================

  // ---- 字符串类型：有值才覆盖（保留默认值作为兜底） ----
  if (env.GEMINI_BL) {
    config.geminiBl = env.GEMINI_BL;
  }
  if (env.DEFAULT_MODEL) {
    config.defaultModel = env.DEFAULT_MODEL;
  }

  // ---- 认证相关字段：使用 || 操作符确保显式覆盖 ----
  // 这些字段可能为 null 或空字符串
  // 使用 || null 确保即使 env 值为 undefined 或空字符串，
  // 也会显式设置为 null，防止 Isolate 复用时上次请求的值残留
  config.authUser = env.AUTH_USER || null;
  config.xsrfToken = env.XSRF_TOKEN || null;

  // ================================================================
  // 🎭 多 Cookie 轮换支持
  // ================================================================
  // 将环境变量中的字符串按 | 分割成数组
  // 过滤掉空字符串（处理连续 | 或首尾 | 的情况）
  // 
  // 环境变量格式示例:
  //   COOKIE_STRING = "cookie_account1| cookie_account2| cookie_account3"
  //   SAPISID = "sapisid_1| sapisid_2| sapisid_3"
  // 
  // 如果分割后只有 1 个元素，效果等同于单个 Cookie

  var cookieStrings = (env.COOKIE_STRING || '').split('|').filter(function (c) {
    return c.trim();  // 过滤掉空字符串
  });
  var sapisids = (env.SAPISID || '').split('|').filter(function (s) {
    return s.trim();  // 过滤掉空字符串
  });

  // 情况 1：有多个 Cookie 可供选择
  if (cookieStrings.length > 0) {
    // 随机选择一个 Cookie 索引
    var cookieIdx = Math.floor(Math.random() * cookieStrings.length);
    config.cookieString = cookieStrings[cookieIdx].trim();

    // 如果 SAPISID 数量与 Cookie 数量匹配，使用对应索引的 SAPISID
    // 这样可以保持 Cookie 和 SAPISID 的对应关系
    if (sapisids.length === cookieStrings.length) {
      config.sapisid = sapisids[cookieIdx].trim();
    } else if (sapisids.length > 0) {
      // 如果数量不匹配，随机选择一个 SAPISID
      var sapisidIdx = Math.floor(Math.random() * sapisids.length);
      config.sapisid = sapisids[sapisidIdx].trim();
    }
    // 如果只有一个 SAPISID 或没有 SAPISID，留给后面的自动提取逻辑处理
  }
  // 情况 2：只有 SAPISID，没有 Cookie
  else if (sapisids.length > 0) {
    // 随机选择一个 SAPISID
    var sapisidIdx = Math.floor(Math.random() * sapisids.length);
    config.sapisid = sapisids[sapisidIdx].trim();
  }
  // 情况 3：既没有 Cookie 也没有 SAPISID
  // config.cookieString 和 config.sapisid 保持默认值 null

  // ================================================================
  // 🛡️ 智能兼容：自动从 COOKIE_STRING 提取 SAPISID
  // ================================================================
  // 如果最终 SAPISID 为空但 Cookie 不为空，
  // 尝试从 Cookie 字符串中正则匹配提取 SAPISID 值
  // 
  // Cookie 格式示例:
  //   "__Secure-1PSID=AJDrVf...; __Secure-3PSID=AJDrVf...; SAPISID=abc123/def456; ..."
  // 
  // 正则 /SAPISID=([^;]+)/ 的含义：
  //   SAPISID=   匹配字面量 "SAPISID="
  //   ([^;]+)    捕获组：匹配一个或多个非分号字符（即 SAPISID 的值）
  if (!config.sapisid && config.cookieString) {
    var match = config.cookieString.match(/SAPISID=([^;]+)/);
    if (match) {
      // match[1] 是第一个捕获组，即 SAPISID 的值
      // trim() 去除可能的首尾空白字符
      config.sapisid = match[1].trim();
    }
  }

  // ---- API 密钥：JSON 数组格式，需要特殊解析 ----
  // env.API_KEYS 是字符串类型，如 '["sk-gemini", "sk-my-key"]'
  // 需要用 JSON.parse 解析为真正的数组
  if (env.API_KEYS) {
    try {
      config.apiKeys = JSON.parse(env.API_KEYS);
    } catch (e) {
      // JSON 解析失败时保留默认值
      // 输出错误日志但不中断程序运行
      console.error('[ERROR] API_KEYS 解析失败: ' + e.message + '，使用默认值');
    }
  }

  // ---- 数字类型字段：需要 parseInt 转换 ----
  // env 中的环境变量都是字符串类型
  // 需要用 parseInt(value, 10) 转换为十进制整数
  // 使用 isNaN() 检查转换结果，防止无效值
  if (env.RETRY_ATTEMPTS) {
    var ra = parseInt(env.RETRY_ATTEMPTS, 10);
    if (!isNaN(ra)) config.retryAttempts = ra;
  }
  if (env.RETRY_DELAY_SEC) {
    var rd = parseInt(env.RETRY_DELAY_SEC, 10);
    if (!isNaN(rd)) config.retryDelaySec = rd;
  }
  if (env.REQUEST_TIMEOUT_SEC) {
    var rt = parseInt(env.REQUEST_TIMEOUT_SEC, 10);
    if (!isNaN(rt)) config.requestTimeoutSec = rt;
  }
  // 指纹轮换随机延迟配置
  if (env.FINGERPRINT_JITTER_MS) {
    var fj = parseInt(env.FINGERPRINT_JITTER_MS, 10);
    if (!isNaN(fj)) config.fingerprintJitterMs = fj;
  }

  // ---- 速率限制配置 ----
  if (env.RATE_LIMIT_MAX) {
    var rlmax = parseInt(env.RATE_LIMIT_MAX, 10);
    if (!isNaN(rlmax)) config.rateLimit.maxRequests = rlmax;
  }
  if (env.RATE_LIMIT_WINDOW) {
    var rlwin = parseInt(env.RATE_LIMIT_WINDOW, 10);
    if (!isNaN(rlwin)) config.rateLimit.windowSec = rlwin;
  }

  // 返回请求专属的配置副本
  // 这个对象在请求结束后随 Isolate 回收
  return config;
}

// ============================================================================
// 🛠 工具函数
// ============================================================================

/**
 * 日志记录函数
 * 
 * 使用请求级配置中的 logRequests 开关控制是否输出日志。
 * 如果没有传入 config 参数（比如在 getRequestConfig 中调用），
 * 使用 DEFAULT_CONFIG 的 logRequests 设置。
 * 
 * 日志格式: [HH:MM:SS] [LEVEL] message
 * 例如: [14:30:25] [INFO] Chat: model=gemini-3.6-flash, stream=true
 * 
 * @param {string} msg - 要记录的日志消息
 * @param {string} [level] - 日志级别，默认 'INFO'。可选值: INFO / WARN / ERROR
 * @param {Object} [config] - 请求级配置对象（可选，用于并发安全）
 */
function log(msg, level, config) {
  // 如果未指定日志级别，默认使用 INFO
  level = level || 'INFO';
  // 根据 config 参数决定是否输出日志
  // 有 config 时使用 config.logRequests，没有时使用默认配置
  var shouldLog = config ? config.logRequests : DEFAULT_CONFIG.logRequests;
  if (shouldLog) {
    // 生成时间戳，格式: HH:MM:SS
    // toISOString() 返回 "2026-07-30T14:30:25.123Z"
    // split('T')[1] 取 "14:30:25.123Z"
    // split('.')[0] 取 "14:30:25"
    var ts = new Date().toISOString().split('T')[1].split('.')[0];
    console.log('[' + ts + '] [' + level + '] ' + msg);
  }
}

/**
 * 生成 UUID v4（通用唯一标识符）
 * 
 * UUID v4 格式: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
 * 其中 4 固定为版本号，y 的高位固定为 10xx（表示变体）
 * 
 * Cloudflare Workers 环境优先使用内置的 crypto.randomUUID() 方法。
 * 如果不可用（老版本或其他环境），使用 Math.random() 回退方案。
 * 回退方案的随机性较弱，不适合安全敏感场景。
 * 
 * @returns {string} UUID v4 格式的字符串，如 "550e8400-e29b-41d4-a716-446655440000"
 */
function generateUUID() {
  // 优先使用 CF Workers 内置方法（性能更好，随机性更强）
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // 回退方案：手动生成符合 UUID v4 规范的字符串
  // 使用 Math.random() 生成伪随机数
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    // 生成 0-15 的随机整数
    var r = Math.random() * 16 | 0;
    // x 位置直接使用随机值
    // y 位置确保高位为 10xx（符合 UUID v4 规范：10xx = 8,9,a,b）
    var v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

/**
 * 生成短 ID
 * 
 * 从 UUID 中提取前 length 个十六进制字符（去掉连字符）。
 * 用于生成聊天补全 ID、工具调用 ID、请求 ID 等不需要完整 UUID 的场景。
 * 
 * @param {number} [length] - 需要的 ID 长度，默认 12 字符
 * @returns {string} 短 ID 字符串，如 "a1b2c3d4e5f6"
 */
function generateShortId(length) {
  var len = length || 12;
  // 去掉 UUID 中的连字符，取前 len 个字符
  return generateUUID().replace(/-/g, '').substring(0, len);
}

/**
 * 获取当前 Unix 时间戳（秒）
 * 
 * Unix 时间戳是从 1970-01-01 00:00:00 UTC 开始的秒数。
 * 广泛用于 API 响应中的 created 字段。
 * 
 * @returns {number} Unix 时间戳（秒），如 1753872000
 */
function timestamp() {
  return Math.floor(Date.now() / 1000);
}

/**
 * 估算文本的 Token 数量
 * 
 * 使用简单的启发式算法进行粗略估算：
 * - 英文约 4 字符 = 1 token
 * - 这个估算不够精确，但足以用于基本的资源预估和日志显示
 * - 不是精确计算，仅供 reference
 * 
 * @param {string} text - 要估算的文本
 * @returns {number} 估算的 token 数量，至少为 1
 */
function estimateTokens(text) {
  if (!text) return 0;
  // 至少返回 1，避免除零错误
  return Math.max(1, Math.ceil(text.length / 4));
}

/**
 * 生成 SAPISID 认证哈希
 * 
 * Google API 使用基于时间的 SHA-1 哈希进行认证。
 * 这个哈希证明请求来自持有有效 Google 会话的用户。
 * 
 * 算法步骤:
 * 1. 获取当前 Unix 时间戳（秒）
 * 2. 构造输入字符串: "{timestamp} {sapisid} https://gemini.google.com"
 * 3. 使用 SHA-1 算法对输入进行哈希
 * 4. 将哈希结果转换为十六进制字符串
 * 5. 返回格式化字符串: "SAPISIDHASH {timestamp}_{hex_hash}"
 * 
 * 格式示例: SAPISIDHASH 1753872000_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0
 * 
 * @param {string} sapisid - 从 Google Cookie 中提取的 SAPISID 值
 * @returns {Promise<string>} 认证哈希字符串
 */
async function makeSapisidHash(sapisid) {
  // 获取当前时间戳
  var ts = timestamp();
  // 构造哈希输入（与 Google Web 前端完全一致的格式）
  var input = ts + ' ' + sapisid + ' https://gemini.google.com';

  // 将输入字符串编码为 UTF-8 字节数组
  var encoder = new TextEncoder();
  var data = encoder.encode(input);

  // 使用 Web Crypto API 进行 SHA-1 哈希
  var hashBuffer = await crypto.subtle.digest('SHA-1', data);

  // 将哈希结果（ArrayBuffer）转换为十六进制字符串
  var hashArray = Array.from(new Uint8Array(hashBuffer));
  var hashHex = hashArray.map(function (b) {
    // 每个字节转换为两位十六进制数
    return b.toString(16).padStart(2, '0');
  }).join('');

  // 返回格式化的认证字符串
  return 'SAPISIDHASH ' + ts + '_' + hashHex;
}

/**
 * 获取多账户 URL 前缀
 * 
 * Google 支持在同一个浏览器中登录多个 Google 账号。
 * 当使用非默认账户时，Gemini 的 URL 路径会包含账户索引：
 * - 默认账户: https://gemini.google.com/app
 * - 第二个账户: https://gemini.google.com/u/1/app
 * - 第三个账户: https://gemini.google.com/u/2/app
 * 
 * @param {Object} config - 请求级配置对象
 * @returns {string} URL 前缀，如 "/u/1"，默认账户返回空字符串 ""
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
// 这是整个程序能够正常工作的基础。

/**
 * 构建 Gemini API 请求负载
 * 
 * Gemini 内部使用 80 个元素的嵌套数组作为请求体。
 * 这个结构是通过逆向工程 Gemini Web 前端 JS 代码获得的。
 * 
 * 关键字段说明:
 *   inner[0]: 用户消息和元数据
 *     [prompt, 消息索引, 图片数据, 附件信息, 元数据, 上下文ID, 是否新对话]
 *   inner[1]: 语言设置 ["en"]
 *   inner[2]: 对话上下文（空表示新对话）
 *   inner[6]: 连续对话标志 [0]
 *   inner[7]: 流式输出标志 1
 *   inner[10]: 流式输出标志 1
 *   inner[11]: 安全过滤级别（0=基础, 1=严格, 2=最严格）
 *   inner[17]: 思考模式 [[thinkMode]]
 *     thinkMode=0: 启用深度思考
 *     thinkMode=4: 自动选择
 *   inner[18]: 扩展思考标志 0
 *   inner[30]: 输出格式 [4]
 *   inner[41]: 响应类型 [2]
 *   inner[59]: 唯一请求 ID（UUID v4）
 *   inner[61]: 附件列表 []
 *   inner[79]: 模型选择（MODE_CATEGORY 枚举值）⭐ 最关键的字段
 *     1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE
 * 
 * 其他索引位置的值为 null，表示使用默认设置。
 * 
 * 外层包装:
 *   outer = [null, json.dumps(inner)]
 *   然后作为 f.req 参数的值进行 URL 编码
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
  // [prompt, 消息索引, 图片, 附件, 元数据, 上下文ID, 新对话标志]
  inner[0] = [prompt, 0, null, null, null, null, 0];

  // --- 语言设置为英语 ---
  inner[1] = ['en'];

  // --- 对话上下文 ---
  // 全部为空表示新对话，不使用任何历史记录
  inner[2] = ['', '', '', null, null, null, null, null, null, ''];

  // --- 连续对话标志 ---
  inner[6] = [0];

  // --- 流式输出标志 ---
  inner[7] = 1;    // 启用流式
  inner[10] = 1;   // 流式输出

  // --- 安全过滤级别 ---
  // 0 = 基础过滤（推荐值，不会过度拦截正常内容）
  // 1 = 严格过滤（可能误拦）
  // 2 = 最严格过滤（非常保守）
  inner[11] = 0;

  // --- 思考模式配置 ---
  // 双层嵌套数组: [[thinkMode]]
  // 外层数组包含一个内层数组，内层数组包含 thinkMode 值
  inner[17] = [[thinkMode]];

  // --- 扩展思考标志 ---
  inner[18] = 0;

  // --- 各种内部参数 ---
  // 这些参数的具体含义未知，但保持与 Gemini Web 前端一致
  inner[27] = 1;   // 未知标志
  inner[30] = [4]; // 输出格式设置
  inner[41] = [2]; // 响应类型设置
  inner[53] = 0;   // 未知标志

  // --- 唯一请求 ID ---
  // 使用 UUID v4 确保每次请求都有全局唯一的标识
  inner[59] = generateUUID();

  // --- 附件列表 ---
  // 空数组表示没有附件
  inner[61] = [];

  // --- 其他设置 ---
  inner[68] = 1;   // 未知标志

  // ⭐ 模型选择（最关键字段）
  // MODE_CATEGORY 枚举值:
  //   1=FAST（快速）, 2=THINKING（深度思考）, 3=PRO（专业版）
  //   4=AUTO（自动）, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE
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
  // 通常不需要，但某些极端情况下 Gemini 可能要求
  if (config.xsrfToken) {
    params.append('at', config.xsrfToken);
  }

  // 返回 URL 编码的字符串
  // 例如: f.req=%5Bnull%2C%22%5B%5B...%5D%5D%22%5D
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
 * - bl (build label): Gemini 前端构建版本标识，用于 API 版本控制
 * - hl (host language): 界面语言，固定为 en（英语）
 * - _reqid: 请求 ID，使用时间戳的后 6 位数字
 * - rt: 请求类型，c 表示普通聊天请求
 * 
 * @param {Object} config - 请求级配置对象
 * @returns {string} 完整的请求 URL
 */
function buildUrl(config) {
  // 获取多账户 URL 前缀
  var prefix = getAccountPrefix(config);
  // 生成请求 ID（使用时间戳的后 6 位数字）
  // 例如 timestamp() = 1753872000 → reqid = 872000
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
 * 构建 Gemini API 请求头（包含多指纹轮换）
 * 
 * 🎭 多指纹轮换机制:
 * 每次调用此函数时，会随机选择不同的浏览器指纹组合：
 * - User-Agent: 从 8 种真实浏览器 UA 中加权随机选择
 * - Accept-Language: 从 6 种语言偏好中均匀随机选择
 * - Sec-Ch-Ua: 如果选中的是 Chrome UA，随机选择 Chrome 版本标识
 * - Sec-Ch-Ua-Platform: 随机选择操作系统平台
 * 
 * 这使每次请求看起来来自不同的浏览器和设备，
 * 降低被 Gemini 服务器识别为自动化脚本的概率。
 * 
 * 注意：Firefox 和 Safari 不会发送 Sec-Ch-Ua 系列头，
 * 所以只有当 UA 是 Chrome 时才添加这些头。
 * 
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Object>} HTTP 请求头对象
 */
async function buildHeaders(config) {
  // 获取多账户 URL 前缀
  var prefix = getAccountPrefix(config);

  // 🎭 第一步：随机选择浏览器指纹
  var selectedUA = getRandomUserAgent();           // 加权随机选择 User-Agent
  var selectedLanguage = getRandomAcceptLanguage(); // 均匀随机选择语言偏好

  // 第二步：构建基础请求头
  var headers = {
    // 标准表单提交格式（与浏览器表单提交一致）
    'Content-Type': 'application/x-www-form-urlencoded',
    // 声明请求来源域（必须是 gemini.google.com）
    'Origin': 'https://gemini.google.com',
    // 声明引用页面
    'Referer': 'https://gemini.google.com' + prefix + '/app',
    // 同域请求标志（让 Gemini 认为这是内部请求）
    'X-Same-Domain': '1',
    // 🔑 使用随机选择的 User-Agent
    'User-Agent': selectedUA,
    // 接受任意响应类型
    'Accept': '*/*',
    // 🔑 使用随机选择的 Accept-Language
    'Accept-Language': selectedLanguage,
    // 浏览器安全策略头（现代浏览器的标准行为）
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
  };

  // 🎭 第三步：如果是 Chrome UA，添加 Sec-Ch-Ua 系列头
  // 判断方法：检查 User-Agent 字符串中是否包含 "Chrome"
  // Firefox 的 UA 包含 "Gecko" 和 "Firefox"，不包含 "Chrome"
  // Safari 的 UA 包含 "Safari" 但不包含 "Chrome"
  if (selectedUA.indexOf('Chrome') !== -1) {
    headers['Sec-Ch-Ua'] = getRandomSecChUa();              // Chrome 版本标识
    headers['Sec-Ch-Ua-Mobile'] = '?0';                      // 桌面端（非移动端）
    headers['Sec-Ch-Ua-Platform'] = getRandomSecChUaPlatform(); // 操作系统平台
  }

  // 第四步：多账户支持
  // 如果使用了非默认账户（authUser 不为空），添加认证用户头
  if (prefix) {
    headers['X-Goog-AuthUser'] = String(config.authUser);
  }

  // 第五步：Cookie 认证（如果有）
  // 提供有效的 Cookie 可以大幅提升请求稳定性
  // 减少 429（限流）和 403（禁止访问）错误的概率
  if (config.cookieString) {
    headers['Cookie'] = config.cookieString;
  }

  // 第六步：SAPISID 认证哈希（如果有）
  // 生成基于时间的 SHA-1 哈希，证明请求来自有效的 Google 会话
  // 格式: SAPISIDHASH {timestamp}_{sha1_hex_hash}
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
 * 【重试策略】
 * 使用指数退避算法：
 * - 第一次重试: 等待 retryDelaySec * 2^0 = 2 秒
 * - 第二次重试: 等待 retryDelaySec * 2^1 = 4 秒
 * - 第三次重试: 等待 retryDelaySec * 2^2 = 8 秒
 * 
 * 【错误处理】
 * - 405: BL 版本过期，需要更新 geminiBl 配置
 * - 429: 请求频率超限，等待 Retry-After 秒后重试
 * - 403: 需要有效的 Cookie 认证
 * - 其他: 记录错误信息并重试
 * 
 * 🎭 【指纹轮换】
 * 每次重试时都会重新构建请求头，使用不同的浏览器指纹。
 * 这增加了重试成功的机会，因为不同的指纹可能通过不同的限流规则。
 * 
 * 🎭 【随机延迟】
 * 请求前会添加 0 到 fingerprintJitterMs 之间的随机延迟。
 * 模拟人类操作的自然间隔，降低被检测为脚本的概率。
 * 
 * @param {string} prompt - 用户输入的提示文本
 * @param {number} modelId - 模型类别 ID（MODE_CATEGORY 枚举值: 1-6）
 * @param {number} thinkMode - 思考模式设置（0=深度思考, 4=自动）
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<string>} API 原始响应文本（包含嵌套 JSON）
 * @throws {Error} 所有重试失败后抛出最后的错误
 */
async function geminiStreamGenerate(prompt, modelId, thinkMode, config) {
  // 🎭 请求前添加随机微小延迟（模拟人类操作间隔）
  // 延迟时间在 0 到 fingerprintJitterMs 毫秒之间随机均匀分布
  // 例如 fingerprintJitterMs=1500 时，延迟在 0 到 1.5 秒之间
  if (config.fingerprintJitterMs > 0) {
    var jitter = Math.random() * config.fingerprintJitterMs;
    await new Promise(function (resolve) { setTimeout(resolve, jitter); });
  }

  // 构建请求负载、请求头、请求 URL
  var body = buildPayload(prompt, modelId, thinkMode, config);
  var headers = await buildHeaders(config);
  var url = buildUrl(config);

  // 保存最后一次错误，所有重试失败后抛出
  var lastError;

  // 重试循环
  for (var attempt = 0; attempt < config.retryAttempts; attempt++) {
    try {
      // 🎭 重试时重新构建请求头（使用不同的浏览器指纹）
      // 这增加了重试成功的机会
      if (attempt > 0) {
        headers = await buildHeaders(config);
        // 重试时也添加新的随机延迟
        // 避免在完全相同的时间点重试
        if (config.fingerprintJitterMs > 0) {
          var retryJitter = Math.random() * config.fingerprintJitterMs;
          await new Promise(function (resolve) { setTimeout(resolve, retryJitter); });
        }
      }

      // 创建 AbortController 用于超时控制
      var controller = new AbortController();
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
      // 等待服务器指定的 Retry-After 时间后重试
      if (response.status === 429) {
        var retryAfter = parseInt(response.headers.get('Retry-After') || '5', 10);
        log('收到 429 限流，等待 ' + retryAfter + ' 秒后重试...', 'WARN', config);
        if (attempt < config.retryAttempts - 1) {
          await new Promise(function (resolve) { setTimeout(resolve, retryAfter * 1000); });
          continue;  // 跳过本次，进入下一次重试
        }
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
        await new Promise(function (resolve) { setTimeout(resolve, delay); });
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
 * ...代码内容...
 * ```
 * ```javascript?code_stdout&code_event_index=1
 * ...输出内容...
 * ```
 * 
 * 这些代码执行块对最终用户没有意义，应该被移除以获得干净的响应文本。
 * 
 * 正则表达式说明:
 * - ```(?:python|javascript|text): 匹配代码块开始的三个反引号和语言标识
 * - \?code_(?:reference|stdout): 匹配代码执行参数
 * - &code_event_index=\d+: 匹配事件索引
 * - \n[\s\S]*?```: 匹配代码块内容（非贪婪模式）到结束的三个反引号
 * - \n?: 匹配可能存在的尾随换行符
 * 
 * @param {string} text - 原始响应文本
 * @param {boolean} [strip] - 是否去除首尾空白字符，默认 true
 * @returns {string} 清理后的文本
 */
function cleanGeminiText(text, strip) {
  // 如果未指定 strip 参数，默认值为 true
  if (strip === undefined) strip = true;

  // 移除代码执行块
  // 使用全局替换（g 标志）和多行模式（s 标志，允许 . 匹配换行符）
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
 * Gemini API 返回的是多行嵌套 JSON 数据，每行格式如下:
 * [["wrb.fr", "[[...]]", ...], ...]
 * 
 * 解析逻辑:
 * 1. 检查是否有 BardErrorInfo 错误信息
 * 2. 按行分割原始响应文本
 * 3. 跳过不包含 "wrb.fr" 标记的行（非数据行）
 * 4. 跳过长度小于 200 的行（太短，不包含有效数据）
 * 5. 解析每行的 JSON 数据（双层嵌套结构）
 * 6. 从 inner[4] 中提取文本内容
 * 7. 返回最后一个非空文本（通常是最终的完整响应）
 * 
 * 数据结构说明:
 * 外层 JSON 数组:
 *   [0]: "wrb.fr"（数据标记）
 *   [1]: 预留
 *   [2]: 内层 JSON 字符串
 * 内层 JSON 数组:
 *   [4]: 对话内容数组
 *     [*][0]: 内容类型
 *     [*][1]: 文本数组
 * 
 * @param {string} raw - API 原始响应文本
 * @returns {string} 提取并清理后的最终文本
 * @throws {Error} 如果检测到 BardErrorInfo 错误
 */
function extractResponseText(raw) {
  // 第一步：检查 BardErrorInfo 错误
  // 格式: BardErrorInfo [错误代码]
  // 例如: BardErrorInfo [10] 表示请求被拒绝
  var bardErr = raw.match(/BardErrorInfo\s*\[(\d+)\]/);
  if (bardErr) {
    throw new Error('Gemini upstream rejected request: BardErrorInfo [' + bardErr[1] + ']');
  }

  // 第二步：收集所有提取到的文本片段
  var texts = [];

  // 第三步：按行分割原始响应
  var lines = raw.split('\n');
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];

    // 跳过不包含 "wrb.fr" 的行（不是数据行）
    // 跳过长度小于 200 的行（太短，不包含有效数据）
    if (line.indexOf('"wrb.fr"') === -1 || line.length < 200) continue;

    try {
      // 第四步：解析外层 JSON
      var arr = JSON.parse(line);
      // 提取内层 JSON 字符串（arr[0][2]）
      var innerStr = arr[0][2];

      // 跳过空的或太短的内层 JSON
      if (!innerStr || innerStr.length < 50) continue;

      // 第五步：解析内层 JSON
      var inner = JSON.parse(innerStr);

      // 第六步：检查 inner[4] 是否存在且包含内容
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
      // JSON 解析错误，可能是响应不完整
      // 继续处理下一行，不中断整个解析过程
    }
  }

  // 第七步：获取最后一个非空文本
  // Gemini 的响应是逐步累积的，最后一个文本通常包含完整内容
  var text = '';
  for (var m = texts.length - 1; m >= 0; m--) {
    if (texts[m].trim()) {
      text = texts[m];
      break;
    }
  }

  // 第八步：清理代码执行痕迹并返回
  return cleanGeminiText(text);
}

// ============================================================================
// 🔄 OpenAI 格式转换
// ============================================================================

/**
 * 将 OpenAI 消息列表转换为 Gemini 提示文本
 * 
 * 这是整个程序的"翻译层"，负责将 OpenAI 的 Chat Completions API 格式
 * 转换为 Gemini 可以理解的纯文本格式。
 * 
 * 转换规则:
 * ┌──────────────┬──────────────────────────────────────────┐
 * │ OpenAI Role  │ Gemini 格式                               │
 * ├──────────────┼──────────────────────────────────────────┤
 * │ system       │ [System instruction]: {content}           │
 * │ assistant    │ [Assistant]: {content}                    │
 * │ tool         │ [Tool result for {name}]: {content}       │
 * │ user         │ {content}（直接使用）                      │
 * │ 工具调用      │ ```tool_call\n{json}\n``` 代码块格式      │
 * └──────────────┴──────────────────────────────────────────┘
 * 
 * 多条消息之间使用双换行（\n\n）分隔。
 * 
 * @param {Array} messages - OpenAI 格式的消息列表
 *   每条消息格式: { role: string, content: string | array }
 * @param {Array} [tools] - 可用的工具/函数定义列表（可选）
 *   每个工具格式: { type: "function", function: { name, description, parameters } }
 * @returns {string} 转换后的提示文本
 */
function messagesToPrompt(messages, tools) {
  // 存储各个消息段的数组
  var parts = [];

  // ================================================================
  // 第一步：如果提供了工具定义，在开头添加工具使用说明
  // ================================================================
  if (tools && tools.length > 0) {
    // 标准化工具定义格式
    // 兼容两种格式:
    //   1. { type: "function", function: { name, description, parameters } }
    //   2. { name, description, parameters }（简写格式）
    var toolDefs = [];
    for (var ti = 0; ti < tools.length; ti++) {
      var tool = tools[ti];
      var fn = (tool.type === 'function') ? (tool.function || tool) : tool;
      toolDefs.push({
        name: fn.name || tool.name || '',
        description: fn.description || tool.description || '',
        parameters: fn.parameters || tool.parameters || {},
      });
    }

    // 构建工具使用说明文本
    // 包含:
    //   1. 工具调用格式说明
    //   2. 所有可用工具的 JSON 定义
    parts.push(
      '[System instruction]: You have access to tools. ' +
      'To call a tool, respond with:\n' +
      '```tool_call\n{"name": "func_name", "arguments": {...}}\n```\n' +
      'Only use tool_call blocks when needed.\n\n' +
      'Available tools:\n' + JSON.stringify(toolDefs, null, 2)
    );
  }

  // ================================================================
  // 第二步：逐条处理消息
  // ================================================================
  for (var mi = 0; mi < messages.length; mi++) {
    var msg = messages[mi];
    var role = msg.role || 'user';     // 角色，默认为 user
    var content = msg.content || '';    // 消息内容

    // 如果内容是数组（多模态消息），提取文本部分
    // 例如: [{ type: "text", text: "Hello" }, { type: "image_url", ... }]
    // 只提取 type 为 "text" 或 "input_text" 的部分
    if (Array.isArray(content)) {
      var textParts = [];
      for (var ci = 0; ci < content.length; ci++) {
        var c = content[ci];
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
      // 工具响应：添加结果前缀和工具名称
      parts.push('[Tool result for ' + (msg.name || 'unknown') + ']: ' + content);
    } else {
      // 用户消息：直接使用内容
      parts.push(content || '');
    }
  }

  // 第三步：用双换行连接所有部分，过滤掉空字符串
  return parts.filter(function (p) { return p; }).join('\n\n');
}

/**
 * 从响应文本中解析工具调用
 * 
 * 工具调用格式（在响应文本中）:
 * ```tool_call
 * {"name": "get_weather", "arguments": {"city": "Beijing"}}
 * ```
 * 
 * 解析后转换为 OpenAI 格式的工具调用对象:
 * {
 *   id: "call_xxxxxxxxxxxx",
 *   type: "function",
 *   function: {
 *     name: "get_weather",
 *     arguments: '{"city":"Beijing"}'
 *   }
 * }
 * 
 * @param {string} text - 可能包含工具调用的响应文本
 * @returns {Object} { cleanText: 清理后的纯文本, toolCalls: 工具调用数组 }
 */
function parseToolCalls(text) {
  var toolCalls = [];

  // 正则匹配 tool_call 代码块
  // /```tool_call\s*\n(.*?)\n```/gs
  // g: 全局匹配（查找所有匹配项，而非只找第一个）
  // s: dotAll 模式（允许 . 匹配换行符 \n）
  var pattern = /```tool_call\s*\n(.*?)\n```/gs;
  var match;

  // 循环提取所有工具调用
  while ((match = pattern.exec(text)) !== null) {
    try {
      // match[1] 是第一个捕获组，即 tool_call 代码块中的 JSON 内容
      var data = JSON.parse(match[1].trim());

      // 构建 OpenAI 格式的工具调用对象
      toolCalls.push({
        id: 'call_' + generateShortId(8),       // 生成唯一的调用 ID
        type: 'function',
        function: {
          name: data.name,                       // 函数名
          arguments: JSON.stringify(data.arguments || {}),  // 参数（必须是 JSON 字符串）
        },
      });
    } catch (e) {
      // JSON 解析失败，跳过格式有误的代码块
      // 不中断整个解析过程
    }
  }

  // 从文本中移除所有 tool_call 代码块
  var cleanText = text.replace(pattern, '').trim();

  return {
    cleanText: cleanText,    // 清理后的纯文本
    toolCalls: toolCalls     // 工具调用数组
  };
}

/**
 * Google 原生 API 格式转换为提示文本
 * 
 * 支持 Google Gemini CLI 的原生 API 格式（generateContent）。
 * 格式示例:
 * {
 *   "systemInstruction": {
 *     "parts": [{"text": "你是一个有用的助手"}]
 *   },
 *   "contents": [
 *     {"role": "user", "parts": [{"text": "你好"}]},
 *     {"role": "model", "parts": [{"text": "你好！有什么可以帮助你的？"}]}
 *   ]
 * }
 * 
 * 转换规则:
 * - systemInstruction.parts[].text → "[System instruction]: {text}"
 * - contents[].role="model" → "[Assistant]: {text}"
 * - contents[].role="user" → {text}（直接使用）
 * 
 * @param {Object} req - Google API 格式的请求对象
 * @returns {string} 转换后的提示文本
 */
function googleContentsToPrompt(req) {
  var parts = [];

  // 处理系统指令（systemInstruction）
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

  // 处理对话内容（contents）
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

    // model 角色 → Assistant 前缀
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
// Map 相对于普通 Object 的优势：
// 1. 支持任意类型的键（这里使用字符串）
// 2. 有内置的 size 属性
// 3. 迭代性能更好
var rateLimitStore = new Map();

/**
 * 检查请求是否超过速率限制（滑动窗口算法）
 * 
 * 算法步骤:
 * 1. 获取当前时间和该 IP 的历史请求记录
 * 2. 过滤出时间窗口内的有效请求
 * 3. 如果有效请求数达到或超过阈值 → 拒绝（返回 false）
 * 4. 否则记录本次请求并允许（返回 true）
 * 
 * 【内存管理】
 * 由于 Cloudflare Workers 的 Isolate 可能长时间存活（热启动复用），
 * rateLimitStore 中的记录如果不清理会无限增长，导致内存泄漏。
 * 
 * 清理策略:
 * - 每次检查时有 5% 的概率触发全局清理（Math.random() < 0.05）
 * - 遍历所有 IP 的记录，删除过期或空的条目
 * - 5% 的概率确保清理不会过于频繁影响性能
 * 
 * @param {string} clientIP - 客户端 IP 地址
 * @param {Object} config - 请求级配置对象
 * @returns {boolean} true 表示允许请求，false 表示被限流拒绝
 */
function checkRateLimit(clientIP, config) {
  // 如果速率限制未启用，直接放行
  if (!config.rateLimit || !config.rateLimit.enabled) return true;

  var now = Date.now();
  // 计算时间窗口的毫秒数（配置中是秒，需要转换为毫秒）
  var windowMs = config.rateLimit.windowSec * 1000;
  // 生成存储键（添加前缀避免与其他键冲突）
  var key = 'rl:' + clientIP;

  // 获取该 IP 的历史记录，并过滤出当前窗口内的有效请求
  var timestamps = (rateLimitStore.get(key) || []).filter(function (t) {
    return now - t < windowMs;
  });

  // 检查是否达到或超过阈值
  if (timestamps.length >= config.rateLimit.maxRequests) {
    return false;  // 拒绝请求
  }

  // 记录本次请求的时间戳
  timestamps.push(now);
  rateLimitStore.set(key, timestamps);

  // ================================================================
  // 🛡️ 随机概率清理过期键（5% 概率触发）
  // ================================================================
  // 防止长期高并发运行后，大量冷 IP 记录残留内存
  // 5% 的概率（约每 20 次检查触发一次）确保不会频繁执行
  if (Math.random() < 0.05) {
    // 使用 forEach 遍历 Map 中的所有条目
    rateLimitStore.forEach(function (v, k) {
      // 过滤出有效的（未过期的）记录
      var valid = v.filter(function (t) {
        return now - t < windowMs;
      });
      if (valid.length === 0) {
        // 该 IP 已无任何有效记录，删除整个条目
        rateLimitStore.delete(k);
      } else {
        // 更新为只包含有效记录的数组
        rateLimitStore.set(k, valid);
      }
    });
  }

  return true;  // 允许请求
}

// ============================================================================
// 🔐 API 密钥验证
// ============================================================================

/**
 * 验证 API 密钥（支持多种认证方式）
 * 
 * 认证方式按优先级排列:
 * 1. Authorization: Bearer <key>（标准 Bearer Token 认证，最推荐）
 * 2. x-api-key: <key>（自定义请求头，常用于 OpenAI SDK）
 * 3. x-goog-api-key: <key>（Google 风格的请求头）
 * 4. URL 查询参数 ?key=<key>（最不推荐，密钥暴露在 URL 中）
 * 
 * 如果 apiKeys 为空数组 []，表示不验证密钥，所有请求都允许。
 * 适用于内网使用或已有其他安全措施的场景。
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} config - 请求级配置对象
 * @returns {boolean} true 表示通过认证，false 表示认证失败
 */
function checkApiKey(request, config) {
  // 获取 API 密钥白名单
  var keys = config.apiKeys || [];

  // 如果没有配置任何密钥，允许所有请求（不验证模式）
  if (keys.length === 0) return true;

  // ================================================================
  // 方式 1: Authorization: Bearer <key>
  // ================================================================
  var auth = request.headers.get('Authorization') || '';
  // 检查是否以 "Bearer " 开头
  if (auth.indexOf('Bearer ') === 0) {
    // 提取 Bearer 后面的 token（去掉 "Bearer " 前缀，共 7 个字符）
    var token = auth.slice(7);
    // 使用 indexOf 检查 token 是否在白名单中
    if (keys.indexOf(token) !== -1) return true;
  }

  // ================================================================
  // 方式 2 & 3: x-api-key / x-goog-api-key
  // ================================================================
  var headerNames = ['x-api-key', 'x-goog-api-key'];
  for (var i = 0; i < headerNames.length; i++) {
    var value = request.headers.get(headerNames[i]) || '';
    if (keys.indexOf(value) !== -1) return true;
  }

  // ================================================================
  // 方式 4: URL 查询参数 ?key=<key>
  // ================================================================
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
 * 自动设置 CORS 跨域头，允许来自任何域的请求访问。
 * 
 * @param {Object} data - 要发送的响应数据（会被 JSON.stringify 序列化）
 * @param {number} [status] - HTTP 状态码，默认 200
 * @returns {Response} HTTP 响应对象
 */
function sendJSON(data, status) {
  if (status === undefined) status = 200;
  // 将数据序列化为 JSON 字符串
  var body = JSON.stringify(data);
  // 构建并返回 Response 对象
  return new Response(body, {
    status: status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',           // 允许所有域访问
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',  // 允许的 HTTP 方法
      'Access-Control-Allow-Headers': '*',           // 允许所有请求头
    },
  });
}

/**
 * 发送 SSE（Server-Sent Events）流式响应
 * 
 * SSE 是一种服务器向客户端推送实时数据的协议。
 * 相比 WebSocket，SSE 更简单：
 * - 单向通信（服务器 → 客户端）
 * - 基于 HTTP 协议
 * - 自动重连机制
 * 
 * 数据格式:
 * data: {json}\n\n
 * 
 * 特殊格式:
 * data: [DONE]\n\n  → 表示流结束
 * : heartbeat\n\n   → SSE 注释（客户端忽略），用于保持连接
 * 
 * @param {ReadableStream} stream - 可读流对象
 * @returns {Response} HTTP 流式响应对象
 */
function sendSSE(stream) {
  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',  // SSE 必需的内容类型
      'Cache-Control': 'no-cache',                          // 禁用缓存
      'Connection': 'keep-alive',                           // 保持连接不关闭
      'X-Accel-Buffering': 'no',                            // 禁用 nginx 代理缓冲
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
 * 例如: "gemini-3.6-flash@think=0" 
 * 表示使用 Flash 模型但启用深度思考（think=0）。
 * 
 * @param {string} modelName - 模型名称
 *   格式: "模型名" 或 "模型名@think=数字"
 * @returns {Object} 
 *   - modelName: 去掉 @think= 参数后的实际模型名称
 *   - modelId: MODE_CATEGORY 枚举值（1-6）
 *   - thinkMode: 思考模式（0=深度思考, 4=自动）
 *   - error: 错误信息，null 表示正常
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
    modelId: cfg.mode,                                            // 模型类别 ID
    thinkMode: thinkOverride !== null ? thinkOverride : cfg.think,  // 使用覆盖值或默认值
    error: null,
  };
}

// ============================================================================
// 📋 核心请求处理 - /v1/chat/completions
// ============================================================================

/**
 * 处理 /v1/chat/completions 请求
 * 
 * 这是 OpenAI 兼容 API 的核心端点，也是整个程序最关键的函数。
 * 负责将 OpenAI 格式的聊天请求转换为 Gemini 格式，并返回响应。
 * 
 * 【支持两种模式】
 * 1. 非流式（stream=false）: 
 *    - 等待 Gemini 返回完整响应
 *    - 一次性解析并返回 JSON 格式的响应
 *    - 适用于工具调用（需要完整响应来解析 tool_call 代码块）
 * 
 * 2. 流式（stream=true）:
 *    - 实时读取 Gemini 的流式数据
 *    - 计算增量文本（当前全量 - 之前全量）
 *    - 立即将增量推送给客户端（打字机效果）
 *    - 包含心跳保活机制（每 2 秒发送 SSE 注释）
 * 
 * 【SSE 格式严格遵循 OpenAI 标准】
 * 首块:    { delta: { role: 'assistant' }, finish_reason: null }
 * 内容块:  { delta: { content: '增量文本' }, finish_reason: null }
 * 结束块:  { delta: { content: "" }, finish_reason: 'stop' }
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体（OpenAI Chat Completions 格式）
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Response>} HTTP 响应对象
 */
async function handleChatCompletions(request, body, config) {
  // ---- 第一步：解析模型 ----
  var resolved = resolveModel(body.model || config.defaultModel);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }

  var modelName = resolved.modelName;
  var modelId = resolved.modelId;
  var thinkMode = resolved.thinkMode;
  var tools = body.tools || null;

  // ---- 第二步：转换消息为提示文本 ----
  var prompt = messagesToPrompt(body.messages || [], tools);
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty prompt' } }, 400);
  }

  var stream = body.stream === true;
  var chatId = 'chatcmpl-' + generateShortId(12);

  log('Chat: model=' + modelName + ', stream=' + stream + ', tokens≈' + estimateTokens(prompt), 'INFO', config);

  // ================================================================
  // 情况 A：非流式或带工具调用
  // ================================================================
  // 工具调用需要完整的响应文本才能解析 tool_call 代码块
  // 所以即使请求了 stream=true，如果有 tools 也强制使用非流式
  if (!stream || tools) {
    try {
      // 调用 Gemini API 获取完整响应
      var raw = await geminiStreamGenerate(prompt, modelId, thinkMode, config);

      // 提取并清理响应文本
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

      // 如果要求流式但有工具调用，以单块 SSE 的方式返回
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

      // 标准非流式 JSON 响应
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
  // 情况 B：真流式 SSE 响应（打字机效果）
  // ================================================================
  var streamEncoder = new TextEncoder();

  var streamBody = new ReadableStream({
    start: function (controller) {
      // ---- 状态管理变量 ----
      var heartbeatTimer = null;  // 心跳定时器 ID
      var isFinished = false;      // 流是否已经结束（防止重复关闭）

      /**
       * 清理心跳定时器
       * 在流结束或出错时调用，确保定时器被正确清除
       */
      var clearHeartbeat = function () {
        if (heartbeatTimer) {
          clearInterval(heartbeatTimer);
          heartbeatTimer = null;
        }
      };

      /**
       * 安全结束流
       * 确保发送结束块和 [DONE] 标记后才关闭流
       * 防止重复关闭导致错误
       * 
       * @param {string} reason - 结束原因，'stop' 表示正常结束，'error' 表示异常结束
       */
      var finishStream = function (reason) {
        // 防止重复结束（可能同时触发 error 和 close 事件）
        if (isFinished) return;
        clearHeartbeat();
        isFinished = true;
        try {
          // 发送符合 OpenAI 标准的结束块
          // ⚠️ 重要：delta.content 必须为 ""（空字符串），不能是空对象 {}
          // NextChat 等客户端会检查 delta.content 是否存在
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
          // 发送 [DONE] 标记（SSE 协议规定的流结束信号）
          controller.enqueue(streamEncoder.encode('data: [DONE]\n\n'));
          controller.close();
        } catch (e) {
          log('Failed to finish stream: ' + e.message, 'ERROR', config);
        }
      };

      // 使用异步立即执行函数（IIFE）处理流式逻辑
      // 因为 ReadableStream 的 start 不能是 async 函数
      (async function () {
        try {
          // ---- 第一步：发送 role 声明块 ----
          // 符合 OpenAI 标准：首块只包含 role，不包含 content
          // 这告诉客户端："接下来是 assistant 角色的消息"
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

          // ---- 第二步：启动心跳定时器 ----
          // 每 2 秒发送一次 SSE 注释（以冒号开头的行）
          // 客户端会忽略注释行，但连接保持活跃
          // 这防止了长时间无数据时连接被中间代理断开
          heartbeatTimer = setInterval(function () {
            if (!isFinished) {
              try {
                // SSE 注释格式：以冒号开头，客户端忽略
                controller.enqueue(streamEncoder.encode(': heartbeat\n\n'));
              } catch (e) {
                clearHeartbeat();  // 写入失败，停止心跳
              }
            } else {
              clearHeartbeat();
            }
          }, 2000);

          // ---- 第三步：构建并发送 Gemini 请求 ----
          var reqBody = buildPayload(prompt, modelId, thinkMode, config);
          var headers = await buildHeaders(config);
          var url = buildUrl(config);

          // 创建独立的 AbortController 用于超时控制
          var fetchController = new AbortController();
          var fetchTimeout = setTimeout(function () {
            fetchController.abort();  // 超时后中止 fetch 请求
          }, (config.requestTimeoutSec - 2) * 1000);

          try {
            // 发送 HTTP POST 请求到 Gemini
            var response = await fetch(url, {
              method: 'POST',
              headers: headers,
              body: reqBody,
              signal: fetchController.signal,
            });
            clearTimeout(fetchTimeout);

            // 检查响应状态码
            if (!response.ok) {
              var errorText = '';
              try {
                errorText = await response.text();
              } catch (e) {
                errorText = '无法读取错误信息';
              }
              throw new Error('HTTP ' + response.status + ': ' + errorText.substring(0, 200));
            }

            // ---- 第四步：读取流式响应并实时转发增量数据 ----
            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';      // 行缓冲区（处理不完整的行）
            var prevText = '';    // 记录之前已发送的完整文本

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

              // 按行分割处理（Gemini 的响应是每行一个 JSON）
              var lines = buffer.split('\n');
              // 最后一行可能不完整，保留在缓冲区中
              buffer = lines.pop() || '';

              // 遍历每一行完整的数据
              for (var li = 0; li < lines.length; li++) {
                var line = lines[li];
                // 跳过不包含数据标记的行或太短的行
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
                            // 🔑 计算增量文本
                            // 增量 = 当前完整文本 - 之前已发送的完整文本
                            var delta = t.slice(prevText.length);
                            // 清理代码执行痕迹（不 trim，保留空白格式）
                            var cleaned = cleanGeminiText(delta, false);
                            if (cleaned) {
                              // 立即将增量块推送给客户端（打字机效果）
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
                  // Gemini 的响应可能在传输中被截断
                }
              }
            }
          } finally {
            // 无论成功还是失败，确保清除超时定时器
            clearTimeout(fetchTimeout);
          }

          // ---- 第五步：正常结束流 ----
          finishStream('stop');

        } catch (error) {
          // 错误处理：记录日志并尝试通知客户端
          log('Stream error: ' + error.message, 'ERROR', config);
          try {
            if (!isFinished) {
              controller.enqueue(streamEncoder.encode('data: ' + JSON.stringify({
                error: { message: error.message, type: 'upstream_error' }
              }) + '\n\n'));
            }
          } catch (e) {
            // 发送错误信息失败，可能客户端已断开
          }
          finishStream('error');
        }
      })();  // 立即执行异步函数
    },

    /**
     * 客户端断开连接时的回调
     * 当用户关闭页面或网络中断时触发
     * 清理资源，停止心跳
     */
    cancel: function () {
      log('Client disconnected from stream', 'INFO', config);
    },
  });

  return sendSSE(streamBody);
}

/**
 * 处理 /v1/responses 请求（OpenAI Responses API）
 * 
 * 这是 OpenAI 新的 Responses API（用于 Codex CLI 等工具）。
 * 与 Chat Completions API 类似，但消息格式略有不同。
 * 
 * Responses API 格式:
 * {
 *   "model": "gpt-4o",
 *   "input": [
 *     {"role": "user", "content": "Hello"},
 *     {"type": "function_call_output", "call_id": "...", "output": "..."}
 *   ],
 *   "instructions": "系统指令（可选）",
 *   "tools": [...]
 * }
 * 
 * 本函数负责将 Responses API 格式转换为 Chat Completions 格式，
 * 然后复用 handleChatCompletions 的逻辑。
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Response>} HTTP 响应对象
 */
async function handleResponses(request, body, config) {
  // 解析模型
  var resolved = resolveModel(body.model || config.defaultModel);
  if (resolved.error) {
    return sendJSON({ error: { message: resolved.error } }, 400);
  }

  var modelName = resolved.modelName;
  var modelId = resolved.modelId;
  var thinkMode = resolved.thinkMode;
  var messages = [];

  // 添加系统指令（instructions 字段）
  if (body.instructions) {
    messages.push({ role: 'system', content: body.instructions });
  }

  // 处理输入项（input 字段）
  var inputs = body.input || [];
  // 兼容字符串格式的 input
  if (typeof inputs === 'string') {
    inputs = [inputs];
  }
  for (var i = 0; i < inputs.length; i++) {
    var item = inputs[i];
    if (typeof item === 'string') {
      // 简单字符串 → user 消息
      messages.push({ role: 'user', content: item });
    } else if (item.type === 'function_call_output') {
      // 函数调用输出 → tool 消息
      messages.push({
        role: 'tool',
        tool_call_id: item.call_id,
        name: item.name,
        content: item.output,
      });
    } else {
      // 其他格式的消息
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
        // 简写格式 → 完整格式
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

  // 转换消息为提示文本
  var prompt = messagesToPrompt(messages, tools);
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty input' } }, 400);
  }

  try {
    // 调用 Gemini API
    var raw = await geminiStreamGenerate(prompt, modelId, thinkMode, config);
    var text = extractResponseText(raw);
    var toolCalls = null;

    // 解析工具调用
    if (tools && text) {
      var parsed = parseToolCalls(text);
      text = parsed.cleanText;
      toolCalls = parsed.toolCalls.length > 0 ? parsed.toolCalls : null;
    }

    // 构建 Responses API 格式的输出
    var responseId = 'resp_' + generateShortId(16);
    var messageId = 'msg_' + generateShortId(12);
    var output = [];

    // 添加工具调用输出
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

    // 添加文本输出
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
 * 支持 Google Gemini CLI 的原生 generateContent 和 streamGenerateContent 格式。
 * URL 格式: /v1beta/models/{model}:generateContent
 * 
 * @param {Request} request - HTTP 请求对象
 * @param {Object} body - 解析后的请求体（Google 格式）
 * @param {boolean} stream - 是否使用流式传输
 * @param {Object} config - 请求级配置对象
 * @returns {Promise<Response>} HTTP 响应对象
 */
async function handleGoogleAPI(request, body, stream, config) {
  // 从 URL 路径中提取模型名称
  // 例如: /v1beta/models/gemini-3.6-flash:generateContent → "gemini-3.6-flash"
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

  // 转换 Google 格式为提示文本
  var prompt = googleContentsToPrompt(body);
  if (!prompt.trim()) {
    return sendJSON({ error: { message: 'empty content' } }, 400);
  }

  try {
    var raw = await geminiStreamGenerate(prompt, modelId, thinkMode, config);
    var text = extractResponseText(raw);

    // 构建 Google 格式的响应
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
// 🚀 主入口 - Cloudflare Workers fetch 事件处理器
// ============================================================================

export default {
  /**
   * Cloudflare Workers 的核心入口函数
   * 
   * 每个到达 Worker 的 HTTP 请求都会调用此函数。
   * 处理流程严格按照以下顺序:
   * 
   * 1. OPTIONS 预检 → 返回 CORS 头（浏览器跨域必须）
   * 2. 创建请求级配置 → getRequestConfig(env)（解决并发串扰）
   * 3. 速率限制检查 → checkRateLimit()（防滥用）
   * 4. API 密钥验证 → checkApiKey()（安全认证）
   * 5. 路由分发:
   *    GET  /health              → 健康检查
   *    GET  /v1/models           → 模型列表（OpenAI 格式）
   *    GET  /v1beta/models       → 模型列表（Google 格式）
   *    POST /v1/chat/completions → 聊天补全（OpenAI 格式）
   *    POST /v1/responses        → Responses API（Codex CLI）
   *    POST ...:generateContent  → 生成内容（Google 格式）
   *    POST /v1/*                → 万能兜底（自动转为 chat）
   * 
   * @param {Request} request - HTTP 请求对象
   * @param {Object} env - 环境变量（每个请求由 CF 平台独立注入）
   * @param {Object} ctx - 执行上下文
   * @returns {Promise<Response>} HTTP 响应对象
   */
  async fetch(request, env, ctx) {
    // ================================================================
    // 第一步：OPTIONS CORS 预检请求优先处理
    // ================================================================
    // 浏览器在发送跨域 POST 请求前会先发送 OPTIONS 预检请求。
    // 必须返回正确的 CORS 头，否则浏览器会阻止实际请求。
    // 这个处理必须在所有其他逻辑之前完成。
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,  // No Content
        headers: {
          'Access-Control-Allow-Origin': '*',              // 允许所有域访问
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',  // 允许的 HTTP 方法
          'Access-Control-Allow-Headers': '*',             // 允许所有自定义请求头
          'Access-Control-Max-Age': '86400',               // 预检结果缓存 24 小时（秒）
        },
      });
    }

    // ================================================================
    // 第二步：为当前请求创建独立的配置副本
    // ================================================================
    // 🔑 这是解决并发串扰问题的核心步骤。
    // 不修改任何全局变量，每个请求都有自己专属的 config 对象。
    // env 参数是 Cloudflare 为每个请求独立提供的环境变量。
    var config = getRequestConfig(env);

    // 解析请求 URL 和方法
    var requestUrl = new URL(request.url);
    var path = requestUrl.pathname;
    var method = request.method;

    // ================================================================
    // 第三步：速率限制检查
    // ================================================================
    // 使用 Cloudflare 提供的真实客户端 IP（CF-Connecting-IP 头）
    // 如果取不到（非 CF 代理），使用默认值 0.0.0.0
    var clientIP = request.headers.get('CF-Connecting-IP') || '0.0.0.0';
    if (!checkRateLimit(clientIP, config)) {
      log('Rate limit exceeded: ' + clientIP, 'WARN', config);
      return sendJSON({
        error: {
          message: '请求过于频繁，请稍后再试',
          type: 'rate_limit_exceeded',
        },
      }, 429);  // HTTP 429 Too Many Requests
    }

    // ================================================================
    // 第四步：API 密钥验证
    // ================================================================
    // 仅对 /v1 路径的请求进行密钥验证
    // /health 等公共端点不需要验证
    if (path.indexOf('/v1') === 0 && !checkApiKey(request, config)) {
      return sendJSON({
        error: { message: 'invalid api key' },
      }, 401);  // HTTP 401 Unauthorized
    }

    // ================================================================
    // 第五步：GET 请求处理
    // ================================================================
    if (method === 'GET') {
      // ---- 健康检查端点 ----
      // 可用于监控 Worker 是否正常运行
      // 返回版本号、模型列表、配置状态等信息
      if (path === '/' || path === '/health') {
        return sendJSON({
          status: 'ok',
          version: '1.5.0-cf-multifingerprint',  // 版本标识
          platform: 'Cloudflare Workers',
          models: Object.keys(MODELS),
          defaultModel: config.defaultModel,
          hasCookie: !!config.cookieString,      // 是否配置了 Cookie
          hasSapisid: !!config.sapisid,          // 是否配置了 SAPISID
          fingerprintJitterMs: config.fingerprintJitterMs,  // 随机延迟配置
          rateLimit: config.rateLimit,            // 速率限制配置
        });
      }

      // ---- OpenAI 格式模型列表 ----
      // 返回所有可用模型的信息
      // 客户端（NextChat、Cherry Studio 等）会调用此端点获取模型列表
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
      // 用于 Gemini CLI 等工具的模型发现
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
    // 第六步：POST 请求处理
    // ================================================================
    if (method === 'POST') {
      var body;
      try {
        body = await request.json();
      } catch (e) {
        return sendJSON({ error: { message: 'invalid JSON' } }, 400);
      }

      // ---- OpenAI Chat Completions API ----
      // 这是最常用的端点，处理聊天补全请求
      if (path === '/v1/chat/completions') {
        return handleChatCompletions(request, body, config);
      }

      // ---- OpenAI Responses API（Codex CLI 兼容） ----
      if (path === '/v1/responses') {
        return handleResponses(request, body, config);
      }

      // ---- Google 原生 generateContent（非流式） ----
      if (path.indexOf(':generateContent') !== -1 && path.indexOf('stream') === -1) {
        return handleGoogleAPI(request, body, false, config);
      }

      // ---- Google 原生 streamGenerateContent（流式） ----
      if (path.indexOf(':streamGenerateContent') !== -1) {
        return handleGoogleAPI(request, body, true, config);
      }

      // ---- 万能兜底路由 ----
      // 所有 /v1/ 下的未匹配 POST 请求都自动转为 chat 处理
      // 兼容各种客户端的路径差异
      if (path.indexOf('/v1/') === 0) {
        return handleChatCompletions(request, body, config);
      }

      // 未匹配的 POST 请求
      return sendJSON({ error: { message: 'not found' } }, 404);
    }

    // ================================================================
    // 第七步：未支持的 HTTP 方法
    // ================================================================
    return sendJSON({ error: { message: 'method not allowed' } }, 405);
  },
};
