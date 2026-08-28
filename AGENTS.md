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
│   ├── App.tsx              # createBrowserRouter + RouterProvider（AgenticRuntimeProvider 挂在路由外层）+ sonner Toaster
│   ├── agentic-runtime.tsx  # HttpAgent + useAgUiRuntime（SSE 地址来自 @/lib/config）
│   ├── pages/               # 路由页面：chat-page（纯对话；右上角「管理」按钮进管理侧）
│   ├── pages/admin/         # 管理侧：admin-home-page（模块启动页）+ knowledge-list-page / knowledge-detail-page
│   │                        #   + memory-graph-page（记忆图谱：React Flow 径向布局，时点回放/详情面板）
│   ├── components/assistant-ui/  # assistant-ui chat UI (thread, reasoning, markdown-text, tool-*, ...)
│   ├── components/ui/        # shadcn/ui 原语（base-nova 风格，Base UI 原语；table/input/select/dialog/... ）
│   ├── components/shared/    # 聊天/管理两侧共用组件：confirm-dialog、PDF 预览通道（provider/查看器/预览弹窗/溯源抽屉）
│   ├── components/knowledge/ # 知识库管理域组件：卡片列表、状态徽章、表单/上传弹窗、文档表格
│   ├── components/memory-graph/ # 记忆图谱域组件：layout.ts（快照→径向布局+索引，实体类型配色）、
│   │                        #   nodes/node-types（实体卡/事件卡）、detail-panel、legend
│   ├── hooks/               # use-conversation-list（聊天）+ use-knowledge-list/-base/-documents（知识库，
│   │                        #   含 pending/processing/deleting 状态的条件轮询）+ use-memory-graph（快照）
│   ├── lib/                 # utils.ts (cn())、config.ts (REST_BASE/SSE_URL)、http.ts (axios 信封封装)、
│   │                        #   format.ts (文件大小/时间格式化)、admin-modules.ts（管理模块注册表）
│   └── services/            # REST 服务层：conversation-service、knowledge-service、memory-service
│   │                        #   （图快照契约是 camelCase 特例）+ types.ts（后端 snake_case 镜像类型）
├── .oxlintrc.json
└── package.json
```

## Commands

All commands run with CWD = `client/agentic-client/` (this directory).

- Install deps: `yarn`
- Dev: `yarn dev` (Vite)
- Build: `yarn build` (runs `tsc -b` then `vite build`)
- Lint: `yarn lint` (oxlint)
- Preview prod build: `yarn preview`

需要 **node ^20.19 || ≥ 22.12**（Vite 8 的 engines 要求）。node 由 **fnm** 管理
（不是 nvm）：非交互 shell 里 `/usr/local/bin/node` 是系统 v16，fnm 的 `default`
别名又指向 v14，都不能用 —— 跑任何命令前先
`eval "$(fnm env)" && fnm use 22`（本机已装 v22.18.0；shell 状态不跨命令保留，
每条需要 node 的命令都要带上）。

## Architecture & boundaries

**Client ⇄ Server contract = ag-ui protocol（聊天）+ REST（其余）.** The client
(`HttpAgent` from `@ag-ui/client`, wrapped by `useAgUiRuntime` from
`@assistant-ui/react-ag-ui`) POSTs a `ChatRequest` to the backend's
`/agentic/chat` endpoint and consumes an SSE stream of ag-ui events.
`useAgUiRuntime` maps ag-ui events onto assistant-ui's parts model
(`REASONING_*` → reasoning part, `TEXT_MESSAGE_*` → text part, `TOOL_CALL_*` →
tool-call part), so **do not** write an extra adapter layer on top of it. When
changing either side, keep the event sequence/format consistent with the server.

**REST 数据流**（知识库等管理功能）：`lib/http.ts`（axios 实例，拦截器把
`{error_code, error_message, response}` 信封拆包，HTTP 4xx 的业务错误同样转
`BizError`）→ `services/*-service.ts`（类型化方法，multipart 用 `postForm`）→
`hooks/use-*.ts`（状态 + 条件轮询 + toast）→ 组件。新增管理页面沿用这套分层。

**路由**：react-router（`createBrowserRouter`）。`AgenticRuntimeProvider` 在
路由外层，保证聊天 runtime 状态在页面切换间不丢。聊天与管理侧分离：`/`
纯对话（右上角「管理」按钮进入管理侧）；`/admin` 管理控制台首页是
「模块即 App」的启动页（无菜单栏，模块清单在 `lib/admin-modules.ts` 注册表，
新模块 = 注册表加一条 + 路由加一条）；`/admin/knowledge` 知识库列表、
`/admin/knowledge/:kbId` 详情；旧 `/knowledge*` 路径重定向到 `/admin/knowledge*` 兜底。

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
  不是 Radix —— 组件 API（如 `render={...}`、`data-open`）按 Base UI 语义写).
  State via **zustand**. Icons via **lucide-react**. Toast via **sonner**（`Toaster`
  挂在 `App.tsx`）. 文件上传拖拽用 **react-dropzone**.
  This replaced an earlier Ant Design X UI — do not reintroduce antd.
- **注释用中文**，与现有文件保持一致。

## Gotchas

- 后端地址统一在 `src/lib/config.ts`（`REST_BASE`/`SSE_URL`，来自
  `import.meta.env.VITE_API_BASE`/`VITE_SSE_URL`，兜底 `http://127.0.0.1:8000`
  —— API 根）。REST 端点按领域挂顶级前缀：会话 `/agentic/conversation...`、
  聊天 SSE `/agentic/chat`、知识库 `/knowledge...`、绑定 `/agent/.../knowledge`、
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
