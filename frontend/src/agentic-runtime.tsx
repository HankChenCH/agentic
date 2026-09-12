
import { useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import {
  AssistantRuntimeProvider,
  type AssistantRuntime,
} from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent } from "@ag-ui/client";

import {
  ConversationActionsContext,
  useConversationList,
} from "@/hooks/use-conversation-list";
import { SSE_URL } from "@/lib/config";
import { authenticatedFetch } from "@/lib/authenticated-fetch";
import { conversationService } from "@/services/conversation-service";
import { ServerImageAttachmentAdapter } from "@/services/image-attachment-adapter";
import { applyRunInputInjections, type RunAgentInputLike } from "@/services/run-input";
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

  // runtime 桥：useConversationList 在 runtime 创建之前执行（threadList adapter
  // 依赖其返回值），而切换/删除会话时需要 runtime 的取消与清空能力（取消进行
  // 中轮次、清空共享消息仓库），经 ref 延后取用——用户交互一定发生在挂载
  // effect 之后，届时 ref 已就绪。
  const runtimeRef = useRef<AssistantRuntime | null>(null);

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
  } = useConversationList(agent, { runtimeRef });

  const runtime = useAgUiRuntime({
    agent,
    adapters: {
      threadList: threadListAdapter,
      // 图片附件适配器（services/image-attachment-adapter.ts）：选中即上传、
      // 发送复用结果零等待；thread.tsx 的附件 UI（加号/拖拽区/预览）已就绪，
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

  // 填充 runtime 桥（见上方 runtimeRef 注释）
  useEffect(() => {
    runtimeRef.current = runtime;
  }, [runtime]);

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
