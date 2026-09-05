/**
 * run 请求体加工（纯函数，无 React / 网络依赖）：
 *
 * HttpAgent 组装好 RunAgentInput 后、发出 POST 前的两项注入，原本内联在
 * agentic-runtime 的 prepareRunAgentInput 覆写里；抽成本模块便于单测。
 * store 读取（选中智能体、已知会话集合）留在调用点请求时现读，避免闭包陈旧。
 *
 * 1) 分支信号提升：重新生成/编辑在 thread.tsx 经 RunConfig.custom 携带
 *    branchBaseMessageId（基点消息 id，与库中 message_id 同源），runtime 组装
 *    时落在 forwardedProps.runConfig；这里提升为 forwardedProps.branch 供
 *    后端 open_turn 定位兄弟轮次。runConfig 是内部载体，提升后删除。
 *    重生成/编辑都发生在已有会话上，不受 agentId 的「新会话」门控。
 *
 * 2) 智能体选择：forwardedProps.agentId（后端 run 链路消费该字段绑定新会话
 *    的智能体）。只在「新会话」时携带：判定依据是 threadId 不在 agent-store
 *    的已知会话集合（会话列表回填）——runtime 组装的 run 输入拿不到已加载的
 *    历史消息，按消息内容/条数判定不可靠（旧会话续聊会被误判为新会话而改写
 *    绑定）。新会话 threadId 是前端新生成的 UUID，必然不在列表中；首条消息
 *    发出、会话落库并刷新列表后才进入集合，此后继续聊/重生成一律不携带。
 */

/** prepareRunAgentInput 输出的最小结构面（与 @ag-ui/client RunAgentInput 对齐） */
export type RunAgentInputLike = {
  forwardedProps?: Record<string, unknown>;
  threadId?: string;
  resume?: unknown;
} & Record<string, unknown>;

/** 注入所需的运行时快照（调用点从 store 现读） */
export interface RunInjectionOptions {
  /** agent-store 当前选中项；null/空 = 未选择（走服务端默认/会话绑定） */
  selectedAgentId: string | null;
  /** threadId 是否为已存在会话（会话列表已回填的 knownThreads） */
  knownThread: boolean;
}

export function applyRunInputInjections(
  input: RunAgentInputLike,
  options: RunInjectionOptions,
): RunAgentInputLike {
  const forwarded: Record<string, unknown> = { ...(input.forwardedProps ?? {}) };
  const custom = forwarded.runConfig as Record<string, unknown> | undefined;
  const baseMessageId = custom?.branchBaseMessageId;
  if (typeof baseMessageId === "string" && baseMessageId) {
    forwarded.branch = { baseMessageId };
  }
  delete forwarded.runConfig;
  const isNewConversation = !input.resume && !options.knownThread;
  if (options.selectedAgentId && isNewConversation) {
    forwarded.agentId = options.selectedAgentId;
  }
  return { ...input, forwardedProps: forwarded };
}
