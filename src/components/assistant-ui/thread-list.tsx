
import { useState, type FC } from "react";

import { ThreadListPrimitive, ThreadListItemPrimitive, useAuiState } from "@assistant-ui/react";
import { ChevronsDownIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";

import { ConfirmDialog } from "@/components/shared/confirm-dialog";
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

export const ThreadList: FC<{ onRequestClose?: () => void }> = ({
  onRequestClose,
}) => {
  const { deleteConversation, hasMore, isLoadingMore, loadMoreConversations } =
    useConversationActions();
  const [deleting, setDeleting] = useState<DeletingThread | null>(null);

  return (
    <ThreadListPrimitive.Root
      data-slot="aui_thread-list-root"
      className="aui-thread-list-root flex h-full w-full flex-col gap-2 p-2"
    >
      {/* 新建会话：触发 adapter.onSwitchToNewThread；移动端抽屉里选中后收起 */}
      <ThreadListPrimitive.New
        render={
          <Button
            variant="outline"
            className="aui-thread-list-new w-full justify-start gap-2 border-dashed border-border/80 text-muted-foreground hover:border-primary/50 hover:bg-card hover:text-foreground"
            onClick={() => onRequestClose?.()}
          >
            <PlusIcon className="size-4" />
            <span>新建对话</span>
          </Button>
        }
      >
        <PlusIcon className="size-4" />
        <span>新建对话</span>
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
              onRequestClose={onRequestClose}
            />
          )}
        </ThreadListPrimitive.Items>

        {/* 加载更多：首屏只拉一页，列表超出默认页大小时由此追加更早的会话。
            随列表一起滚动，仅在还有下一页时占位；加载中禁用防重复点击 */}
        {hasMore && (
          <Button
            variant="ghost"
            size="sm"
            data-slot="aui_thread-list-load-more"
            className="aui-thread-list-load-more mt-1 w-full justify-center gap-1.5 text-muted-foreground"
            disabled={isLoadingMore}
            onClick={() => void loadMoreConversations()}
          >
            {isLoadingMore ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <ChevronsDownIcon className="size-3.5" />
            )}
            {isLoadingMore ? "加载中…" : "加载更多会话"}
          </Button>
        )}
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
  /** 选中会话后回调（移动端抽屉收起），桌面端不传 */
  onRequestClose?: () => void;
}

const ThreadListRow: FC<ThreadListRowProps> = ({
  onRequestDelete,
  onRequestClose,
}) => {
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
        "aui-thread-list-item-root group/item relative rounded-lg transition-all",
        // 选中 = 暖灰底上的白卡浮起
        isActive && "bg-card shadow-sm ring-1 ring-border/70",
      )}
    >
      {/* 点击切换会话 → adapter.onSwitchToThread；onClick 附加回调供移动端抽屉收起
         （primitive 的事件与 render 元素自身 handler 会串联执行，互不影响） */}
      <ThreadListItemPrimitive.Trigger
        render={
          <div
            tabIndex={0}
            role="button"
            onClick={() => onRequestClose?.()}
            className="aui-thread-list-item-trigger flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 outline-none transition-colors hover:bg-accent/60 max-md:pe-9"
          >
            <span className="aui-thread-list-item-title truncate text-sm text-foreground">
              {/* Title 只渲染纯文本（无 className 支持），用 span 包一层做截断 */}
              <ThreadListItemPrimitive.Title />
            </span>
          </div>
        }
      />

      {/* 删除：桌面 hover/focus 时显示；触屏无 hover，md 以下常显。
          点击弹确认框，确认后走 useConversationActions().deleteConversation
          （见文件头注释）。垂直定位用固定 top-1 而非 top-1/2 -translate-y-1/2：
          Button 基类的 active:translate-y-px 与 -translate-y-1/2 同写
          --tw-translate-y 变量，按下时 active 规则胜出会让按钮瞬移半个身位
          逃出光标，click 落到外层 Trigger 上变成切换会话 */}
      <Button
        variant="ghost"
        size="icon-sm"
        className={cn(
          "aui-thread-list-item-delete",
          "absolute end-1 top-1 opacity-0 transition-opacity",
          "group-hover/item:opacity-100 focus-visible:opacity-100",
          "max-md:top-0.5 max-md:size-8 max-md:opacity-100",
        )}
        aria-label="Delete conversation"
        onClick={() => onRequestDelete(itemId, itemTitle ?? "New Chat")}
      >
        <Trash2Icon className="size-3.5" />
      </Button>
    </ThreadListItemPrimitive.Root>
  );
};
