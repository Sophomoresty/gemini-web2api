"""中文 Web 管理台页面。"""

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Gemini2API 管理台</title>
  <style>
    :root{
      --bg:#0b0b0f;--panel:#121218;--panel2:#181820;--panel3:#20202a;--text:#f7f7f8;--muted:#a5a5b2;
      --line:#2a2a36;--primary:#f5b301;--primary2:#1d73e8;--good:#16a34a;--danger:#ef4444;--warn:#f59e0b;
      --shadow:0 18px 55px rgba(0,0,0,.28)
    }
    *{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
    button,input,select,textarea{font:inherit}button{cursor:pointer}button:disabled{opacity:.55;cursor:wait}
    .app{min-height:100vh;display:grid;grid-template-columns:264px minmax(0,1fr)}
    .sidebar{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;border-right:1px solid var(--line);background:#111116;padding:22px 16px}
    .brand{display:flex;align-items:center;gap:12px;padding:0 6px 22px}.brand img{width:38px;height:38px;border-radius:8px;background:#fff;object-fit:cover}.brand strong{display:block;font-size:20px}.brand span{display:block;margin-top:2px;color:var(--muted);font-size:12px}
    .nav{display:grid;gap:7px}.nav button{display:flex;align-items:center;gap:11px;width:100%;min-height:42px;border:1px solid transparent;border-radius:8px;background:transparent;color:var(--muted);padding:10px 12px;text-align:left;font-weight:700}.nav button:hover{background:var(--panel2);color:var(--text)}.nav button.active{background:var(--panel3);border-color:var(--line);color:var(--primary)}.nav .dot{width:8px;height:8px;border-radius:999px;background:currentColor}
    .side-foot{margin-top:auto;border-top:1px solid var(--line);padding-top:16px;display:grid;gap:10px}.side-mini{display:grid;grid-template-columns:1fr 1fr;gap:8px}.metric{border:1px solid var(--line);background:var(--panel);border-radius:8px;padding:10px}.metric .label{font-size:11px;color:var(--muted);font-weight:700}.metric .value{font-size:20px;font-weight:900;margin-top:4px}.logout{display:flex;align-items:center;justify-content:center;height:40px;border:1px solid var(--line);border-radius:8px;color:var(--muted);text-decoration:none;font-size:13px;font-weight:800}.logout:hover{color:var(--danger);border-color:rgba(239,68,68,.35);background:rgba(239,68,68,.08)}
    .main{min-width:0;height:100vh;overflow:auto;padding:28px}.wrap{max-width:1220px;margin:0 auto;display:grid;gap:18px}.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.top h1{margin:0;font-size:28px;line-height:1.15}.top p{margin:8px 0 0;color:var(--muted)}.top-actions{display:flex;gap:10px;align-items:center}
    .summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.card,.panel{border:1px solid var(--line);background:var(--panel);border-radius:8px;box-shadow:var(--shadow)}.card{padding:16px}.card .label{color:var(--muted);font-size:12px;font-weight:800}.card .value{font-size:26px;font-weight:950;margin-top:6px}.card .sub{color:var(--muted);font-size:12px;margin-top:4px;word-break:break-word}
    .panel{overflow:hidden}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 20px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.015)}.panel-title{font-size:16px;font-weight:900}.panel-desc{font-size:12px;color:var(--muted);margin-top:4px}.panel-body{padding:18px 20px}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.stack{display:grid;gap:14px}.row{display:flex;align-items:center;gap:10px}.row.wrap{flex-wrap:wrap}.field{display:grid;gap:7px}.field label{font-size:12px;color:var(--muted);font-weight:800}.input,select,textarea{width:100%;border:1px solid var(--line);border-radius:8px;background:#0a0a0f;color:var(--text);padding:10px 12px;outline:none}.input:focus,select:focus,textarea:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(245,179,1,.13)}textarea{min-height:126px;resize:vertical}
    .btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:38px;border:1px solid var(--line);border-radius:8px;background:var(--panel3);color:var(--text);padding:9px 13px;font-weight:850;white-space:nowrap}.btn:hover{border-color:#3c3c4a;background:#272735}.btn.primary{background:var(--primary);border-color:var(--primary);color:#15120a}.btn.blue{background:var(--primary2);border-color:var(--primary2);color:#fff}.btn.danger{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.32);color:#fca5a5}.btn.ghost{background:transparent}.btn.small{min-height:32px;padding:6px 9px;font-size:12px}
    .table{width:100%;border-collapse:collapse}.table th,.table td{border-bottom:1px solid var(--line);padding:12px;text-align:left;vertical-align:top}.table th{font-size:11px;color:var(--muted);font-weight:900;letter-spacing:.06em}.table td{font-size:13px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace}.muted{color:var(--muted)}.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;background:var(--panel3);padding:5px 8px;font-size:11px;font-weight:800}.badge.good{color:#86efac;border-color:rgba(22,163,74,.3);background:rgba(22,163,74,.1)}.badge.bad{color:#fca5a5;border-color:rgba(239,68,68,.3);background:rgba(239,68,68,.1)}.badge.warn{color:#fbbf24;border-color:rgba(245,158,11,.3);background:rgba(245,158,11,.1)}
    pre{margin:0;white-space:pre-wrap;word-break:break-word;border:1px solid var(--line);border-radius:8px;background:#07070a;color:#e7e7ee;padding:13px;font-size:12px;line-height:1.55;max-height:360px;overflow:auto}.samples{display:grid;gap:12px}.sample-title{font-size:12px;color:var(--muted);font-weight:900;margin-bottom:6px}.notice{display:none;border-radius:8px;border:1px solid var(--line);padding:12px 14px;font-size:13px}.notice.show{display:block}.notice.ok{background:rgba(22,163,74,.1);border-color:rgba(22,163,74,.28);color:#86efac}.notice.error{background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.28);color:#fca5a5}
    .empty{padding:34px;text-align:center;color:var(--muted)}.split{display:grid;grid-template-columns:minmax(0,1fr) 420px;gap:14px}.model-list{display:flex;flex-wrap:wrap;gap:8px}.model-chip{border:1px solid var(--line);background:var(--panel3);border-radius:999px;padding:7px 9px;font-size:12px}
    .chat-layout{display:grid;grid-template-columns:340px minmax(0,1fr);gap:14px;min-height:620px}.chat-panel{display:grid;grid-template-rows:auto minmax(0,1fr) auto;min-height:620px}.chat-messages{min-height:0;overflow:auto;padding:18px 20px;display:flex;flex-direction:column;gap:13px;background:linear-gradient(180deg,rgba(255,255,255,.018),rgba(255,255,255,0))}.bubble{max-width:min(760px,88%);border:1px solid var(--line);border-radius:8px;padding:12px 14px;line-height:1.65;white-space:pre-wrap;word-break:break-word}.bubble.user{align-self:flex-end;background:rgba(29,115,232,.12);border-color:rgba(29,115,232,.32)}.bubble.assistant{align-self:flex-start;background:var(--panel2)}.bubble .role{display:block;margin-bottom:5px;color:var(--muted);font-size:11px;font-weight:900}.chat-composer{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;border-top:1px solid var(--line);padding:14px;background:rgba(255,255,255,.015)}.chat-composer textarea{min-height:58px;max-height:180px}
    @media(max-width:980px){.app{grid-template-columns:1fr}.sidebar{position:static;height:auto}.main{height:auto;padding:16px}.summary,.grid2,.grid3,.split,.chat-layout{grid-template-columns:1fr}.top{flex-direction:column}.side-foot{display:none}.panel-head{align-items:flex-start;flex-direction:column}.row,.chat-composer{align-items:stretch;grid-template-columns:1fr;flex-direction:column}.table{min-width:780px}.table-wrap{overflow:auto}.btn{width:100%}.chat-layout,.chat-panel{min-height:540px}}
  </style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><img src="/assets/logo.png" alt="" /><div><strong>Gemini2API</strong><span>中文管理台</span></div></div>
    <nav id="nav" class="nav"></nav>
    <div class="side-foot">
      <div class="side-mini">
        <div class="metric"><div class="label">API 密钥</div><div id="side-keys" class="value">-</div></div>
        <div class="metric"><div class="label">模型</div><div id="side-models" class="value">-</div></div>
      </div>
      <div class="metric"><div class="label">代理</div><div id="side-proxy" class="sub">读取中</div></div>
      <a class="logout" href="/admin/logout">退出登录</a>
    </div>
  </aside>
  <main class="main">
    <div class="wrap">
      <header class="top">
        <div><h1 id="page-title">管理台</h1><p id="page-desc">读取中</p></div>
        <div class="top-actions"><button id="refresh" class="btn ghost" type="button">刷新</button></div>
      </header>
      <section id="summary" class="summary"></section>
      <div id="notice" class="notice"></div>
      <section id="content"></section>
    </div>
  </main>
</div>
<script>
const tabs=[
  {id:"accounts",label:"账号管理",desc:"管理外部调用密钥、模型和接口样例"},
  {id:"stats",label:"会话统计",desc:"查看本服务记录的请求成功率、Token 估算和最近会话"},
  {id:"proxy",label:"代理 IP",desc:"查看、修改并测试当前上游代理出口"},
  {id:"test",label:"API 测试",desc:"直接用对话框测试当前模型响应"},
  {id:"settings",label:"设置中心",desc:"在线调整默认模型、重试、超时、日志和登录信息"}
];
const state={active:"accounts",status:{},settings:{},models:[],apiKeys:[],stats:{summary:{},recent:[],by_model:[],by_key:[]},notice:null,proxyResult:null,chatMessages:[],chatLoading:false};
const $=id=>document.getElementById(id);
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
const fmt=n=>new Intl.NumberFormat("zh-CN").format(Number(n||0));
const pct=n=>`${(Number(n||0)*100).toFixed(Number(n||0)>0.995?0:1)}%`;
function timeText(value){const n=Number(value||0);if(!n)return "-";return new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"}).format(new Date(n<1e12?n*1000:n))}
function maskKey(key){const text=String(key||"");if(text.length<=14)return text;return `${text.slice(0,8)}...${text.slice(-6)}`}
function modelOptions(selected){return state.models.map(m=>`<option value="${esc(m.id)}" ${m.id===selected?"selected":""}>${esc(m.id)}</option>`).join("")}
function apiFetch(url,options={}){return fetch(url,{...options,headers:{"Content-Type":"application/json",...(options.headers||{})}}).then(async response=>{const data=await response.json().catch(()=>({}));if(response.status===401){location.href="/admin/login";return {}}if(!response.ok)throw new Error(data.error||data.message||data.detail||"请求失败");return data})}
function showNotice(type,text){state.notice={type,text};renderNotice();clearTimeout(showNotice.timer);showNotice.timer=setTimeout(()=>{state.notice=null;renderNotice()},4500)}
function renderNotice(){const box=$("notice");if(!state.notice){box.className="notice";box.textContent="";return}box.className=`notice show ${state.notice.type==="error"?"error":"ok"}`;box.textContent=state.notice.text}
function renderNav(){const nav=$("nav");nav.innerHTML=tabs.map(t=>`<button type="button" data-tab="${t.id}" class="${state.active===t.id?"active":""}"><span class="dot"></span><span>${t.label}</span></button>`).join("");const tab=tabs.find(t=>t.id===state.active)||tabs[0];$("page-title").textContent=tab.label;$("page-desc").textContent=tab.desc}
function renderSummary(){const summary=state.stats.summary||{};$("side-keys").textContent=state.apiKeys.length;$("side-models").textContent=state.models.length;$("side-proxy").textContent=state.status.proxy||"未配置";$("summary").innerHTML=[
  ["服务版本",`v${esc(state.status.version||"__VERSION__")}`,state.status.cookie?"已配置 Cookie":"未配置 Cookie"],
  ["API 密钥",fmt(state.apiKeys.length),state.status.api_auth?"接口已启用鉴权":"接口未启用鉴权"],
  ["成功率",pct(summary.success_rate),`成功 ${fmt(summary.success)} / 失败 ${fmt(summary.failure)}`],
  ["Token 估算",fmt(summary.total_tokens),`请求总数 ${fmt(summary.total)}`],
].map(([a,b,c])=>`<div class="card"><div class="label">${a}</div><div class="value">${b}</div><div class="sub">${c}</div></div>`).join("")}
function render(){renderNav();renderSummary();renderNotice();const fn={accounts:renderAccounts,stats:renderStats,proxy:renderProxy,test:renderTest,settings:renderSettings}[state.active]||renderAccounts;$("content").innerHTML=fn();bindSection()}
function renderAccounts(){const key=state.apiKeys[0]||"";const model=state.settings.default_model||state.status.default_model||(state.models[0]?.id||"");const origin=location.origin;return `<div class="stack">
  <div class="panel"><div class="panel-head"><div><div class="panel-title">可用模型</div><div class="panel-desc">聊天测试和调用样例会使用这里的模型</div></div><button id="reload-models" class="btn small" type="button">刷新模型</button></div>
    <div class="panel-body"><div class="model-list">${state.models.map(m=>`<span class="model-chip" title="${esc(m.description||"")}">${esc(m.id)}</span>`).join("")||"<div class='empty'>暂无模型</div>"}</div></div>
  </div>
  <div class="panel"><div class="panel-head"><div><div class="panel-title">API 密钥</div><div class="panel-desc">这些密钥就是外部调用接口时使用的 Bearer Key</div></div><button id="generate-key" class="btn primary" type="button">自动创建</button></div>
    <div class="panel-body stack">
      <form id="create-key-form" class="row"><input id="new-key" class="input" placeholder="自定义 API 密钥，留空可点自动创建" /><button class="btn blue" type="submit">创建密钥</button></form>
      <div class="table-wrap"><table class="table"><thead><tr><th>密钥</th><th>最近统计</th><th>操作</th></tr></thead><tbody>${renderKeyRows()}</tbody></table></div>
    </div>
  </div>
  <div class="panel"><div class="panel-head"><div><div class="panel-title">调用样例</div><div class="panel-desc">这里只显示当前项目实际可用的接口</div></div></div><div class="panel-body samples">${renderSamples(origin,key,model)}</div></div>
</div>`}
function renderKeyRows(){if(!state.apiKeys.length)return `<tr><td colspan="3" class="empty">暂无 API 密钥</td></tr>`;const byKey=Object.fromEntries((state.stats.by_key||[]).map(i=>[i.key,i]));return state.apiKeys.map((key,i)=>{const s=byKey[maskKey(key)]||{};return `<tr><td><input class="input mono" data-key-input="${i}" value="${esc(key)}" /><div class="muted mono">${esc(maskKey(key))}</div></td><td><span class="badge good">成功 ${fmt(s.success)}</span> <span class="badge bad">失败 ${fmt(s.failure)}</span><div class="muted">最后调用：${timeText(s.last_at)}</div></td><td><div class="row wrap"><button class="btn small" data-action="save-key" data-index="${i}" type="button">保存</button><button class="btn small" data-action="copy-key" data-index="${i}" type="button">复制</button><button class="btn small danger" data-action="delete-key" data-index="${i}" type="button">删除</button></div></td></tr>`}).join("")}
function renderStats(){const summary=state.stats.summary||{};const modelRows=(state.stats.by_model||[]).map(i=>`<tr><td class="mono">${esc(i.model||"-")}</td><td>${fmt(i.success)}</td><td>${fmt(i.failure)}</td><td>${fmt(i.total_tokens)}</td><td>${timeText(i.last_at)}</td></tr>`).join("")||`<tr><td colspan="5" class="empty">暂无模型统计</td></tr>`;const recent=(state.stats.recent||[]).map(i=>`<tr><td>${timeText(i.at)}</td><td>${esc(i.source||"-")}</td><td class="mono">${esc(i.model||"-")}</td><td>${i.status==="success"?"<span class='badge good'>成功</span>":"<span class='badge bad'>失败</span>"}</td><td>${fmt(i.total_tokens)}</td><td class="mono">${esc(i.key||"-")}</td><td>${esc(i.error||"")}</td></tr>`).join("")||`<tr><td colspan="7" class="empty">暂无会话记录</td></tr>`;return `<div class="stack">
  <div class="grid3">
    <div class="card"><div class="label">总请求</div><div class="value">${fmt(summary.total)}</div><div class="sub">自 ${timeText(summary.started_at)} 起</div></div>
    <div class="card"><div class="label">成功率</div><div class="value">${pct(summary.success_rate)}</div><div class="sub">成功 ${fmt(summary.success)} / 失败 ${fmt(summary.failure)}</div></div>
    <div class="card"><div class="label">Token 估算</div><div class="value">${fmt(summary.total_tokens)}</div><div class="sub">输入 ${fmt(summary.input_tokens)} / 输出 ${fmt(summary.output_tokens)}</div></div>
  </div>
  <div class="panel"><div class="panel-head"><div><div class="panel-title">按模型统计</div><div class="panel-desc">按请求模型聚合成功、失败与 Token 估算</div></div><button id="reload-stats" class="btn small" type="button">刷新统计</button></div><div class="table-wrap"><table class="table"><thead><tr><th>模型</th><th>成功</th><th>失败</th><th>Token</th><th>最后调用</th></tr></thead><tbody>${modelRows}</tbody></table></div></div>
  <div class="panel"><div class="panel-head"><div><div class="panel-title">最近会话</div><div class="panel-desc">最多保留最近 80 条记录，统计文件：${esc(state.stats.path||"")}</div></div></div><div class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>来源</th><th>模型</th><th>状态</th><th>Token</th><th>密钥</th><th>错误</th></tr></thead><tbody>${recent}</tbody></table></div></div>
</div>`}
function renderProxy(){const info=state.status.proxy_info||{};return `<div class="split">
  <div class="panel"><div class="panel-head"><div><div class="panel-title">代理 IP</div><div class="panel-desc">当前项目使用全局上游代理，留空则直连</div></div></div>
    <form id="proxy-form" class="panel-body stack">
      <div class="field"><label>代理地址</label><input id="proxy-url" class="input mono" value="${esc(state.settings.proxy||state.status.proxy||"")}" placeholder="http://proxy.example:8080" /></div>
      <div class="grid3">
        <div class="metric"><div class="label">类型</div><div class="value">${esc(info.scheme||"-")}</div></div>
        <div class="metric"><div class="label">主机</div><div class="value" style="font-size:16px">${esc(info.host||"-")}</div></div>
        <div class="metric"><div class="label">端口</div><div class="value">${esc(info.port||"-")}</div></div>
      </div>
      <div class="row"><button class="btn primary" type="submit">保存代理</button><button id="test-proxy" class="btn" type="button">测试代理</button></div>
    </form>
  </div>
  <div class="panel"><div class="panel-head"><div><div class="panel-title">代理测试结果</div><div class="panel-desc">测试访问 Gemini 上游入口</div></div></div><div class="panel-body"><pre id="proxy-result">${esc(state.proxyResult?JSON.stringify(state.proxyResult,null,2):"尚未测试")}</pre></div></div>
</div>`}
function renderTest(){const model=state.settings.default_model||state.status.default_model||(state.models[0]?.id||"");return `<div class="chat-layout">
  <div class="panel"><div class="panel-head"><div><div class="panel-title">测试配置</div><div class="panel-desc">选择模型后直接发送消息</div></div></div>
    <div class="panel-body stack">
      <div class="field"><label>模型</label><select id="chat-model">${modelOptions(model)}</select></div>
      <div class="grid2">
        <div class="metric"><div class="label">消息数</div><div class="value">${fmt(state.chatMessages.length)}</div></div>
        <div class="metric"><div class="label">统计</div><div class="value">${fmt((state.stats.summary||{}).total)}</div></div>
      </div>
      <button id="clear-chat" class="btn" type="button">清空对话</button>
    </div>
  </div>
  <div class="panel chat-panel">
    <div class="panel-head"><div><div class="panel-title">在线对话</div><div class="panel-desc">直接验证当前模型回复</div></div></div>
    <div id="chat-messages" class="chat-messages">${renderChatMessages()}</div>
    <form id="chat-form" class="chat-composer">
      <textarea id="chat-input" name="message" placeholder="输入测试消息"></textarea>
      <button class="btn primary" type="submit" ${state.chatLoading?"disabled":""}>${state.chatLoading?"发送中":"发送"}</button>
    </form>
  </div>
</div>`}
function renderChatMessages(){if(!state.chatMessages.length)return `<div class="empty">暂无对话</div>`;return state.chatMessages.map(item=>`<div class="bubble ${item.role==="user"?"user":"assistant"}"><span class="role">${item.role==="user"?"你":"Gemini"}</span>${esc(item.content||"")}</div>`).join("")}
function renderSamples(origin,key,model){const safeKey=key||"<API_KEY>";const safeModel=model||"<MODEL>";return `<div><div class="sample-title">OpenAI Chat Completions</div><pre>curl ${origin}/v1/chat/completions \\
  -H "Authorization: Bearer ${safeKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${safeModel}","messages":[{"role":"user","content":"你好"}]}'</pre></div>
<div><div class="sample-title">OpenAI Responses API</div><pre>from openai import OpenAI

client = OpenAI(base_url="${origin}/v1", api_key="${safeKey}")
response = client.responses.create(model="${safeModel}", input="你好")
print(response.output_text)</pre></div>
<div><div class="sample-title">Google Native API</div><pre>curl "${origin}/v1beta/models/${safeModel}:generateContent?key=${safeKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"contents":[{"role":"user","parts":[{"text":"你好"}]}]}'</pre></div>`}
function renderSettings(){return `<div class="panel"><div class="panel-head"><div><div class="panel-title">设置中心</div><div class="panel-desc">保存后立即写回 config.json，代理改动会重建流式客户端</div></div></div>
  <form id="settings-form" class="panel-body stack">
    <div class="grid3"><div class="field"><label>Web 登录账号</label><input class="input" name="web_username" value="${esc(state.settings.web_username||"admin")}" autocomplete="username" /></div><div class="field"><label>新密码</label><input class="input" name="web_password" type="password" placeholder="留空不修改" autocomplete="new-password" /></div><div class="field"><label>登录有效期（秒）</label><input class="input" name="session_ttl_sec" type="number" min="300" value="${esc(state.settings.session_ttl_sec||86400)}" /></div></div>
    <div class="grid2"><div class="field"><label>默认模型</label><select name="default_model">${modelOptions(state.settings.default_model||state.status.default_model)}</select></div><div class="field"><label>代理地址</label><input class="input mono" name="proxy" value="${esc(state.settings.proxy||"")}" /></div></div>
    <div class="grid3"><div class="field"><label>请求超时（秒）</label><input class="input" name="request_timeout_sec" type="number" min="5" value="${esc(state.settings.request_timeout_sec||180)}" /></div><div class="field"><label>重试次数</label><input class="input" name="retry_attempts" type="number" min="1" max="10" value="${esc(state.settings.retry_attempts||3)}" /></div><div class="field"><label>重试间隔（秒）</label><input class="input" name="retry_delay_sec" type="number" min="0" max="60" value="${esc(state.settings.retry_delay_sec||2)}" /></div></div>
    <div class="field"><label>Cookie 文件</label><input class="input mono" name="cookie_file" value="${esc(state.settings.cookie_file||"")}" placeholder="留空则不使用 Cookie" /></div>
    <label class="row" style="align-items:center"><input name="log_requests" type="checkbox" ${state.settings.log_requests?"checked":""} style="width:auto" /> <span>记录请求日志</span></label>
    <div><button class="btn primary" type="submit">保存设置</button></div>
  </form>
</div>`}
function bindSection(){const content=$("content");const byId=id=>content.querySelector(`#${id}`);
  byId("reload-models")?.addEventListener("click",loadModelsAndRender);byId("reload-stats")?.addEventListener("click",loadStatsAndRender);
  byId("generate-key")?.addEventListener("click",async()=>{try{const data=await apiFetch("/admin/api/api-keys",{method:"POST",body:JSON.stringify({})});state.apiKeys=data.api_keys||[];showNotice("ok","API 密钥已创建");await loadStats();render()}catch(e){showNotice("error",e.message)}})
  byId("create-key-form")?.addEventListener("submit",async event=>{event.preventDefault();const input=byId("new-key");try{const data=await apiFetch("/admin/api/api-keys",{method:"POST",body:JSON.stringify({key:input.value})});state.apiKeys=data.api_keys||[];showNotice("ok","API 密钥已创建");render()}catch(e){showNotice("error",e.message)}})
  content.querySelectorAll("[data-action]").forEach(btn=>btn.addEventListener("click",handleKeyAction));
  byId("settings-form")?.addEventListener("submit",saveSettings);byId("proxy-form")?.addEventListener("submit",saveProxy);byId("test-proxy")?.addEventListener("click",testProxy);byId("chat-form")?.addEventListener("submit",sendChat);byId("clear-chat")?.addEventListener("click",()=>{state.chatMessages=[];render()})}
async function handleKeyAction(event){const index=Number(event.currentTarget.dataset.index);const key=state.apiKeys[index];const action=event.currentTarget.dataset.action;if(action==="copy-key"){await navigator.clipboard?.writeText(key);showNotice("ok","已复制 API 密钥");return}if(action==="delete-key"){if(!confirm("确定删除这个 API 密钥吗？"))return;try{const data=await apiFetch("/admin/api/api-keys",{method:"DELETE",body:JSON.stringify({key})});state.apiKeys=data.api_keys||[];showNotice("ok","API 密钥已删除");render()}catch(e){showNotice("error",e.message)}return}if(action==="save-key"){const input=document.querySelector(`[data-key-input="${index}"]`);try{const data=await apiFetch("/admin/api/api-keys",{method:"PATCH",body:JSON.stringify({old_key:key,new_key:input.value})});state.apiKeys=data.api_keys||[];showNotice("ok","API 密钥已保存");render()}catch(e){showNotice("error",e.message)}}}
async function saveSettings(event){event.preventDefault();const form=new FormData(event.currentTarget);const payload={web_username:String(form.get("web_username")||""),default_model:String(form.get("default_model")||""),proxy:String(form.get("proxy")||""),cookie_file:String(form.get("cookie_file")||""),request_timeout_sec:Number(form.get("request_timeout_sec")||180),retry_attempts:Number(form.get("retry_attempts")||3),retry_delay_sec:Number(form.get("retry_delay_sec")||2),session_ttl_sec:Number(form.get("session_ttl_sec")||86400),log_requests:form.get("log_requests")==="on"};const password=String(form.get("web_password")||"");if(password)payload.web_password=password;try{const data=await apiFetch("/admin/api/settings",{method:"PATCH",body:JSON.stringify(payload)});state.settings=data.settings||state.settings;await loadStatus();showNotice("ok","设置已保存");render()}catch(e){showNotice("error",e.message)}}
async function saveProxy(event){event.preventDefault();const proxy=event.currentTarget.querySelector("#proxy-url").value;try{const data=await apiFetch("/admin/api/proxy",{method:"PATCH",body:JSON.stringify({proxy})});state.settings.proxy=data.proxy||"";state.status.proxy=data.proxy||"";state.status.proxy_info=data.proxy_info||{};showNotice("ok","代理已保存");render()}catch(e){showNotice("error",e.message)}}
async function testProxy(){const proxy=document.querySelector("#proxy-url")?.value||state.settings.proxy||"";const btn=document.querySelector("#test-proxy");btn.disabled=true;state.proxyResult={testing:true,proxy};render();try{state.proxyResult=await apiFetch("/admin/api/proxy/test",{method:"POST",body:JSON.stringify({proxy})});showNotice(state.proxyResult.success?"ok":"error",state.proxyResult.message||"代理测试完成")}catch(e){state.proxyResult={success:false,message:e.message};showNotice("error",e.message)}finally{btn.disabled=false;render()}}
function scrollChatToBottom(){const box=document.querySelector("#chat-messages");if(box)box.scrollTop=box.scrollHeight}
function updateLastAssistant(content){const last=state.chatMessages[state.chatMessages.length-1];if(last&&last.role==="assistant"){last.content=content;render();scrollChatToBottom()}}
async function readChatStream(response){if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.error||data.message||"请求失败")}if(!response.body){throw new Error("当前浏览器不支持流式读取")}const reader=response.body.getReader();const decoder=new TextDecoder();let buffer="";let content="";while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const frames=buffer.split("\n\n");buffer=frames.pop()||"";for(const frame of frames){const lines=frame.split("\n").filter(line=>line.startsWith("data:"));for(const line of lines){const raw=line.slice(5).trim();if(!raw||raw==="[DONE]")continue;let payload;try{payload=JSON.parse(raw)}catch{continue}if(payload.type==="delta"){content+=payload.delta||"";updateLastAssistant(content)}else if(payload.type==="done"){content=payload.content??content;updateLastAssistant(content)}else if(payload.type==="error"){throw new Error(payload.error||"请求失败")}}}}return content}
async function sendChat(event){event.preventDefault();const form=new FormData(event.currentTarget);const message=String(form.get("message")||"").trim();const model=document.querySelector("#chat-model")?.value||state.settings.default_model||state.status.default_model;if(!message)return;const input=document.querySelector("#chat-input");const history=state.chatMessages.slice(-20);state.chatMessages.push({role:"user",content:message});state.chatMessages.push({role:"assistant",content:""});state.chatLoading=true;if(input)input.value="";render();scrollChatToBottom();try{const response=await fetch("/admin/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model,message,history,stream:true})});const content=await readChatStream(response);if(!content){updateLastAssistant("")}await loadStats()}catch(e){updateLastAssistant(`请求失败：${e.message}`);showNotice("error",e.message)}finally{state.chatLoading=false;render();scrollChatToBottom()}}
async function loadStatus(){state.status=await apiFetch("/admin/api/status")}
async function loadSettings(){const data=await apiFetch("/admin/api/settings");state.settings=data.settings||{}}
async function loadModels(){const data=await apiFetch("/admin/api/models");state.models=data.data||[]}
async function loadKeys(){const data=await apiFetch("/admin/api/api-keys");state.apiKeys=data.api_keys||[]}
async function loadStats(){state.stats=await apiFetch("/admin/api/session-stats")}
async function loadModelsAndRender(){try{await loadModels();showNotice("ok","模型已刷新");render()}catch(e){showNotice("error",e.message)}}
async function loadStatsAndRender(){try{await loadStats();showNotice("ok","统计已刷新");render()}catch(e){showNotice("error",e.message)}}
async function loadAll(){try{await Promise.all([loadStatus(),loadSettings(),loadModels(),loadKeys(),loadStats()])}catch(e){showNotice("error",e.message)}render()}
$("nav").addEventListener("click",event=>{const btn=event.target.closest("[data-tab]");if(!btn)return;state.active=btn.dataset.tab;render()});
$("refresh").addEventListener("click",loadAll);
loadAll();
</script>
</body>
</html>
"""
