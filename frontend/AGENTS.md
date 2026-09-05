# AGENTS.md — `client/agentic-client/`

Guide for the React frontend of the `agentic` monorepo. Workspace overview and
cross-cutting contract: [`../../AGENTS.md`](../../AGENTS.md). Backend guide:
[`../../server/AGENTS.md`](../../server/AGENTS.md).

**React 19 + TypeScript + Vite** frontend (yarn) that talks to the backend over
the **ag-ui protocol** (SSE streaming) plus plain REST (knowledge base etc.).

## Layout

```
agentic-client/                  # this directory is its own git repo (client/ and the workspace root are not)
├── src/
│   ├── App.tsx              # createBrowserRouter + RouterProvider（AgenticRuntimeProvider 挂在路由外层；
│   │                        #   /login 公开，其余路由经 RequireAuth 守卫）+ sonner Toaster
│   ├── agentic-runtime.tsx  # HttpAgent（authenticatedFetch 包装注入 Bearer + SSE 401 登出跳转）
│   │                        #   + useAgUiRuntime（SSE 地址来自 @/lib/config）+ attachments 适配器
│   │                        #   （ServerImageAttachmentAdapter：图片附件 send 阶段上传后端，成功才放行提交）
│   ├── services/a2ui.ts     # A2UI 载荷防御性解析（parseA2uiPayload 纯函数）+
│   │                        #   a2ui-data.tsx（useAssistantDataUI 注册的官方渲染器接线，天气卡片）
│   ├── stores/              # auth-store.ts（首个 zustand store：token/user + localStorage 持久化，
│   │                        #   getToken() 供非 React 环境读票）+ tool-catalog-store.ts（工具目录
│   │                        #   缓存 name→中文标题，幂等拉取一次，useToolDisplay 的降级链一环）
│   │                        #   + agent-store.ts（智能体选择：目录缓存 + 选中项 localStorage 持久化，
│   │                        #   run 请求经 prepareRunAgentInput 覆写注入 forwardedProps.agentId）
│   ├── pages/               # 路由页面：login-page（登录/注册双 Tab，注册即登录）+
│   │                        #   chat-page（纯对话；右上角「管理」按钮与用户菜单进管理侧/退出）
│   │                        #   + profile-page（/profile 用户资料）
│   ├── pages/admin/         # 管理侧：admin-home-page（模块启动页）+ knowledge-list-page / knowledge-detail-page
│   │                        #   / knowledge-document-detail-page + memory-graph-page（React Flow 径向布局，时点回放/详情面板）
│   │                        #   + usage-stats-page（我的用量：recharts 每日柱状图 + 场景/模型分布 + 流水分页）
│   ├── components/assistant-ui/  # assistant-ui chat UI (thread, reasoning, markdown-text, tool-*, ...)
│   ├── components/ui/        # shadcn/ui 原语（base-nova 风格，Base UI 原语；table/input/select/dialog/... ）
│   ├── components/shared/    # 聊天/管理两侧共用组件：confirm-dialog、dialog-footer、password-input、
│   │                        #   use-dialog-submit、PDF 预览通道（provider/查看器/预览弹窗/溯源抽屉/渲染边界）
│   ├── components/knowledge/ # 知识库管理域组件：卡片列表、状态徽章、表单/上传弹窗、文档表格、分段列表/表单弹窗
│   ├── components/memory-graph/ # 记忆图谱域组件：layout.ts（快照→径向布局+索引，实体类型配色）、
│   │                        #   graph-canvas/header、nodes/node-types（实体卡/事件卡）、detail-panel、legend、
│   │                        #   edit/identity/maintenance 对话框 + use-memory-graph-actions
│   ├── hooks/               # use-conversation-list（聊天；初始拉取按登录态门控）+ use-knowledge-list/-base/-documents/-document/-segments
│   │                        #   （知识库，含 pending/processing/deleting 状态的条件轮询）+ use-memory-graph（快照）
│   │                        #   + use-usage-stats（用量统计：预置范围 + 汇总/序列/流水并行拉取）
│   ├── lib/                 # utils.ts (cn())、config.ts (REST_BASE/SSE_URL)、http.ts (axios 信封封装 +
│   │                        #   请求拦截器注 Bearer/401 登出跳转)、format.ts (文件大小/时间格式化)、
│   │                        #   admin-modules.ts（管理模块注册表）
│   └── services/            # REST 服务层：auth-service（register/login/me）、conversation-service、
│       │                    #   knowledge-service、memory-service（图快照契约是 camelCase 特例）、
│       │                    #   tool-catalog-service（GET /agentic/tool-catalog 展示元数据）、
│       │                    #   agent-service（GET /agentic/agents 智能体目录，agent-store/选择器消费）、
│       │                    #   attachment-service（POST /agentic/attachments 会话图片附件上传）、
│       │                    #   stats-service（GET /stats/usage/*，契约也是 camelCase 特例）
│       │                    #   + types.ts（后端 snake_case 镜像类型）+ translators/（后端历史→
│       │                    #   ThreadMessageLike 翻译器；user 消息 image part 还原为附件卡片）
├── .oxlintrc.json
└── package.json
```

