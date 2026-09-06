
import { useMemo } from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import {
  AssistantRuntimeProvider,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
} from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent } from "@ag-ui/client";

import {
  ConversationActionsContext,
  useConversationList,
} from "@/hooks/use-conversation-list";
import { REST_BASE, SSE_URL } from "@/lib/config";
import { refreshSession } from "@/lib/token-refresh";
import { attachmentService } from "@/services/attachment-service";
import { conversationService } from "@/services/conversation-service";
import { applyRunInputInjections, type RunAgentInputLike } from "@/services/run-input";
import { getToken, useAuthStore } from "@/stores/auth-store";
import { getSelectedAgentId, isKnownThread } from "@/stores/agent-store";

/**
 * 把流链路上的各类异常翻译成用户可读的一句话：
 * - 非长连接网络故障（发请求就失败 / 流中途断链）：`Failed to fetch` 等浏览器
 *   原始文案 → 网络中断提示；
 * - HTTP 非 2xx：HttpAgent 自拼 `HTTP 500: {原始响应体}` —— 我们在
 *   authenticatedFetch 里已拦截并抛干净错误，这里兜底按状态码归类；
 * - 服务端 RUN_ERROR 事件：后端给的 message 本身可读，原样透出。
 */
const friendlyStreamError = (error: unknown): string => {
  const message = error instanceof Error ? error.message : String(error);
  if (/failed to fetch|networkerror|load failed|network/i.test(message)) {
    return "网络连接中断，请检查网络后重试";
  }
  const statusMatch = /^HTTP (\d{3})/.exec(message);
  if (statusMatch) {
    const status = Number(statusMatch[1]);
    if (status === 401) return "登录已过期，请重新登录";
    if (status === 403) return "没有权限执行该操作";
    if (status === 429) return "请求太频繁了，请稍后再试";
    if (status >= 500) return "服务器开小差了，请稍后重试";
  }
  return message || "对话流异常中断，请重试";
};

/**
 * SSE 流式请求的认证与错误包装：每次发请求实时读 token（agent 实例 useMemo
 * 一次创建，闭包捕获会陈旧，必须请求时现读）。三类拦截：
 * - 401（令牌缺失/过期）：先用 refresh token 单飞静默换新，成功即换新
 *   token 重试一次再建流；刷新失败才清会话并跳登录 —— SSE 端点在 200 流式
 *   头之后不走全局异常处理器，只能在 fetch 层拦截，与 lib/http.ts 的 REST
 *   拦截器口径一致（刷新通道共用 lib/token-refresh.ts）；
 * - 其余非 2xx：解析响应信封取 error_message（业务错误），5xx/解析失败给
 *   归类提示，抛出干净 Error —— 避免 HttpAgent 把 `HTTP 500: {原始JSON}`
 *   整段怼进聊天气泡。
 */
const authenticatedFetch: typeof fetch = async (input, init) => {
  const buildHeaders = (token: string | null) => {
    const headers = new Headers(init?.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return headers;
  };
  let response: Response;
  try {
    response = await fetch(input, { ...init, headers: buildHeaders(getToken()) });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new Error("网络连接中断，请检查网络后重试");
  }
  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      // 用换新的 token 重试一次；再 401 只能登出
      response = await fetch(input, {
        ...init,
        headers: buildHeaders(getToken()),
      });
    }
    if (response.status === 401) {
      useAuthStore.getState().logout();
      if (
        typeof window !== "undefined" &&
        !window.location.pathname.startsWith("/login")
      ) {
        window.location.assign(
          `/login?next=${encodeURIComponent(window.location.pathname)}`,
        );
      }
      return response;
    }
  }
  if (!response.ok) {
    let detail: string | null = null;
    try {
      const text = await response.text();
      const body = JSON.parse(text) as { error_message?: unknown };
      detail =
        typeof body?.error_message === "string" && body.error_message
          ? body.error_message
          : null;
    } catch {
      // 非 JSON 响应体（如网关 HTML 错误页），走状态码兜底
    }
    if (response.status >= 500) {
      throw new Error(detail ?? "服务器开小差了，请稍后重试");
    }
    throw new Error(detail ?? `请求失败（HTTP ${response.status}）`);
  }
  return response;
};

/**
 * 图片附件适配器：上传语义收口在 send 阶段（composer-send）。
 *
 * 流程：选中文件 → add() 进 composer 待发区（本地预览，File 对象直读）→
 * 用户点发送 → assistant-ui 逐附件调 send() → 此处上传后端，成功才返回
 * CompleteAttachment（含稳定引用 URL）——失败抛错会中止本次提交并把附件
 * 标记为错误，即「附件上传成功后才能提交消息」。
 *
 * 引用 URL 为稳定相对路径拼 API 根；消息发出后 react-ag-ui 自动转成
 * ag-ui 的 image url source（绝对 http URL → url source），后端落库该
 * 引用并在渲染时 302 重定向到预签名地址（浏览器直拉对象存储）。
 */
class ServerImageAttachmentAdapter implements AttachmentAdapter {
  accept = "image/png,image/jpeg,image/webp,image/gif";

