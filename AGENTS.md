# AGENTS.md

Workspace guide for ZCode agents. `agentic` is a monorepo with two parts that
talk over the **ag-ui protocol** (SSE streaming): a Python backend (`backend/`)
and a React frontend (`frontend/`).

## Scoped guides — read them first

This file is only the workspace overview and the cross-cutting contract. Each
sub-project has its own guide with the full layout, commands, architecture
rules, conventions, and gotchas. Those files are **not loaded automatically**
into the agent's context — before touching anything under a sub-project, read
its guide:

- Working under `backend/` → read [`backend/AGENTS.md`](backend/AGENTS.md) first.
- Working under `frontend/` → read
  [`frontend/AGENTS.md`](frontend/AGENTS.md) first.
- Changing deployment artifacts / port & env conventions → read
  [`deploy/deployment-spec.md`](deploy/deployment-spec.md) first.

## Layout

```
agentic/                       # git monorepo root（单一仓库，无子模块）
├── backend/                   # Python 3.12 backend（v2 六边形布局）— details in backend/AGENTS.md
│   ├── app/                   #   FastAPI + Celery + LangChain/LangGraph + ag-ui
│   ├── tests/ + tests_e2e/    #   单元套件 + 针对运行中服务的端到端套件
│   ├── docker-compose.yaml    #   middleware stack (postgres/weaviate/rustfs/redis)
│   └── pyproject.toml         #   uv-managed deps
├── frontend/                  # React 19 + TS + Vite frontend — details in     
│   ├── src/                   #   frontend/AGENTS.md
│   │                          #   (assistant-ui chat UI, shadcn/ui primitives)
│   └── package.json           #   yarn
└── deploy/                    # 部署视图：规范 + 文档 + 全栈一键拉起编排
    ├── deployment-spec.md     #   部署规范（镜像/端口/变量/数据卷/安全/升级）
    ├── README.md              #   一键拉起指南
    └── docker-compose.yaml    #   全栈编排（include backend 的镜像；与 dev 栈不可同跑）
```

> The whole tree is **one git repo**: a single `.git` at the `agentic/` root.
> `backend/` and `frontend/` are plain subdirectories (no submodules, no nested
> repos) — run git commands from the root. Each part's full history was
> subtree-merged in (original hashes preserved). Subtree-merge caveat:
> `git log -- <subdir>/` stops at the import merge — browse a side's
> pre-import history from that merge's second parent
> (`git log --oneline <import-merge>^2 -- <path>`); `git blame` crosses the
> boundary fine.

## Agent shell environment — fix PATH before running anything

The agent's shell is **non-interactive**: `~/.zshrc` (where this machine's
toolchain PATH setup lives) is not sourced, so out of the box `uv` is missing
and `node` is stale. Apply these before any command below (shell state does not
persist between Bash calls — redo it in every command that needs it):

- **uv** is installed at `~/.local/bin/uv`, which is not on PATH. Run
  `export PATH="$HOME/.local/bin:$PATH"` first, or invoke it by absolute path.
- **node/yarn are managed by fnm** (not nvm) — nothing version-managed is on
  the default PATH. Do **not** use `/usr/local/bin/node` (system v16) or bare
  `fnm env` (its `default` alias points at v14) — both are too old for Vite 8.
  Run `eval "$(fnm env)" && fnm use 22` first (`fnm` itself is at
  `/usr/local/bin/fnm`; v22.18.0 is installed).

## Commands at a glance

Full details (entrypoint flags, Celery broker config, middleware
ports/credentials) live in the scoped guides.

| What | CWD | Command |
| --- | --- | --- |
| Install backend deps | `backend/` | `uv sync` |
| Run backend dev server | `backend/` | `uv run uvicorn app.cmd.http.main:server --reload` |
| Run Celery worker | `backend/` | `uv run celery -A app.adapters.tasking.celery_app worker` |
| Run backend unit tests | `backend/` | `uv run pytest tests/` |
| Run backend e2e tests | `backend/` | 起服务后 `E2E_BASE_URL=http://127.0.0.1:8000 uv run pytest tests_e2e/` |
| Start middleware stack | `backend/` | `docker compose up -d` |
| Start full stack (one-click, see deploy/README.md) | `deploy/` | `docker compose up -d --build` |
| Install frontend deps | `frontend/` | `yarn` |
| Run frontend dev server | `frontend/` | `yarn dev` |
| Run frontend unit tests | `frontend/` | `yarn test` |

Backend tests live in `backend/tests/` (pure unit level over temp SQLite, no
middleware needed; `test_http_integration.py` additionally walks the real
`create_app()` app via TestClient with the LLM/finalizer swapped for fakes in
the DI container) plus `backend/tests_e2e/` (against a **running** server +
middleware + real LLM; see `backend/AGENTS.md`). The frontend has a minimal
Vitest setup (`yarn test`,
config in `vitest.config.ts`, node env — no jsdom): pure-logic tests for the
thread-message translator (branch tree + attachment parsing), run-input
injection, and the agent store.

## Client ⇄ Server contract = ag-ui protocol

The client (`HttpAgent` from `@ag-ui/client`, wrapped by `useAgUiRuntime` from
`@assistant-ui/react-ag-ui`) POSTs a `RunRequest` to `/agentic/run` and
consumes an SSE stream of ag-ui events. `useAgUiRuntime` maps ag-ui events onto
assistant-ui's parts model (`REASONING_*` → reasoning part, `TEXT_MESSAGE_*` →
text part, `TOOL_CALL_*` → tool-call part), so **do not** write an extra
adapter layer on the client. When changing either side, keep the event
sequence/format consistent.

**A2UI**: declarative UI (Google's A2UI v0.9 spec, e.g. the demo agent's
weather card) rides the same stream as a `CUSTOM` event named `a2ui`, which
react-ag-ui surfaces as a data part rendered by the registered A2UI renderer.
The transport/persistence contract lives in the scoped guides (backend
「A2UI 通道」gotcha / frontend「A2UI 通道」gotcha).

**Auth**: the backend requires JWT Bearer auth (register/login at `/auth/*`)
for run, conversations, and memory; the client injects the token via an axios
request interceptor (REST) and a custom `fetch` on the HttpAgent (SSE). See the
scoped guides for details.
