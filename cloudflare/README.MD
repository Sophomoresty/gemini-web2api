# Gemini Web2API - Cloudflare Workers 部署文档

## 📖 项目简介

Gemini Web2API 是一个部署在 Cloudflare Workers 上的无服务器代理服务，将 Google Gemini 的 Web 界面转换为 OpenAI 兼容的 API 接口。无需服务器、无需 API Key（可选），开箱即用。

### 核心特性

- **零成本部署**：基于 Cloudflare Workers 免费计划（每日 10 万次请求）
- **全球加速**：自动部署到 Cloudflare 全球 300+ 边缘节点
- **OpenAI 兼容**：完全兼容 `/v1/chat/completions` 和 `/v1/models` 端点
- **打字机流式输出**：真正的 SSE（Server-Sent Events）流式响应
- **多指纹轮换**：8 种浏览器指纹 + 6 种语言偏好随机轮换，降低被识别概率
- **多 Cookie 轮换**：支持配置多个 Google 账号 Cookie，随机选择使用
- **并发安全**：请求级配置隔离，彻底消除高并发场景下的配置串扰
- **工具调用支持**：兼容 OpenAI Function Calling 格式

### 适用场景

- 为 NextChat、Cherry Studio、ChatBox 等客户端提供免费的 Gemini API
- 在 WorkBuddy 等工具中作为 Gemini 模型的后端
- 个人学习、研究和小型项目的 AI 能力接入

---

## 🚀 快速部署

### 第一步：登录 Cloudflare

