# agentic-client

`agentic` 的前端:**React 19 + TypeScript + Vite**。聊天走 ag-ui 协议
(SSE 流),知识库等管理功能走普通 REST,后端为同仓的 `server/`。

## 功能一览

- **流式聊天**:基于 assistant-ui 的会话界面,实时渲染文本增量、推理过程
  (reasoning)与工具调用(含 `knowledge_search` 检索结果的专门展示)。
- **会话管理**:会话列表、切换与新建,历史消息由后端权威存储。
- **知识库管理**:卡片式列表、创建 / 编辑 / 删除 / 启停;PDF 拖拽上传、
  文档表格与状态轮询(过渡态每 3s 静默刷新)、失败原因 tooltip、内嵌 PDF 预览。

## 技术栈

React 19 · TypeScript · Vite 8 · [assistant-ui](https://www.assistant-ui.com/)
(`@assistant-ui/react` + `react-ag-ui` + `react-markdown`)· Tailwind CSS v4 ·
shadcn/ui(base-nova 风格,底层是 Base UI)· zustand · axios · react-router ·
sonner(toast)· lucide-react · react-dropzone · react-pdf · oxlint

## 环境要求

- Node `^20.19 || >= 22.12`(Vite 8 的 engines 要求)
- yarn

## 命令

| 事项 | 命令(CWD = `client/agentic-client/`) |
| --- | --- |
| 安装依赖 | `yarn` |
| 本地开发 | `yarn dev`(Vite) |
| 构建 | `yarn build`(先 `tsc -b` 再 `vite build`) |
| Lint | `yarn lint`(oxlint,不是 ESLint) |
| 预览生产构建 | `yarn preview` |

## 后端地址配置

后端地址集中收敛在 `src/lib/config.ts`,经 Vite 环境变量注入(`.env` /
`.env.local`):

```bash
VITE_API_BASE=http://127.0.0.1:8000              # REST 基址(API 根)
VITE_SSE_URL=http://127.0.0.1:8000/agentic/run   # 可选;默认由 VITE_API_BASE 派生
```

不配置时兜底 `http://127.0.0.1:8000`,与后端 dev server 默认端口对齐。

## 与后端的契约

- **Agentic run(ag-ui)**:`HttpAgent`(`@ag-ui/client`)经 `useAgUiRuntime`
  (`@assistant-ui/react-ag-ui`)POST `RunRequest` 到 `/agentic/run`,消费
  ag-ui 事件的 SSE 流;事件到 UI parts 的映射由 `useAgUiRuntime` 完成
  (`REASONING_*` → reasoning、`TEXT_MESSAGE_*` → 文本、`TOOL_CALL_*` → 工具),
  不要在其上再写一层适配器。
- **REST(其余功能)**:`lib/http.ts` 的 axios 实例统一拆
  `{error_code, error_message, response}` 信封,HTTP 4xx 的业务错误同样转
  `BizError`;`services/*-service.ts` 提供类型化方法(multipart 上传用
  `postForm`)。端点按领域挂顶级前缀:会话 `/agentic/conversation...`、
  知识库 `/knowledge...`、绑定 `/agent/.../knowledge`。

改任一侧时保持事件序列 / 请求格式与后端一致。

## 页面与路由

react-router(`createBrowserRouter`),`AgenticRuntimeProvider` 挂在路由外层,
聊天 runtime 状态在页面切换间不丢。

| 路由 | 页面 |
| --- | --- |
| `/` | 聊天(含侧栏导航) |
| `/knowledge` | 知识库卡片列表 |
| `/knowledge/:kbId` | 知识库详情(文档管理) |

## 目录结构

```
agentic-client/
├── src/
│   ├── agentic-runtime.tsx           # HttpAgent + useAgUiRuntime(SSE 接入)
│   ├── App.tsx                       # 路由 + Toaster
│   ├── pages/                        # chat / knowledge-list / knowledge-detail
│   ├── components/
│   │   ├── assistant-ui/             # 聊天 UI(thread、reasoning、markdown、tool-* 等)
│   │   ├── knowledge/                # 知识库业务组件(卡片、表格、上传、PDF 预览)
│   │   └── ui/                       # shadcn/ui 原语(Base UI)
│   ├── hooks/                        # use-conversation-list、use-knowledge-*(条件轮询)
│   ├── lib/                          # config(后端地址)、http(信封封装)、format、utils
│   └── services/                     # REST 服务层 + 后端 snake_case 镜像类型
├── index.html
└── vite.config.ts                    # @/* 别名 → src/*,Tailwind v4 插件
```

## 注意事项

- 文档上传**仅支持 PDF**(后端解析流水线强校验后缀)。
- `yarn shadcn add <comp>` 生成的文件可能落到项目根目录的字面量 `@/` 文件夹
  (CLI 解析不到别名),用完需手动搬进 `src/components/ui/`;生成的 `sonner.tsx`
  需去掉 `next-themes` 依赖(Vite 环境不适用)。
- 代码注释用中文;路径别名 `@/*` → `src/*`;lint 用 oxlint
  (`react/rules-of-hooks` error、`react/only-export-components` warn)。

更完整的开发约定与坑位说明见 [`AGENTS.md`](AGENTS.md)。
