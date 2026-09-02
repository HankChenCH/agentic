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
│   ├── stores/              # auth-store.ts（首个 zustand store：token/user + localStorage 持久化，
│   │                        #   getToken() 供非 React 环境读票）+ tool-catalog-store.ts（工具目录
│   │                        #   缓存 name→中文标题，幂等拉取一次，useToolDisplay 的降级链一环）
│   ├── pages/               # 路由页面：login-page（登录/注册双 Tab，注册即登录）+
│   │                        #   chat-page（纯对话；右上角「管理」按钮与用户菜单进管理侧/退出）
│   ├── pages/admin/         # 管理侧：admin-home-page（模块启动页）+ knowledge-list-page / knowledge-detail-page
│   │                        #   + memory-graph-page（记忆图谱：React Flow 径向布局，时点回放/详情面板）
│   ├── components/assistant-ui/  # assistant-ui chat UI (thread, reasoning, markdown-text, tool-*, ...)
│   ├── components/ui/        # shadcn/ui 原语（base-nova 风格，Base UI 原语；table/input/select/dialog/... ）
│   ├── components/shared/    # 聊天/管理两侧共用组件：confirm-dialog、PDF 预览通道（provider/查看器/预览弹窗/溯源抽屉）
│   ├── components/knowledge/ # 知识库管理域组件：卡片列表、状态徽章、表单/上传弹窗、文档表格
│   ├── components/memory-graph/ # 记忆图谱域组件：layout.ts（快照→径向布局+索引，实体类型配色）、
│   │                        #   nodes/node-types（实体卡/事件卡）、detail-panel、legend
│   ├── hooks/               # use-conversation-list（聊天；初始拉取按登录态门控）+ use-knowledge-list/-base/-documents
│   │                        #   （知识库，含 pending/processing/deleting 状态的条件轮询）+ use-memory-graph（快照）
│   ├── lib/                 # utils.ts (cn())、config.ts (REST_BASE/SSE_URL)、http.ts (axios 信封封装 +
│   │                        #   请求拦截器注 Bearer/401 登出跳转)、format.ts (文件大小/时间格式化)、
│   │                        #   admin-modules.ts（管理模块注册表）
│   └── services/            # REST 服务层：auth-service（register/login/me）、conversation-service、
│       │                    #   knowledge-service、memory-service（图快照契约是 camelCase 特例）、
│       │                    #   tool-catalog-service（GET /agentic/tool-catalog 展示元数据）、
│       │                    #   attachment-service（POST /agentic/attachments 会话图片附件上传）
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
- CI: `.github/workflows/ci.yml`（node 24 —— react-router 8.3 的 engines
  要求 ≥22.22.0，本机 fnm 的 22.18 不满足；frozen-lockfile 安装 + lint +
  typecheck + build）
- Preview prod build: `yarn preview`

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
  `package.json` 里遗留一个无 import 使用的 `radix-ui` 直接依赖，属历史残留，
  不要在新代码里用).
  State via **zustand**. Icons via **lucide-react**. Toast via **sonner**（`Toaster`
  挂在 `App.tsx`）. 文件上传拖拽用 **react-dropzone**.
  This replaced an earlier Ant Design X UI — do not reintroduce antd.
- **注释用中文**，与现有文件保持一致。

## Gotchas

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
  消息/历史里引用的是后端稳定相对 url（永不过期），`<img>` 直接指向
  `${REST_BASE}${url}`（后端鉴权后 302 到预签名地址，浏览器直拉对象存储，
  无需 Bearer 头也无需 blob 中转）；历史重载的还原在
  `translators/thread-message-translator.ts` 的 `toUserThreadMessage`（image
  part → attachments 卡片）。附件 UI（加号/拖拽/预览）是 assistant-ui 模板
  自带的，注册适配器即激活。composer 里的语音按钮（Dictate）仍无适配器，
  点了无效。