1. 打开 [Cloudflare Dashboard](https://dash.cloudflare.com)
2. 登录你的 Cloudflare 账号（没有账号可以免费注册）
3. 进入左侧菜单 **Workers & Pages**

### 第二步：创建 Worker

1. 点击 **创建应用程序** → **创建 Worker**
2. 给 Worker 起一个名字（例如 `api`）
3. 点击 **部署** 按钮
4. 点击 **编辑代码** 按钮
5. 清空编辑器中的默认代码
6. 将本项目完整代码粘贴到编辑器中
7. 点击右上角 **保存并部署**

### 第三步：获取测试地址

部署成功后，你的 API 地址为：

```
https://你的worker名称.你的账户名.workers.dev
```

例如：`https://api.geminai.workers.dev`

### 第四步：验证部署

在浏览器中访问以下地址：

```
https://你的worker.workers.dev/health
```

如果看到类似以下 JSON 响应，说明部署成功：

```json
{
  "status": "ok",
  "version": "1.5.0-cf-multifingerprint",
  "platform": "Cloudflare Workers",
  "models": ["gemini-3.6-flash", "gemini-3.5-flash", "..."],
  "hasCookie": false,
  "hasSapisid": false
}
```

---

## 🔧 客户端配置

### NextChat (ChatGPT-Next-Web)

| 配置项 | 值 |
|--------|-----|
| 接口类型 | OpenAI |
| 接口地址 | `https://你的worker.workers.dev/v1` |
| API Key | `sk-gemini`（默认密钥） |
| 模型 | `gemini-3.6-flash` |

### Cherry Studio

| 配置项 | 值 |
|--------|-----|
| API 地址 | `https://你的worker.workers.dev/v1` |
| API 密钥 | `sk-gemini` |
| 模型 | `gemini-3.6-flash` |

### ChatBox

| 配置项 | 值 |
|--------|-----|
| API 模式 | OpenAI API |
| API 域名 | `https://你的worker.workers.dev` |
| API 路径 | `/v1/chat/completions` |
| API Key | `sk-gemini` |

### 使用 curl 测试

```bash
# 非流式请求
curl https://你的worker.workers.dev/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-gemini" \
  -d '{
    "model": "gemini-3.6-flash",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'

# 流式请求（打字机效果）
curl -N https://你的worker.workers.dev/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-gemini" \
  -d '{
    "model": "gemini-3.6-flash",
    "messages": [{"role": "user", "content": "讲个故事"}],
    "stream": true
  }'
```

---

## ⚙️ 环境变量配置（可选）

在 Cloudflare Dashboard → Workers → 你的 Worker → 设置 → 变量 → 环境变量中配置：

### 认证相关

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `COOKIE_STRING` | Gemini Cookie，多个用 `\|` 分隔 | `cookie1\| cookie2\| cookie3` |
| `SAPISID` | SAPISID 值，多个用 `\|` 分隔 | `sapisid1\| sapisid2\| sapisid3` |
| `API_KEYS` | API 密钥白名单（JSON 数组） | `["sk-gemini", "my-key"]` |

### Gemini 配置

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `GEMINI_BL` | Gemini 构建标签（遇到 405 时更新） | `boq_assistant-bard-web-server_20260716.08_p0` |
| `DEFAULT_MODEL` | 默认模型 | `gemini-3.6-flash` |
| `AUTH_USER` | 多账户索引 | `0` |

### 性能调优

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `RETRY_ATTEMPTS` | 重试次数 | `3` |
| `RETRY_DELAY_SEC` | 重试间隔（秒） | `2` |
| `REQUEST_TIMEOUT_SEC` | 请求超时（秒） | `28` |
| `FINGERPRINT_JITTER_MS` | 随机延迟最大值（毫秒） | `1500` |
| `RATE_LIMIT_MAX` | 速率限制最大请求数 | `3000` |
| `RATE_LIMIT_WINDOW` | 速率限制时间窗口（秒） | `60` |

---

## 🍪 获取 Gemini Cookie

### 为什么需要 Cookie？

匿名请求容易被 Gemini 限流（返回 HTTP 429 错误）。配置有效的 Cookie 可以：
- 大幅降低被限流的概率
- 提升 Pro 模型的路由质量
- 获得更稳定的服务体验

### 获取步骤

1. 打开 Chrome/Edge 浏览器
2. 访问 https://gemini.google.com/app 并登录 Google 账号
3. 按 **F12** 打开开发者工具
4. 进入 **Application**（应用程序）标签
5. 左侧选择 **Cookies** → `https://gemini.google.com`
6. 找到以下 Cookie 并复制其值：
   - `__Secure-1PSID`
   - `__Secure-3PSID`
   - `SAPISID`
7. 组合为完整 Cookie 字符串：
   ```
   __Secure-1PSID=你的值; __Secure-3PSID=你的值; SAPISID=你的值
   ```

### 多账号配置

如果你有多个 Google 账号，可以用 `|` 分隔多个 Cookie：

```
COOKIE_STRING = "cookie_账号1| cookie_账号2| cookie_账号3"
SAPISID = "sapisid_1| sapisid_2| sapisid_3"
```

每次请求会随机选择一个 Cookie 使用，大幅降低单个账号被限流的概率。

---

## 🔄 更新 BL 版本

如果遇到 `HTTP 405: Method Not Allowed` 错误，说明 Gemini 前端已更新，需要同步更新构建标签：

1. 浏览器打开 https://gemini.google.com/app
2. 按 **F12** → **Network**（网络）标签
3. 在任意请求的 URL 中搜索 `boq_assistant`
4. 复制最新的版本号，例如：
   ```
   boq_assistant-bard-web-server_20260730.02_p0
   ```
5. 更新环境变量 `GEMINI_BL` 或代码中的 `geminiBl` 配置项

---

## 🎭 多指纹轮换机制

本程序内置了浏览器指纹轮换系统，每次请求会随机选择不同的浏览器标识：

| 指纹类型 | 池大小 | 说明 |
|---------|--------|------|
| User-Agent | 8 种 | 加权随机，模拟真实浏览器市场份额 |
| Accept-Language | 6 种 | 均匀随机，模拟不同地区用户 |
| Sec-Ch-Ua | 3 种 | Chrome 版本标识（仅 Chrome UA 时添加） |
| 随机延迟 | 0-1500ms | 请求前添加随机延迟，模拟人类操作 |

---

## 🛡️ 安全建议

1. **修改默认 API Key**：将 `apiKeys` 中的 `sk-gemini` 改为你自己的密钥
2. **设置速率限制**：根据实际使用量调整 `RATE_LIMIT_MAX`
3. **定期更新 Cookie**：Google Cookie 会过期，需要定期更换
4. **不要分享 Cookie**：Cookie 等同于你的 Google 账号凭证

---

## ❓ 常见问题

### Q: 返回 `empty response from server`

**原因**：NextChat 流式解析问题。  
**解决**：确认使用的是最新版代码（已修复 SSE 格式）。

### Q: 返回 `HTTP 429: Too Many Requests`

**原因**：Gemini 限流，匿名请求频率限制更严格。  
**解决**：配置有效的 `COOKIE_STRING` 和 `SAPISID`。

### Q: 返回 `HTTP 405: Method Not Allowed`

**原因**：BL 版本过期。  
**解决**：更新 `geminiBl` 配置（参见上文「更新 BL 版本」章节）。

### Q: 返回 `invalid api key`

**原因**：客户端密钥配置错误。  
**解决**：检查客户端是否配置了正确的 API Key（默认 `sk-gemini`）。

### Q: WorkBuddy 中使用出现串扰

**原因**：多模型并发请求共享全局配置。  
**解决**：当前版本已通过请求级配置隔离解决此问题。

---

## 📊 支持模型列表

| 模型 ID | 类型 | 说明 |
|---------|------|------|
| `gemini-3.6-flash` | FAST | 最新全能模型 |
| `gemini-3.5-flash` | FAST | 3.6 Flash 的别名 |
| `gemini-3.5-flash-thinking` | THINKING | 深度思考模式 |
| `gemini-3.1-pro` | PRO | 专业版（需 Cookie） |
| `gemini-auto` | AUTO | 自动模型选择 |
| `gemini-3.5-flash-thinking-lite` | DYNAMIC | 自适应动态思考 |
| `gemini-flash-lite` | LITE | 轻量级快速模型 |

支持通过 `@think=` 参数覆盖思考模式：
- `gemini-3.6-flash@think=0` — Flash 模型 + 深度思考
- `gemini-3.1-pro@think=4` — Pro 模型 + 自动思考

---

## 📝 更新日志

| 版本 | 日期 | 更新内容 |
|------|------|---------|
| 1.5.0 | 2026-07-31 | 新增多指纹轮换、多Cookie轮换、随机延迟机制 |
| 1.4.0 | 2026-07-30 | 修复并发串扰、速率限制内存安全 |
| 1.3.0 | 2026-07-29 | 修复 SSE 流式格式、NextChat 兼容性 |
| 1.0.0 | 2026-07-16 | 初始版本，基于 gemini-web2api v1.1.0 移植 |

---

## 📄 许可证

本项目基于原项目 [gemini-web2api](https://github.com/your-repo/gemini-web2api) 移植，遵循原项目的开源协议。
