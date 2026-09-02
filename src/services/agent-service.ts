import { getJson } from "@/lib/http";

/** 智能体目录条目（GET /agentic/agents 的 payload 形状） */
export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  /** 是否支持图片输入（前端据此屏蔽上传入口） */
  supportsVision: boolean;
}

export interface AgentCatalog {
  defaultAgentId: string;
  agents: AgentInfo[];
}

/**
 * 智能体目录 REST 服务。
 *
 * 目录与登录用户无关（静态注册表读），选择器挂载时拉取一次缓存进
 * agent-store；选中项经 run 请求的 forwardedProps.agentId 上送（注入点在
 * agentic-runtime 对 HttpAgent 的 prepareRunAgentInput 覆写）。
 */
export const agentService = {
  async listAgents(): Promise<AgentCatalog> {
    return getJson<AgentCatalog>("/agentic/agents");
  },
};
