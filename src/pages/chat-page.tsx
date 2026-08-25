import type { FC } from "react";
import { NavLink } from "react-router";
import { BookOpenIcon, MessageSquareIcon } from "lucide-react";

import { KnowledgeSearchToolUI } from "@/components/assistant-ui/knowledge-search-tool";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * 聊天页（原 App.tsx 布局迁移而来）。
 *
 * AgenticRuntimeProvider 在路由外层（见 App.tsx），保证切到知识库页再
 * 返回时聊天 runtime（当前会话、composer 草稿）不重建。
 * 侧栏顶部新增主导航：对话 / 知识库。
 */

const NAV_ITEMS = [
  { to: "/", label: "对话", icon: MessageSquareIcon, end: true },
  { to: "/knowledge", label: "知识库", icon: BookOpenIcon, end: false },
] as const;

export const ChatPage: FC = () => {
  return (
    <div className="flex h-screen flex-row">
      <TooltipProvider>
        {/* 侧边栏：主导航 + 会话列表 */}
        <aside
          data-slot="aui_sidebar"
          className="aui-sidebar flex w-[260px] shrink-0 flex-col border-r border-border bg-background"
        >
          <nav className="flex shrink-0 gap-1 p-2 pb-0">
            {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "aui-nav-link flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-sm transition-colors",
                    isActive
                      ? "bg-muted font-medium text-foreground"
                      : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                  )
                }
              >
                <Icon className="size-4" />
                {label}
              </NavLink>
            ))}
          </nav>

          {/* ThreadList 自带 p-2，这里只负责高度约束 */}
          <div className="min-h-0 flex-1">
            <ThreadList />
          </div>
        </aside>

        {/* 主对话区 */}
        <main className="aui-main min-w-0 flex-1 overflow-hidden">
          <Thread />
          {/* 注册 knowledge_search 的溯源卡片渲染器（须在 runtime 内，自身不渲染） */}
          <KnowledgeSearchToolUI />
        </main>
      </TooltipProvider>
    </div>
  );
};
