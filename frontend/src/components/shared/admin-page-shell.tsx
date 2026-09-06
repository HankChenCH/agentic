import type { FC, ReactNode } from "react";
import { useNavigate } from "react-router";
import { ArrowLeftIcon, MessageSquareIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * 管理侧模块页共享壳（原各页手写的 h-dvh + 光晕 + 居中容器 + 返回键）。
 *
 * 「模块即 App」：壳只负责页面外框与返回导航，不含侧栏/菜单；内容区
 * 结构由各页自定。移动端口径在此统一——响应式内边距、标题字号与
 * 操作区换行，新增模块页直接套壳即可获得同样的响应式行为。
 */

/** 内容容器最大宽度档位（对应原各页的 max-w-4xl / 5xl / 6xl） */
type ShellWidth = "narrow" | "medium" | "wide";

const WIDTH_CLASS: Record<ShellWidth, string> = {
  narrow: "max-w-4xl",
  medium: "max-w-5xl",
  wide: "max-w-6xl",
};

export const AdminPageShell: FC<{
  /** 返回目标路由；不传则不渲染返回键（预留） */
  backTo: string;
  /** 返回键文案（如「返回对话」「返回控制台」） */
  backLabel: string;
  /** 页面标题（ReactNode，知识库详情页会带状态徽章） */
  title: ReactNode;
  /** 标题下的一句描述 */
  description?: ReactNode;
  /** 标题行右侧操作区（按钮组），窄屏自动换行 */
  actions?: ReactNode;
  width?: ShellWidth;
  children: ReactNode;
}> = ({
  backTo,
  backLabel,
  title,
  description,
  actions,
  width = "narrow",
  children,
}) => {
  const navigate = useNavigate();
  const backToChat = backTo === "/";

  return (
    <div className="relative h-dvh overflow-auto bg-background">
      {/* 顶部暖纸光晕（.paper-glow，见 index.css），铺在内容层之下 */}
      <div
        aria-hidden
        className="paper-glow pointer-events-none absolute inset-x-0 top-0 h-80"
      />
      <div
        className={`relative mx-auto flex min-h-full flex-col gap-6 ${WIDTH_CLASS[width]} p-4 sm:p-6 lg:p-8`}
      >
        <header className="flex flex-col gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 w-fit text-muted-foreground"
            onClick={() => void navigate(backTo)}
          >
            {backToChat ? <MessageSquareIcon /> : <ArrowLeftIcon />}
            {backLabel}
          </Button>
          <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3 pt-2">
            <div className="min-w-0">
              {typeof title === "string" ? (
                <h1 className="font-heading text-2xl font-semibold tracking-tight sm:text-3xl">
                  {title}
                </h1>
              ) : (
                title
              )}
              {description != null && (
                <p className="mt-2 text-sm text-muted-foreground">
                  {description}
                </p>
              )}
            </div>
            {actions != null && (
              <div className="flex flex-wrap items-center gap-2">{actions}</div>
            )}
          </div>
        </header>

        {children}
      </div>
    </div>
  );
};