## Commands

All commands run with CWD = `client/agentic-client/` (this directory).

- Install deps: `yarn`
- Dev: `yarn dev` (Vite)
- Build: `yarn build` (runs `tsc -b` then `vite build`)
- Lint: `yarn lint` (oxlint)
- Typecheck: `yarn typecheck` (`tsc -b`，即 build 的前半步单拆，供 CI 单独跑)
- Unit tests: `yarn test` (vitest run；最小纯逻辑集，见下「Tests」)
- CI: `.github/workflows/ci.yml`（node 24 —— react-router 8.3 的 engines
  要求 ≥22.22.0，本机 fnm 的 22.18 不满足；frozen-lockfile 安装 + lint +
  typecheck + test + build）
- Preview prod build: `yarn preview`

## Tests

Vitest 最小集（`vitest.config.ts` 独立配置，**不加载** react/tailwind 插件
——被测模块均为纯 .ts，与应用 vite 的插件链解耦；`@` alias 需与
vite.config.ts 保持一致）。`environment: "node"`，无 jsdom：zustand persist
在无 window 时静默跳过 hydration（仅有一条 storage 不可用告警，无碍断言）。
测试文件与源码同目录（`*.test.ts`），随 `tsc -b` 进入严格检查（显式
`import { describe, it, expect } from "vitest"`，type-only 一律 `import type`）。

覆盖三块纯逻辑：

- `src/services/translators/thread-message-translator.test.ts` —— 分支树构建
  （`toThreadBranchTree`：线性/末梢扇形/激活切换/活跃叶子缺失退化/失败占位）
  与附件解析（image part → attachments：data/相对/绝对 URL、mime 缺省、复合 id）；
- `src/services/run-input.test.ts` —— run 请求体注入（`applyRunInputInjections`
  纯函数：agentId 新会话门控、branchBaseMessageId 提升、runConfig 载体删除）；
- `src/stores/agent-store.test.ts` —— 注入决策的数据面（select /
  getSelectedAgentId / isKnownThread / setKnownThreads）。

需要 **node ^20.19 || ≥ 22.12**（Vite 8 的 engines 要求）。node 由 **fnm** 管理
（不是 nvm）：非交互 shell 里 `/usr/local/bin/node` 是系统 v16，fnm 的 `default`
别名又指向 v14，都不能用 —— 跑任何命令前先
`eval "$(fnm env)" && fnm use 22`（本机已装 v22.18.0；shell 状态不跨命令保留，
每条需要 node 的命令都要带上）。

## Architecture & boundaries

**Client ⇄ Server contract = ag-ui protocol（agentic run）+ REST（其余）.** The client
(`HttpAgent` from `@ag-ui/client`, wrapped by `useAgUiRuntime` from
`@assistant-ui/react-ag-ui`) POSTs a `RunRequest` to the backend's
`/agentic/run` endpoint and consumes an SSE stream of ag-ui events.
`useAgUiRuntime` maps ag-ui events onto assistant-ui's parts model
(`REASONING_*` → reasoning part, `TEXT_MESSAGE_*` → text part, `TOOL_CALL_*` →
tool-call part), so **do not** write an extra adapter layer on top of it. When
changing either side, keep the event sequence/format consistent with the server.

