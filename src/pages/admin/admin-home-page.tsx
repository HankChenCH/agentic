import type { FC } from "react";
import { useNavigate } from "react-router";
import { ChevronRightIcon, MessageSquareIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ADMIN_MODULES } from "@/lib/admin-modules";

/**
 * 管理控制台首页（/admin）：「模块即 App」的启动页（暖砂编辑部风）。
 *
 * 无侧栏无菜单，只铺一层模块入口卡片，点击整卡进入对应模块（模块内
 * 用各自的返回键回退，详见 /admin/knowledge 两页）。模块清单来自
 * lib/admin-modules.ts 注册表，未来新模块无需改本页。
 */

export const AdminHomePage: FC = () => {
  const navigate = useNavigate();

  return (
    <div className="relative h-screen overflow-auto bg-background">
      {/* 顶部暖纸光晕（.paper-glow，见 index.css），铺在内容层之下 */}
      <div
        aria-hidden
        className="paper-glow pointer-events-none absolute inset-x-0 top-0 h-80"
      />
      <div className="relative mx-auto flex min-h-full max-w-4xl flex-col gap-10 p-6 lg:p-8">
        <header className="flex flex-col gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 w-fit text-muted-foreground"
            onClick={() => void navigate("/")}
          >
            <MessageSquareIcon />
            返回对话
          </Button>
          <div className="pt-4">
            <h1 className="font-heading text-3xl font-semibold tracking-tight">
              管理控制台
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              每个功能模块一个独立入口，点击卡片进入
            </p>
          </div>
        </header>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {ADMIN_MODULES.map(({ key, name, description, icon: Icon, path }) => (
            <button
              key={key}
              type="button"
              onClick={() => void navigate(path)}
              className="group flex flex-col items-start gap-3 rounded-xl border border-border/80 bg-card p-5 text-left shadow-card transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-card-hover"
            >
              {/* 模块图标块：hover 时焦糖橘填充，呼应「进入」动势 */}
              <div className="flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                <Icon className="size-6" />
              </div>
              <div className="flex items-center gap-1">
                <span className="font-medium">{name}</span>
                <ChevronRightIcon className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
              </div>
              <p className="text-sm text-muted-foreground">{description}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
