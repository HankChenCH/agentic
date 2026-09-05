import { useEffect } from "react";
import {
  BookOpenTextIcon,
  CalendarClockIcon,
  CloudSunIcon,
  HistoryIcon,
  LibraryIcon,
  ListTreeIcon,
  NetworkIcon,
  SearchIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";

import { useToolCatalogStore } from "@/stores/tool-catalog-store";

/**
 * 工具展示注册表：机器工具名 → 前端标识（中文标签 / 进行时短语 / 图标）。
 *
 * 背景与边界：ag-ui 事件的 toolCallName 只有机器名，直接展示就是裸
 * snake_case——业界惯例（ChatGPT/Claude 的活动条、MCP annotations.title）
 * 是「机器名做主键、显示标签做元数据、渲染在客户端」。本表即客户端这一层：
 * label 只用于 UI，绝不回传后端、不影响 LLM 看到的工具名与协议契约。
 *
 * 解析降级链（useToolDisplay）：本地注册表 → 后端能力目录
 * （GET /agentic/tool-catalog，兜「后端新增、前端未注册」的工具）→
 * 机器名下划线转空格。knowledge 双工具另有定制溯源卡片
 * （knowledge-search-tool.tsx），不走 fallback，但其条目仍在此维护，
 * 保持「一个工具一处标识」的单一出处。
 */

export interface ToolDisplay {
  /** 完成态短标签 */
  label: string;
  /** 进行时短语（缺省按 label + "…" 生成） */
  running?: string;
  icon: LucideIcon;
}

export const TOOL_REGISTRY: Record<string, ToolDisplay> = {
  get_weather: {
    label: "查询天气",
    running: "正在查询天气…",
    icon: CloudSunIcon,
  },
  timeline: {
    label: "回忆时间线",
    running: "正在检索记忆时间线…",
    icon: HistoryIcon,
  },
  expand: {
    label: "展开记忆网络",
    running: "正在展开关联记忆…",
    icon: NetworkIcon,
  },
  state_at: {
    label: "回溯历史状态",
    running: "正在回放时点状态…",
    icon: CalendarClockIcon,
  },
  knowledge_list: {
    label: "查看知识库清单",
    icon: LibraryIcon,
  },
  knowledge_search: {
    label: "知识库检索",
    running: "知识库检索中…",
    icon: SearchIcon,
  },
  knowledge_context: {
    label: "读取文档片段",
    running: "片段读取中…",
    icon: BookOpenTextIcon,
  },
  knowledge_document_list: {
    label: "查看文档清单",
    icon: ListTreeIcon,
  },
};

/** 未知工具的兜底展示：下划线转空格（不翻译，仅去裸感） */
export const prettifyToolName = (name: string): string =>
  name.replace(/_/g, " ");

export interface ResolvedToolDisplay {
  label: string;
  running: string;
  icon: LucideIcon;
}

/** 同步解析：仅本地注册表 + 机器名兜底（非 React 语境用） */
export function resolveToolDisplay(toolName: string): ResolvedToolDisplay {
  const entry = TOOL_REGISTRY[toolName];
  if (entry) {
    return { running: entry.running ?? `${entry.label}…`, ...entry };
  }
  const label = prettifyToolName(toolName);
  return { label, running: `${label}…`, icon: WrenchIcon };
}

/**
 * React 解析钩子：本地注册表优先，后端能力目录兜底（挂载时幂等拉取一次，
 * 失败静默降级——标签只是增强，不能影响工具调用渲染本身）。
 */
export function useToolDisplay(toolName: string): ResolvedToolDisplay {
  const catalogLabel = useToolCatalogStore((s) => s.byName[toolName]);
  useEffect(() => {
    void useToolCatalogStore.getState().ensure();
  }, []);

  const entry = TOOL_REGISTRY[toolName];
  const label = entry?.label ?? catalogLabel ?? prettifyToolName(toolName);
  return {
    label,
    running: entry?.running ?? `${label}…`,
    icon: entry?.icon ?? WrenchIcon,
  };
}
