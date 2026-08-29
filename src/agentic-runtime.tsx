"use client";

import { useMemo } from "react";
import type { ReactNode } from "react";

import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent } from "@ag-ui/client";

import {
  ConversationActionsContext,
  useConversationList,
} from "@/hooks/use-conversation-list";
import { SSE_URL } from "@/lib/config";
import { conversationService } from "@/services/conversation-service";

export const AgenticRuntimeProvider = ({
  children,
}: {
  children: ReactNode;
}) => {
  // HttpAgent 指向后端 ag-ui SSE 端点（URL 来自 @/lib/config，与 REST 同源配置）。
  // requestInit 会把整个 RunAgentInput（含 threadId/runId/messages）作为 POST body 发出。
  // 注意：runtime 每次发消息都从 agent.threadId 取值，但库不会在切换会话时更新它，
  // 同步逻辑在 useConversationList 的 onSwitchToThread/onSwitchToNewThread 里。
  const agent = useMemo(() => new HttpAgent({ url: SSE_URL }), []);

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
  } = useConversationList(agent);

  const runtime = useAgUiRuntime({
    agent,
    adapters: { threadList: threadListAdapter },
    // 「停止生成」双通道取消：
    // 1) REST 置服务端取消标志——断链事件可能被代理/缓冲吞掉，且工具执行中
    //    的生成器无法被断链打断，标志由流式循环在节流边界与工具入口感知收口；
    // 2) 本地 abort——即时取消态 + 断链取消路径（服务器在下一个 yield 边界
    //    收到 GeneratorExit）。AbortError 会被 runtime 归类为 RUN_CANCELLED，
    //    不会误报成运行错误。REST 失败不阻断本地取消，静默即可。
    onCancel: () => {
      void conversationService.cancelChatRun(agent.threadId).catch(() => {});
      agent.abortRun();
    },
  });

  // 删除、加载更多等会话操作与分页状态经 context 下发给侧栏等
  // runtime 之外的组件（确认弹窗 / 加载更多按钮入口）
  const actions = useMemo(
    () => ({ deleteConversation, loadMoreConversations, hasMore, isLoadingMore }),
    [deleteConversation, loadMoreConversations, hasMore, isLoadingMore],
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ConversationActionsContext.Provider value={actions}>
        {children}
      </ConversationActionsContext.Provider>
    </AssistantRuntimeProvider>
  );
};