**REST 数据流**（知识库等管理功能）：`lib/http.ts`（axios 实例，请求拦截器注
Bearer；响应拦截器只在 `error_code !== 0` / HTTP 4xx 时抛 `BizError`——
**不在拦截器里拆信封**（axios 1.19 的 `AxiosInterceptorFulfilled` 要求拦截器
原样返回 `AxiosResponse`），拆包由 `getJson`/`postJson`/`patchJson`/
`deleteJson`/`postForm`/`getBinary` 各 helper 取 `response` 字段完成）→
`services/*-service.ts`（类型化方法，multipart 用 `postForm`）→
`hooks/use-*.ts`（状态 + 条件轮询 + toast）→ 组件。新增管理页面沿用这套分层。

**路由**：react-router（`createBrowserRouter`）。`AgenticRuntimeProvider` 在
路由外层，保证聊天 runtime 状态在页面切换间不丢。`/login` 公开（已登录访问
则重定向回 `/`），其余路由（聊天 + `/admin/*`）全部经 `RequireAuth` 守卫
（未登录跳 `/login`，回跳目标经 router **state**（`state.from`）传递）；
401 双通道处理（`lib/http.ts` / `agentic-runtime.tsx`）则用 `/login?next=...`
查询参数回跳守卫消费 `stores/auth-store.ts`
（首个 zustand store，localStorage 键 `agentic-auth` 持久化 token/user）。
聊天与管理侧分离：`/` 与 `/chat/:threadId` 都是聊天页 —— 会话身份经
`components/assistant-ui/
thread-route-sync.tsx` 在 URL 与 runtime 间双向同步（数据源是
use-conversation-list 的 `currentThreadId` 镜像，不是 `threads.mainThreadId`
—— external-store 下后者不随会话切换变化）；`/admin` 管理控制台首页是
「模块即 App」的启动页（无菜单栏，模块清单在 `lib/admin-modules.ts` 注册表，
新模块 = 注册表加一条 + 路由加一条）；`/admin/knowledge` 知识库列表、
`/admin/knowledge/:kbId` 详情；旧 `/knowledge*` 路径重定向到 `/admin/knowledge*` 兜底。

**认证数据流（token 双通道注入）**：登录/注册（`services/auth-service.ts`，
注册即登录——后端直接签发 token）写入 auth-store 后，① axios 单例
（`lib/http.ts`）的请求拦截器每次现读 `getToken()` 注入
`Authorization: Bearer`（REST 全覆盖）；② SSE 聊天端点不走 axios——
`agentic-runtime.tsx` 给 HttpAgent 传 `authenticatedFetch` 覆盖，请求时现读
token 注头。401 双通道同口径：清会话 + 跳 `/login?next=...`（`/auth/*` 自身
的 401 是业务错误，交给表单展示不跳转）；SSE 端点在 200 流式头之后不再走
全局异常处理器，401 只能在 fetch 层拦截。use-conversation-list 的初始拉取
按 `token` 门控——runtime provider 在路由外层，未登录挂载时不发必 401 的
列表请求。

## Conventions

- **TypeScript**: `tsconfig.app.json` is strict-ish — `noUnusedLocals`,
  `noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`,
  `moduleResolution: "bundler"`, `allowImportingTsExtensions`. Use `import type`
  for type-only imports. Import paths may use `.ts`/`.tsx` extensions. **Path
  alias `@/*` → `src/*`** (configured in both `tsconfig.app.json` and
  `vite.config.ts`) — prefer it for intra-`src` imports.
- **Linting**: **oxlint** (not ESLint), configured in `.oxlintrc.json`;
  `react/rules-of-hooks` is an error, `react/only-export-components` is a warn.
- **UI stack**: **assistant-ui** (`@assistant-ui/react` +
  `@assistant-ui/react-ag-ui` + `@assistant-ui/react-markdown`) on **Tailwind v4**
  (`@tailwindcss/vite`) + **shadcn/ui** primitives in `src/components/ui/`
  (`components.json` present, style `base-nova` → 底层是 **Base UI**（`@base-ui/react`），
  不是 Radix —— 组件 API（如 `render={...}`、`data-open`）按 Base UI 语义写；
  `package.json` 里的 `radix-ui` 直接依赖仅剩
  `components/assistant-ui/tooltip-icon-button.tsx` 的 `Slot` 一处在用（历史残留），
  不要在新代码里新增使用).
  State via **zustand**. Icons via **lucide-react**. Toast via **sonner**（`Toaster`
  挂在 `App.tsx`）. 文件上传拖拽用 **react-dropzone**.
  This replaced an earlier Ant Design X UI — do not reintroduce antd.
