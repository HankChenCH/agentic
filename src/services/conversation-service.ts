import axios from "axios";

import { deleteJson, getJson, postJson } from "@/lib/http";
import type {
  BackendConversation,
  ConversationListResult,
  HistoryResult,
} from "@/services/types";

/**
 * 会话相关 REST 服务。
 *
 * 所有方法都经 @/lib/http 的 axios 实例（拦截器已自动剥去
 * { error_code, error_message, response } 信封），因此这里直接拿到干净 payload。
 * SSE run 端点（/agentic/run）不在此处，由 @ag-ui/client 的 HttpAgent 直接消费。
 */
export const conversationService = {
  /**
   * 分页拉取会话列表。
   * GET /agentic/conversation?page=&pageSize=
   */
  async listConversations(
    page = 1,
    pageSize = 20,
  ): Promise<ConversationListResult> {
    return getJson<ConversationListResult>("/agentic/conversation", {
      params: { page, pageSize },
    });
  },

  /**
   * 查询单个会话（只读）。
   * GET /agentic/conversation/{threadId}
   *
   * 后端不存在时返回 HTTP 404 → 这里转成 null；其它错误（网络/5xx 等）原样抛出，
   * 不再像之前那样把所有错误都吞成 null。
   */
  async getConversation(threadId: string): Promise<BackendConversation | null> {
    try {
      return await getJson<BackendConversation>(
        `/agentic/conversation/${threadId}`,
      );
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        return null; // 会话不存在，按"无此会话"处理
      }
      throw err; // 网络/服务端错误继续上抛，交给调用方处理
    }
  },

  /**
   * 拉取某会话的历史轮次（含每轮 messages，旧→新）。
   * GET /agentic/conversation/{threadId}/history?offset=&limit=
   */
  async listConversationHistory(
    threadId: string,
    offset = 0,
    limit = 20,
  ): Promise<HistoryResult> {
    return getJson<HistoryResult>(`/agentic/conversation/${threadId}/history`, {
      params: { offset, limit },
    });
  },

  /**
   * 删除会话（后端硬删除：连同全部轮次与消息；长期记忆不随会话删除）。
   * DELETE /agentic/conversation/{threadId}
   *
   * 返回删除前的会话快照；会话不存在时后端回 404 业务错误（BizError），
   * 由调用方决定是否提示。
   */
  async deleteConversation(threadId: string): Promise<BackendConversation> {
    return deleteJson<BackendConversation>(
      `/agentic/conversation/${threadId}`,
    );
  },

  /**
   * 显式取消当前会话的活跃轮次。
   * POST /agentic/run/cancel（body: { threadId }，幂等）
   *
   * 服务端置 Redis 取消标志即返回，流式循环在节流边界感知后收口（静默
   * 断流）。前端仍配合 abortRun 本地断链：abort 提供即时取消态与断链取消
   * 路径，REST 标志兜底代理吞断链事件 / 工具执行中不可打断的场景。
   */
  async cancelRun(threadId: string): Promise<{ canceled: boolean }> {
    return postJson<{ canceled: boolean }>("/agentic/run/cancel", {
      threadId,
    });
  },
};
