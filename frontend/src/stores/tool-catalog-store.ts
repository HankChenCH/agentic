import { create } from "zustand";

import { toolCatalogService } from "@/services/tool-catalog-service";

/**
 * 工具目录缓存（zustand）：工具机器名 → 中文展示标题。
 *
 * 目录与登录用户无关、每次登录后基本不变，幂等拉取一次共享给所有
 * tool-call part（useToolDisplay 挂载时触发 ensure）。拉取失败静默降级
 * （复位 loaded 允许下次挂载重试），标签只是展示增强，不 toast 不阻塞
 * 工具渲染。
 */
interface ToolCatalogState {
  byName: Record<string, string>;
  /** 防并发重复拉取的闸门：请求发出即置位 */
  loaded: boolean;
  ensure: () => Promise<void>;
}

export const useToolCatalogStore = create<ToolCatalogState>((set, get) => ({
  byName: {},
  loaded: false,
  ensure: async () => {
    if (get().loaded) return;
    set({ loaded: true });
    try {
      const catalog = await toolCatalogService.getToolCatalog();
      const byName: Record<string, string> = {};
      for (const component of catalog.components) {
        for (const tool of component.tools) {
          if (tool.title) byName[tool.name] = tool.title;
        }
      }
      set({ byName });
    } catch {
      set({ loaded: false });
    }
  },
}));
