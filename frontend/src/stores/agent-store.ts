import { create } from "zustand";
import { persist } from "zustand/middleware";

import { agentService, type AgentInfo } from "@/services/agent-service";

/**
 * 智能体选择（zustand）：目录缓存 + 当前选中项，持久化到 localStorage
 * （key: agentic-agent）。选中项 = 「下一个新会话」绑定的智能体——仅在
 * 新会话首条 run 时经 forwardedProps.agentId 上送（agentic-runtime 的
 * prepareRunAgentInput 覆写，见该文件注释）；会话开始后不再切换，旧会话
 * 保持各自的绑定。
 *
 * `supportsVision` 是当前选中智能体的图片能力镜像：新会话视图的附件入口
 * 据此显隐（未声明 vision 的智能体收不到图片，上传了也只会被降级丢弃）。
 * 持久化的选中项可能已不在最新目录（后端下线/改名）——ensure 拉取后校验，
 * 失配回退默认。
 */
interface AgentState {
  selectedAgentId: string | null;
  agents: AgentInfo[];
  defaultAgentId: string | null;
  /**
   * 已存在会话的 threadId 集合（值为其绑定的 agentic_id），由会话列表
   * 加载/刷新时回填（use-conversation-list）。prepareRunAgentInput 据此
   * 判定「新会话」：threadId 不在集合中才注入 agentId——runtime 组装的
   * run 输入里拿不到已加载的历史消息，消息数/角色启发式不可靠。
   */
  knownThreads: Record<string, string>;
  setKnownThreads: (items: { threadId: string; agenticId: string }[]) => void;
  /** 防并发重复拉取的闸门：请求发出即置位 */
  loaded: boolean;
  /** 拉取目录并校验持久化选中项（选择器挂载时调用一次） */
  ensure: () => Promise<void>;
  select: (agentId: string) => void;
}

export const useAgentStore = create<AgentState>()(
  persist(
    (set, get) => ({
      selectedAgentId: null,
      agents: [],
      defaultAgentId: null,
      knownThreads: {},
      setKnownThreads: (items) =>
        set({
          knownThreads: Object.fromEntries(
            items.map((item) => [item.threadId, item.agenticId]),
          ),
        }),
      loaded: false,
      ensure: async () => {
        if (get().loaded) return;
        set({ loaded: true });
        try {
          const catalog = await agentService.listAgents();
          const { selectedAgentId } = get();
          const valid =
            selectedAgentId &&
            catalog.agents.some((a) => a.id === selectedAgentId)
              ? selectedAgentId
              : null;
          set({
            agents: catalog.agents,
            defaultAgentId: catalog.defaultAgentId,
            // 持久化项失效时回退 null（= 服务端默认），不强行覆盖用户选择
            selectedAgentId: selectedAgentId ? valid : null,
          });
        } catch {
          set({ loaded: false });
        }
      },
      select: (agentId) => set({ selectedAgentId: agentId }),
    }),
    {
      name: "agentic-agent",
      // 只持久化用户选择；目录数据每次会话重新拉取
      partialize: (s) => ({ selectedAgentId: s.selectedAgentId }),
    },
  ),
);

/** 非 React 环境（prepareRunAgentInput 覆写）读选中项的入口 */
export const getSelectedAgentId = (): string | null =>
  useAgentStore.getState().selectedAgentId;

/** threadId 是否为已存在会话（会话列表已回填的绑定） */
export const isKnownThread = (threadId: string | undefined): boolean =>
  threadId != null && threadId in useAgentStore.getState().knownThreads;
