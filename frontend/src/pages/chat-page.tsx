import { useState, type FC } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import {
  LayoutGridIcon,
  LogOutIcon,
  MenuIcon,
  SparklesIcon,
  UserRoundIcon,
  XIcon,
} from "lucide-react";

import { useAuiEvent } from "@assistant-ui/react";

import { A2uiDataUI } from "@/components/assistant-ui/a2ui-data";
import {
  KnowledgeContextToolUI,
  KnowledgeSearchToolUI,
} from "@/components/assistant-ui/knowledge-search-tool";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { ThreadRouteSync } from "@/components/assistant-ui/thread-route-sync";
import { PdfSourcePanel } from "@/components/shared/pdf-source-panel";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { logoutRemote } from "@/lib/logout-remote";
import { useAuthStore } from "@/stores/auth-store";

/**
 * 聊天页（纯对话，暖砂编辑部风）。
 *
 * AgenticRuntimeProvider 在路由外层（见 App.tsx），保证切到管理侧再
 * 返回时聊天 runtime（当前会话、composer 草稿）不重建。
 * 知识库等管理功能已迁至 /admin 管理侧，由主区右上角「管理」按钮进入。
 * 视觉：侧栏暖灰纸底、主区暖纸底 + 顶部光晕（.paper-glow，见 index.css）。
 *
 * 响应式：md 及以上侧栏常驻（桌面布局不变）；以下收成抽屉——顶栏汉堡
 * 打开，遮罩点击 / 选中或新建会话后自动收起（onRequestClose 回调）。
 * h-dvh 跟随手机浏览器地址栏收展，避免 100vh 把底部输入框顶出可视区。
 */

export const ChatPage: FC = () => {
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const user = useAuthStore((s) => s.user);

  // 附件添加失败转 toast：图片选中即上传（services/image-attachment-adapter）
  // 后，add 阶段的失败由 runtime 原位标记附件错误态（缩略图红框浮层）并发出
  // composer.attachmentAddError——此处补一层离开输入框仍可见的提示。scope「*」
  // 从根客户端订阅，正文与编辑消息的 composer 失败都能收到；文件类型被拒
  // （not-accepted）同样走这里，此前是静默失败。
  useAuiEvent(
    { scope: "*", event: "composer.attachmentAddError" },
    ({ reason, message }) => {
      toast.error(
        reason === "adapter-error" ? `图片上传失败：${message}` : message,
      );
    },
  );

  // 退出：best-effort 吊销服务端 refresh 会话族 + 清本地会话 + 整页跳登录
  // （整页刷新顺带重置 runtime 与 agent.threadId，会话身份随用户切换彻底归零；
  // keepalive 保证跳转不中止撤销请求，任何失败不阻塞本地登出）
  const handleLogout = () => {
    logoutRemote();
    useAuthStore.getState().logout();
    window.location.assign("/login");
  };

  return (
    <div className="flex h-dvh flex-row">
      <TooltipProvider>
        {/* / 与 /chat/:threadId ↔ 当前会话双向同步（自身不渲染） */}
        <ThreadRouteSync />
        {/* 移动端抽屉遮罩（md 以下才有） */}
        <div
          aria-hidden={!drawerOpen}
          className={cn(
            "fixed inset-0 z-30 bg-foreground/30 transition-opacity duration-200 md:hidden",
            drawerOpen ? "opacity-100" : "pointer-events-none opacity-0",
          )}
          onClick={() => setDrawerOpen(false)}
        />

        {/* 侧边栏：品牌区 + 会话列表；移动端 fixed 抽屉，桌面 static 常驻 */}
        <aside
          data-slot="aui_sidebar"
          className={cn(
            "aui-sidebar fixed inset-y-0 left-0 z-40 flex w-[260px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-transform duration-200 ease-out",
            "md:static md:translate-x-0",
            drawerOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="flex shrink-0 items-center gap-2.5 p-3">
            {/* 品牌标：焦糖橘圆角块 + 衬线字标 */}
            <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
              <SparklesIcon className="size-4" />
            </span>
            <span className="font-heading text-lg font-semibold tracking-tight">
              Agentic
            </span>
            {/* 移动端抽屉关闭按钮 */}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="收起会话列表"
              className="ml-auto text-muted-foreground hover:text-foreground md:hidden"
              onClick={() => setDrawerOpen(false)}
            >
              <XIcon />
            </Button>
          </div>

          {/* ThreadList 自带 p-2，这里只负责高度约束；选中/新建后收起抽屉 */}
          <div className="min-h-0 flex-1">
            <ThreadList onRequestClose={() => setDrawerOpen(false)} />
          </div>
        </aside>

        {/* 主对话区：顶部工具条 + 会话，暖纸光晕铺在内容层之下 */}
        <main className="aui-main relative flex min-w-0 flex-1 flex-col overflow-hidden">
          <div
            aria-hidden
            className="paper-glow pointer-events-none absolute inset-x-0 top-0 h-72"
          />
          <header className="relative flex h-12 shrink-0 items-center border-b border-border/60 px-3">
            {/* 移动端：打开会话抽屉 */}
            <Button
              variant="ghost"
              size="icon"
              aria-label="打开会话列表"
              className="text-muted-foreground hover:text-foreground md:hidden"
              onClick={() => setDrawerOpen(true)}
            >
              <MenuIcon />
            </Button>
            {/* 管理侧入口（模块启动页）；ml-auto 保证桌面端（汉堡隐藏时）仍右对齐 */}
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto text-muted-foreground hover:text-foreground"
              onClick={() => void navigate("/admin")}
            >
              <LayoutGridIcon />
              管理
            </Button>
            {/* 用户菜单：显示名 + 退出登录 */}
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <UserRoundIcon />
                    {user?.nickname || user?.username || "用户"}
                  </Button>
                }
              />
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>{user?.username}</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => void navigate("/profile")}>
                  <UserRoundIcon />
                  个人资料
                </DropdownMenuItem>
                <DropdownMenuItem onClick={handleLogout}>
                  <LogOutIcon />
                  退出登录
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </header>
          <div className="relative min-h-0 flex-1">
            <Thread />
          </div>
          {/* 注册 knowledge_search / knowledge_context 的溯源卡片渲染器（须在 runtime 内，自身不渲染） */}
          <KnowledgeSearchToolUI />
          <KnowledgeContextToolUI />
          {/* 注册 A2UI data part 渲染器（name="a2ui"，天气卡片等声明式 UI） */}
          <A2uiDataUI />
        </main>

        {/* 检索溯源抽屉：fixed 定位不占布局，点来源卡片「查看原文」弹出 */}
        <PdfSourcePanel />
      </TooltipProvider>
    </div>
  );
};
