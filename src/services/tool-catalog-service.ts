import { getJson } from "@/lib/http";
import type { ToolCatalog } from "@/services/types";

/**
 * 工具能力目录服务：后端静态注册表的展示元数据（组件 → 工具的中文名/描述/
 * 参数 schema）。仅供前端 UI 标识化消费，不参与任何请求编排。
 */
export const toolCatalogService = {
  async getToolCatalog(): Promise<ToolCatalog> {
    return getJson<ToolCatalog>("/agentic/tool-catalog");
  },
};
