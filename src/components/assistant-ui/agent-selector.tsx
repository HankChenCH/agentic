import { useEffect } from "react";
import type { ComponentType } from "react";
import {
  BookOpenIcon,
  BotIcon,
  ChevronDownIcon,
  SparklesIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useAgentStore } from "@/stores/agent-store";

/** 新会话视图直接展示的 pill 上限：超出的收进「更多」下拉 */
const MAX_DIRECT_PILLS = 3;

/**
 * 智能体图标映射：目录协议只有 id/name/description，无图标元数据——
 * 已知内置 id 定制，未知回退通用 Bot。
 */
const AGENT_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  "builtin:demo": SparklesIcon,
  "builtin:rag": BookOpenIcon,
};

const agentIcon = (id: string): ComponentType<{ className?: string }> =>
  AGENT_ICONS[id] ?? BotIcon;

/**
 * 新会话的智能体分段选择器（参考 DeepSeek 首页模式切换）：composer 上方
 * 居中胶囊 pills，选中高亮；超过 {@link MAX_DIRECT_PILLS} 个智能体时前
 * N 个直接切换，其余收进「更多」下拉。
 *
 * 只在无消息的新会话视图渲染（thread.tsx 用 AuiIf isNewChatView 包裹）：
 * 选择即「下一个新会话」绑定的智能体——run 请求仅在首条消息时携带
 * forwardedProps.agentId（见 agentic-runtime），会话开始后不再切换。
 * 目录未就绪渲染 null，不闪烁占位。
 */
export const AgentModeSwitch = () => {
  const agents = useAgentStore((s) => s.agents);
  const defaultAgentId = useAgentStore((s) => s.defaultAgentId);
  const selectedAgentId = useAgentStore((s) => s.selectedAgentId);
  const select = useAgentStore((s) => s.select);
  const ensure = useAgentStore((s) => s.ensure);

  // 目录拉取的唯一触发点：组件挂载即幂等拉取（失败静默，下次挂载重试）。
  // 必须在空目录 early-return 之前调用，否则目录永远为空。
  useEffect(() => {
    void ensure();
  }, [ensure]);

  if (!agents.length) return null;

  // 未显式选择时按服务端默认高亮（null = 不注入 agentId，后端回退默认）
  const activeId = selectedAgentId ?? defaultAgentId;
  const direct = agents.slice(0, MAX_DIRECT_PILLS);
  const rest = agents.slice(MAX_DIRECT_PILLS);
  const restActive = rest.some((a) => a.id === activeId);

  return (
    <div className="aui-agent-mode-switch mb-1 flex justify-center">
      <div className="bg-muted/60 border-border/40 flex items-center gap-1 rounded-full border p-1">
        {direct.map((agent) => {
          const Icon = agentIcon(agent.id);
          const active = agent.id === activeId;
          return (
            <button
              key={agent.id}
              type="button"
              title={agent.description}
              onClick={() => select(agent.id)}
              className={cn(
                "flex cursor-pointer items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm transition-colors",
                active
                  ? "bg-background text-foreground shadow-card border-border/60 border"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              {agent.name}
            </button>
          );
        })}
        {rest.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  title="更多智能体"
                  className={cn(
                    "h-auto gap-1.5 rounded-full px-3.5 py-1.5 text-sm",
                    restActive
                      ? "bg-background text-foreground shadow-card border-border/60 border"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                />
              }
            >
              <BotIcon className="size-4" />
              更多
              <ChevronDownIcon className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="center" className="w-auto min-w-56">
              <DropdownMenuRadioGroup
                value={activeId ?? ""}
                onValueChange={(value) => select(value as string)}
              >
                {rest.map((agent) => {
                  const Icon = agentIcon(agent.id);
                  return (
                    <DropdownMenuRadioItem
                      key={agent.id}
                      value={agent.id}
                      title={agent.description}
                      className="gap-2 py-1.5"
                    >
                      <Icon className="size-4" />
                      {agent.name}
                    </DropdownMenuRadioItem>
                  );
                })}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    </div>
  );
};
