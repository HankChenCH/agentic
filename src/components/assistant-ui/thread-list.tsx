"use client";

import { useState, type FC } from "react";

import { ThreadListPrimitive, ThreadListItemPrimitive, useAuiState } from "@assistant-ui/react";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { ConfirmDialog } from "@/components/knowledge/confirm-dialog";
import { Button } from "@/components/ui/button";
import { useConversationActions } from "@/hooks/use-conversation-list";
import { cn } from "@/lib/utils";

/**
 * 会话列表侧边栏。
 *
 * 数据来源：`useAgUiRuntime({ adapters: { threadList } })` 里配置的
 * threadList adapter（见 src/agentic-runtime.tsx）。组件本身只负责渲染，
 * 不直接发请求 —— 列表、切换、新建、删除都走 runtime / context：
 *   - ThreadListPrimitive.Items         → adapter.threads
 *   - ThreadListPrimitive.New           → adapter.onSwitchToNewThread
 *   - ThreadListItemPrimitive.Trigger   → adapter.onSwitchToThread
 *   - 删除按钮（普通 Button，非 ThreadListItemPrimitive.Delete——后者点击即
 *     触发 adapter.onDelete，没有确认步骤，硬删除场景不能裸用）→ 先弹
 *     ConfirmDialog 确认，再调 useConversationActions().deleteConversation
 *
 * 当前选中态：assistant-ui 不在 item 上暴露 data-state，按官方注释用
 * `s.threads.mainThreadId === s.threadListItem.id` 比较（见
 * @assistant-ui/core ThreadListItemEvents 注释）。
 */

/** 待删除会话（渲染确认弹窗用） */
interface DeletingThread {
  id: string;
  title: string;
}

export const ThreadList: FC = () => {
  const { deleteConversation } = useConversationActions();
  const [deleting, setDeleting] = useState<DeletingThread | null>(null);

  return (
    <ThreadListPrimitive.Root
      data-slot="aui_thread-list-root"
      className="aui-thread-list-root bg-background flex h-full w-full flex-col gap-2 p-2"
    >
      {/* 新建会话：触发 adapter.onSwitchToNewThread */}
      <ThreadListPrimitive.New
        render={
          <Button
            variant="outline"
            className="aui-thread-list-new w-full justify-start gap-2"
          >
            <PlusIcon className="size-4" />
            <span>New Chat</span>
          </Button>
        }
      >
        <PlusIcon className="size-4" />
        <span>New Chat</span>
      </ThreadListPrimitive.New>

      {/* 会话列表：Items 用 children render-prop 逐条渲染 */}
      <div
        data-slot="aui_thread-list-items"
        className="aui-thread-list-items -mx-1 flex flex-1 flex-col gap-0.5 overflow-y-auto px-1"
      >
        <ThreadListPrimitive.Items>
          {() => (
            <ThreadListRow
              onRequestDelete={(id, title) => setDeleting({ id, title })}
            />
          )}
        </ThreadListPrimitive.Items>
      </div>

      {/* 删除确认：硬删除不可恢复，必须确认 */}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={`删除会话「${deleting?.title ?? ""}」？`}
        description="将删除该会话的全部消息记录，操作不可恢复。"
        confirmText="删除"
        destructive
        onConfirm={() =>
          deleting ? deleteConversation(deleting.id) : Promise.resolve(false)
        }
      />
    </ThreadListPrimitive.Root>
  );
};

interface ThreadListRowProps {
  /** 点击删除按钮：由 ThreadList 托管确认弹窗状态 */
  onRequestDelete: (id: string, title: string) => void;
}

const ThreadListRow: FC<ThreadListRowProps> = ({ onRequestDelete }) => {
  // 当前激活的会话 id 与本 item 的 id 比较，决定高亮态。
  // useAuiState 必须在 ThreadListItem 上下文内调用（Row 由 Items render-prop 渲染）。
  const isActive = useAuiState(
    (s) => s.threads.mainThreadId === s.threadListItem.id,
  );
  const itemId = useAuiState((s) => s.threadListItem.id);
  const itemTitle = useAuiState((s) => s.threadListItem.title);

  return (
    // Root 包裹单条会话。relative 给 Delete 按钮做绝对定位锚点。
    <ThreadListItemPrimitive.Root
      data-slot="aui_thread-list-item-root"
      className={cn(
        "aui-thread-list-item-root group/item relative rounded-lg transition-colors",
        isActive && "bg-muted",
      )}
    >
      {/* 点击切换会话 → adapter.onSwitchToThread */}
      <ThreadListItemPrimitive.Trigger
        render={
          <div
            tabIndex={0}
            role="button"
            className="aui-thread-list-item-trigger flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 outline-none"
          >
            <span className="aui-thread-list-item-title truncate text-sm text-foreground">
              {/* Title 只渲染纯文本（无 className 支持），用 span 包一层做截断 */}
              <ThreadListItemPrimitive.Title />
            </span>
          </div>
        }
      />

      {/* 删除：hover/focus 时显示。点击弹确认框，确认后走
          useConversationActions().deleteConversation（见文件头注释） */}
      <Button
        variant="ghost"
        size="icon-sm"
        className={cn(
          "aui-thread-list-item-delete",
          "absolute end-1 top-1/2 -translate-y-1/2 opacity-0 transition-opacity",
          "group-hover/item:opacity-100 focus-visible:opacity-100",
        )}
        aria-label="Delete conversation"
        onClick={() => onRequestDelete(itemId, itemTitle ?? "New Chat")}
      >
        <Trash2Icon className="size-3.5" />
      </Button>
    </ThreadListItemPrimitive.Root>
  );
};