- **注释用中文**，与现有文件保持一致。

## Gotchas

- **智能体选择（新会话先选）**：无消息的新会话视图中，composer 上方渲染
  AgentModeSwitch 分段 pills（thread.tsx 用 `AuiIf isNewChatView` 包裹）——
  ≤3 个智能体直接切换，更多收进「更多」下拉（agent-selector.tsx，数据来自
  `GET /agentic/agents`，agent-store 幂等拉取 + localStorage 持久化选中项）。
  选中项只在**新会话首条 run** 时经 agentic-runtime 的 `prepareRunAgentInput`
  覆写注入 `forwardedProps.agentId`。「新会话」判定 = threadId 不在
  agent-store 的已知会话集合（knownThreads，由 use-conversation-list 每次
  会话列表加载/刷新回填，含各会话绑定的 agentic_id）——**不能**用 run 输入
  里的消息内容/条数判定：react-ag-ui 组装 run 输入时拿不到已加载的历史
  消息，旧会话续聊会被误判成新会话而改写绑定（已踩坑验证）。新会话
  threadId 是前端新生成的 UUID，必然不在列表中；首条 run 后会话落库、
  列表刷新才进入集合，此后继续聊/重生成一律不携带。新会话视图的附件
  加号按选中智能体的 `supportsVision` 显隐；既有会话绑定前端不感知，
  入口保守显示（服务端降级兜底）。
- **A2UI 通道（声明式 UI 渲染，天气卡片为第一块基石）** → 后端把 A2UI v0.9
  消息数组（Google 开放标准，a2ui.org）经 ag-ui `CUSTOM` 事件下发，react-ag-ui
  把 CUSTOM 聚合为 data part（`thread.tsx` 的 `case "data"` 分支），渲染由
  **name 注册制**解决：`chat-page.tsx` 挂载 `<A2uiDataUI />`（=
  `useAssistantDataUI({ name: "a2ui", render })`，与 KnowledgeSearchToolUI 的
  mount-and-register 同模式）。链路：`services/a2ui.ts` 的 `parseA2uiPayload`
  防御性校验（双形态：对象数组/JSON 字符串；非法整体返回 null）→
  `components/assistant-ui/a2ui-data.tsx` 把消息数组喂给官方渲染器
  （`@a2ui/react/v0_9` 的 `MessageProcessor` + `A2uiSurface` + `basicCatalog`，
  React 19 peer 版本 0.11.0）。载荷非法/处理失败降级为折叠 JSON，不渲染半棵
  组件树。交互回传：
  A2UI Button 的 `chat.send` 事件经 `MessageProcessor` 第二参 actionHandler
  接住（a2ui-data.tsx 的 `A2uiDataRender`），把 `context.text` 用
  `aui.thread.append` 作为用户新消息追加进会话（按钮即「替用户说一句话」，
  与后端 `packages/a2ui` 的 `CHAT_SEND_ACTION` 对齐；append 异步生效——发送
  即返回、消息稍后出现，勿以同步读 messages.length 判断成败；消息渲染树内
  的 scoped client 上 `aui.composer` 不可用，`thread.append` 可用）。历史回放：
  `thread-message-translator.ts` 把 CUSTOM 行还原为 data part，刷新后卡片照常
  渲染。新增卡片类型 = 后端组装新消息数组，前端零改动（渲染器按 name 注册，
  不按卡片类型分支）。
  **宿主主题（`a2ui.css` 的 `.a2ui-host` 作用域）**是卡片观感与聊天页同风格
  的关键：① 容器组件（Card/Row/Column/Divider）观感全走 `--a2ui-*` 内联
  变量，由 `.a2ui-host` 接管到应用主题令牌（暖砂渐变 + 噪点纸感、圆角、
  间距、图标色 `--a2ui-icon-color`）；`injectStyles()` 的 `.a2ui-surface`
  结构样式幂等注入，但组件自身不带该类名，须由 wrapper（`a2ui-data.tsx`）
  自包。② Text 的包内 CSS Module 类名在本项目打包链下**丢失**（DOM 只剩
  h1–h5/em/`.body` 标签），排版规则须按**标签选择器**重建（em 去斜体并弱化、
  caption 缩小、h2 主数值放大）。③ 结构样式的 `:where(*){all:revert}` 会把
  字体颜色打回浏览器默认，`.a2ui-host :where(*)` 声明回「继承宿主」。④
  Row `align="end"` 的内联 flex-end 在大小字号同行时阶梯错位，用属性选择器
  + `!important` 覆盖为 baseline。升级 `@a2ui/react` 须回归这些 DOM 假设。
  **Icon 约束**：`Icon` 的 `svgPath` 模式硬编码 `viewBox="0 0 24 24"`，
  960 坐标系的图标库（Material Symbols）须先做坐标变换（后端 weather_surface
  有现成变换产物与注释）。