  async add({ file }: { file: File }): Promise<PendingAttachment> {
    return {
      id: crypto.randomUUID(),
      type: "image",
      name: file.name,
      contentType: file.type || "image/png",
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    const uploaded = await attachmentService.upload(attachment.file);
    return {
      ...attachment,
      id: uploaded.id,
      status: { type: "complete" },
      content: [
        { type: "image", image: `${REST_BASE}${uploaded.url}` },
      ],
    };
  }

  async remove(): Promise<void> {
    // 无需清理：对象删除不做（会话附件孤儿清理是后续项），本地预览
    // 的 File 由 runtime 自行释放
  }
}

/**
 * 分支展示策略（历史 vs 会话中）：
 *   - 历史查看：不种分支树，按服务端激活叶子线性展示，无 1/2 切换入口；
 *   - 会话中：重新生成/编辑产生的兄弟变体由 runtime 仓库原生维护，
 *     BranchPicker（thread.tsx，末梢轮次门控）实时可对比/切换。
 * 曾经尝试在历史加载后用 aui.thread.import 种入末梢扇形，但 react-ag-ui
 * 的 store 同步只认线性数组，会在加载后把扇形抹平（实测），故移除该链路。
 */

export const AgenticRuntimeProvider = ({
  children,
}: {
  children: ReactNode;
}) => {
  // HttpAgent 指向后端 ag-ui SSE 端点（URL 来自 @/lib/config，与 REST 同源配置）。
  // requestInit 会把整个 RunAgentInput（含 threadId/runId/messages）作为 POST body 发出。
  // 注意：runtime 每次发消息都从 agent.threadId 取值，但库不会在切换会话时更新它，
  // 同步逻辑在 useConversationList 的 onSwitchToThread/onSwitchToNewThread 里。
  const agent = useMemo(
    () => new HttpAgent({ url: SSE_URL, fetch: authenticatedFetch }),
    [],
  );

  // 请求体加工（prepareRunAgentInput 覆写，@ag-ui/client 里是 protected，按鸭子
  // 类型赋值）——两项注入的完整语义见 @/services/run-input 模块注释：
  // 1) 分支信号提升：forwardedProps.runConfig.branchBaseMessageId →
  //    forwardedProps.branch（runConfig 载体删除），不受新会话门控；
  // 2) 智能体选择：仅「新会话」（threadId 不在 agent-store 已知会话集合——
  //    不能用消息内容/条数判定，旧会话续聊会被误判而改写绑定）注入
  //    forwardedProps.agentId。
  // 请求时现读 store（与 token 同口径，避免闭包陈旧）。
  useMemo(() => {
    const target = agent as unknown as {
      prepareRunAgentInput: (params?: unknown) => RunAgentInputLike;
    };
    const original = target.prepareRunAgentInput.bind(agent);
    target.prepareRunAgentInput = (params?: unknown) => {
      const input = original(params);
      return applyRunInputInjections(input, {
        selectedAgentId: getSelectedAgentId(),
        knownThread: isKnownThread(input.threadId),
      });
    };
  }, [agent]);

  // 会话列表的加载 / 切换 / 历史回放都封装在 hook 里（agent 传入用于
  // 订阅 RunFinished：轮次结束后轮询标题并刷新列表）。
  // 返回的 adapter 是 threadList adapter，引用随列表/回调变化 → 驱动 runtime 刷新。
  // 详见 src/hooks/use-conversation-list.ts。
  const {
    adapter: threadListAdapter,
    deleteConversation,
    hasMore,
    isLoadingMore,
    loadMoreConversations,
    currentThreadId,
    error: listError,
    refreshConversations,
    updatedMessageIds,
    turnByMessageId,
  } = useConversationList(agent);

  const runtime = useAgUiRuntime({
    agent,
    adapters: {
      threadList: threadListAdapter,
      // 图片附件适配器：thread.tsx 的附件 UI（加号/拖拽区/预览）已就绪，
      // 注册即激活
      attachments: new ServerImageAttachmentAdapter(),
    },
    // 流异常的友好输出：runtime 把本轮错误同时写进助手消息的错误框（聊天气泡
    // 内联展示 friendlyStreamError 归类后的一句话）并回调到这里；toast 让
    // 错误离开消息流仍可见。401 时页面即将跳登录，不弹。
    onError: (error) => {
      const message = friendlyStreamError(error);
      if (/^HTTP 401\b/.test(error.message)) return;
      toast.error(message);
    },
    // 「停止生成」双通道取消：
    // 1) REST 置服务端取消标志——断链事件可能被代理/缓冲吞掉，且工具执行中
    //    的生成器无法被断链打断，标志由流式循环在节流边界与工具入口感知收口；
    // 2) 本地 abort——即时取消态 + 断链取消路径（服务器在下一个 yield 边界
    //    收到 GeneratorExit）。AbortError 会被 runtime 归类为 RUN_CANCELLED，
    //    不会误报成运行错误。REST 失败不阻断本地取消，静默即可。
    onCancel: () => {
      void conversationService.cancelRun(agent.threadId).catch(() => {});
      agent.abortRun();
    },
  });

  // 删除、加载更多等会话操作与分页状态经 context 下发给侧栏等
  // runtime 之外的组件（确认弹窗 / 加载更多按钮入口）；currentThreadId
  // 供 thread-route-sync 做 URL ↔ runtime 双向绑定
  const actions = useMemo(
    () => ({
      deleteConversation,
      loadMoreConversations,
      hasMore,
      isLoadingMore,
      currentThreadId,
      listError,
      refreshConversations,
      updatedMessageIds,
      turnByMessageId,
    }),
    [deleteConversation, loadMoreConversations, hasMore, isLoadingMore, currentThreadId, listError, refreshConversations, updatedMessageIds, turnByMessageId],
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ConversationActionsContext.Provider value={actions}>
        {children}
      </ConversationActionsContext.Provider>
    </AssistantRuntimeProvider>
  );
};
