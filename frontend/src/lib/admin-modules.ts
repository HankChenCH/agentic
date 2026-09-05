import type { LucideIcon } from "lucide-react";
import { BrainIcon, ChartColumnIcon, DatabaseIcon } from "lucide-react";

/**
 * 管理侧模块注册表：管理首页（模块启动页）据此渲染入口卡片。
 * 新增一个管理模块 = 这里加一条 + App.tsx 路由表加对应路径，首页自动出现。
 */

export interface AdminModule {
  /** 稳定标识，用作 React key */
  key: string;
  /** 模块名（卡片标题） */
  name: string;
  /** 一句话描述（卡片副文案） */
  description: string;
  icon: LucideIcon;
  /** 模块入口路由 */
  path: string;
}

export const ADMIN_MODULES: AdminModule[] = [
  {
    key: "knowledge",
    name: "知识库",
    description: "管理知识库与文档，供对话检索引用",
    icon: DatabaseIcon,
    path: "/admin/knowledge",
  },
  {
    key: "memory-graph",
    name: "记忆图谱",
    description: "可视化长期记忆：人/物、事件与事实关系，支持时点回放",
    icon: BrainIcon,
    path: "/admin/memory-graph",
  },
  {
    key: "usage-stats",
    name: "我的用量",
    description: "LLM 调用与 token 消耗统计：趋势、按场景/模型分布与明细",
    icon: ChartColumnIcon,
    path: "/admin/usage",
  },
];