- 「停止生成」走**双通道取消**（`agentic-runtime.tsx` 的 `onCancel`）：先
  `POST /agentic/run/cancel` 置服务端 Redis 取消标志（兜底代理吞断链事件、
  工具执行中不可打断的场景），再 `agent.abortRun()` 本地断链（即时取消态 +
  断链取消路径）。缺一不可：只留 abort 则断链事件可能被传输层吞掉；只留
  REST 则前端没有即时取消态。react-ag-ui 0.0.44 自身的 cancel 只 abort
  运行时内部 controller，signal 传不到 `HttpAgent` 的 fetch。
- 后端地址统一在 `src/lib/config.ts`（`REST_BASE`/`SSE_URL`，来自
  `import.meta.env.VITE_API_BASE`/`VITE_SSE_URL`，兜底 `http://127.0.0.1:8000`
  —— API 根）。REST 端点按领域挂顶级前缀：会话 `/agentic/conversation...`、
  run SSE `/agentic/run`、知识库 `/knowledge...`、绑定 `/agent/.../knowledge`、
  记忆图谱 `/memory/graph`（响应字段 camelCase 特例，类型见 memory-service.ts）；
  service 层写完整相对路径（相对 API 根），不再共享单一 `/agentic` 前缀。
- **shadcn CLI 路径坑**：`yarn shadcn add <comp>` 时 CLI 解析不到 `@` 别名
  （paths 只在 `tsconfig.app.json`），会把文件写到项目根目录的字面量 `@/`
  文件夹里 —— 用完检查并手动搬进 `src/components/ui/`。另外生成的
  `sonner.tsx` 默认 import `next-themes`（Next.js 专属），Vite 环境需手动
  去掉（本项目已改为 `theme="system"`）。
- 知识库/文档状态机：`pending/processing/deleting` 是过渡态，hooks 会在存在
  过渡态时每 3s 静默轮询；`failed` 的 `error_message` 通过状态徽章 tooltip
  展示。文档上传**仅支持 PDF**（后端解析流水线强校验后缀）。
- **聊天图片附件（多模态输入）**：上传语义收口在附件适配器的 `send()` 阶段
  （`agentic-runtime.tsx` 的 `ServerImageAttachmentAdapter`）——assistant-ui 在
  用户点发送时逐附件调 `send()`，此处 `attachmentService.upload` 成功才返回
  `CompleteAttachment`，失败抛错即中止本次提交（= 上传成功才能发消息）。
  适配器 send 时把后端返回的稳定相对 url 拼成「REST_BASE + 相对路径」的
  绝对 URL 上送（浏览器 `<img>` 渲染需要绝对地址，Vite 无 dev 代理），
  消息/历史里存的就是这个绝对引用（永不过期）。
  **展示必须先换预签名地址**：`<img>`/新标签页带不上 Authorization 头，
  稳定引用直渲必 401（rustfs 的 302 预签名响应无 CORS 头，fetch 跟随
  也读不到 blob）——`attachment.tsx` 的 `useAttachmentDisplaySrc` 经
  `attachmentService.getDisplayUrl`（`GET /agentic/attachments/url`）
  换签名地址再渲染，模块级缓存 + 半程 TTL 重签；后端返回 `url=null`
  （本地磁盘后端）降级为鉴权 fetch 转 blob。历史重载的还原在
  `translators/thread-message-translator.ts` 的 `toUserThreadMessage`（image
  part → attachments 卡片）：绝对 URL 直接用，裸相对引用拼 `${REST_BASE}`，
  最终都走同一换签 hook。后端按 path 前缀识别本域引用、服务端读对象存储
  转 base64 喂模型（外部 URL 才透传）。附件 UI（加号/拖拽/预览）是
  assistant-ui 模板自带的，注册适配器即激活。composer 里的语音按钮
  （Dictate）仍无适配器，点了无效。
