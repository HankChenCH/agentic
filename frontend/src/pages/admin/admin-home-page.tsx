import type { FC } from "react";
import { useNavigate } from "react-router";
import { ChevronRightIcon } from "lucide-react";

import { AdminPageShell } from "@/components/shared/admin-page-shell";
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
    <AdminPageShell
      backTo="/"
      backLabel="返回对话"
      title="管理控制台"
      description="每个功能模块一个独立入口，点击卡片进入"
    >
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
    </AdminPageShell>
  );
};
