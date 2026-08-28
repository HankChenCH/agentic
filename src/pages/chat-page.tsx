import type { FC } from "react";
import { useNavigate } from "react-router";
import { LayoutGridIcon, SparklesIcon } from "lucide-react";

import { KnowledgeSearchToolUI } from "@/components/assistant-ui/knowledge-search-tool";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { PdfSourcePanel } from "@/components/shared/pdf-source-panel";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * 聊天页（纯对话，暖砂编辑部风）。
 *
 * AgenticRuntimeProvider 在路由外层（见 App.tsx），保证切到管理侧再
 * 返回时聊天 runtime（当前会话、composer 草稿）不重建。
 * 知识库等管理功能已迁至 /admin 管理侧，由主区右上角「管理」按钮进入。
 * 视觉：侧栏暖灰纸底、主区暖纸底 + 顶部光晕（.paper-glow，见 index.css）。
 */

export const ChatPage: FC = () => {
  const navigate = useNavigate();

  return (
    <div className="flex h-screen flex-row">
      <TooltipProvider>
        {/* 侧边栏：品牌区 + 会话列表 */}
        <aside
          data-slot="aui_sidebar"
          className="aui-sidebar flex w-[260px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar"
        >
          <div className="flex shrink-0 items-center gap-2.5 p-3">
            {/* 品牌标：焦糖橘圆角块 + 衬线字标 */}
            <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
              <SparklesIcon className="size-4" />
            </span>
            <span className="font-heading text-lg font-semibold tracking-tight">
              Agentic
            </span>
          </div>

          {/* ThreadList 自带 p-2，这里只负责高度约束 */}
          <div className="min-h-0 flex-1">
            <ThreadList />
          </div>
        </aside>

        {/* 主对话区：顶部工具条 + 会话，暖纸光晕铺在内容层之下 */}
        <main className="aui-main relative flex min-w-0 flex-1 flex-col overflow-hidden">
          <div
            aria-hidden
            className="paper-glow pointer-events-none absolute inset-x-0 top-0 h-72"
          />
          <header className="relative flex h-12 shrink-0 items-center justify-end border-b border-border/60 px-3">
            {/* 管理侧入口（模块启动页） */}
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => void navigate("/admin")}
            >
              <LayoutGridIcon />
              管理
            </Button>
          </header>
          <div className="relative min-h-0 flex-1">
            <Thread />
          </div>
          {/* 注册 knowledge_search 的溯源卡片渲染器（须在 runtime 内，自身不渲染） */}
          <KnowledgeSearchToolUI />
        </main>

        {/* 检索溯源抽屉：fixed 定位不占布局，点来源卡片「查看原文」弹出 */}
        <PdfSourcePanel />
      </TooltipProvider>
    </div>
  );
};
