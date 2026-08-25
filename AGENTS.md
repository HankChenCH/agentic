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
│   ├── pages/               # 路由页面：chat-page（聊天，含侧栏导航）、knowledge-list-page（知识库卡片列表）、
│   │                        #   knowledge-detail-page（/knowledge/:kbId 文档管理）
│   ├── components/assistant-ui/  # assistant-ui chat UI (thread, reasoning, markdown-text, tool-*, ...)
│   ├── components/ui/        # shadcn/ui 原语（base-nova 风格，Base UI 原语；table/input/select/dialog/... ）
│   ├── components/knowledge/ # 知识库业务组件：卡片列表、状态徽章、表单/上传/确认弹窗、文档表格
│   ├── hooks/               # use-conversation-list（聊天）+ use-knowledge-list/-base/-documents（知识库，
│   │                        #   含 pending/processing/deleting 状态的条件轮询）
│   ├── lib/                 # utils.ts (cn())、config.ts (REST_BASE/SSE_URL)、http.ts (axios 信封封装)、
│   │                        #   format.ts (文件大小/时间格式化)
│   └── services/            # REST 服务层：conversation-service、knowledge-service + types.ts（后端 snake_case 镜像类型）
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
路由外层，保证聊天 runtime 状态在页面切换间不丢。路由：`/` 聊天、
`/knowledge` 知识库列表、`/knowledge/:kbId` 详情。

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
  聊天 SSE `/agentic/chat`、知识库 `/knowledge...`、绑定 `/agent/.../knowledge`；
  service 层写完整相对路径（相对 API 根），不再共享单一 `/agentic` 前缀。
- **shadcn CLI 路径坑**：`yarn shadcn add <comp>` 时 CLI 解析不到 `@` 别名
  （paths 只在 `tsconfig.app.json`），会把文件写到项目根目录的字面量 `@/`
  文件夹里 —— 用完检查并手动搬进 `src/components/ui/`。另外生成的
  `sonner.tsx` 默认 import `next-themes`（Next.js 专属），Vite 环境需手动
  去掉（本项目已改为 `theme="system"`）。
- 知识库/文档状态机：`pending/processing/deleting` 是过渡态，hooks 会在存在
  过渡态时每 3s 静默轮询；`failed` 的 `error_message` 通过状态徽章 tooltip
  展示。文档上传**仅支持 PDF**（后端解析流水线强校验后缀）。
