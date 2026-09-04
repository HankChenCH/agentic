
import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/attachment";
import { AgentModeSwitch } from "@/components/assistant-ui/agent-selector";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/reasoning";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { isTailTurnMessage } from "@/components/assistant-ui/branch-picker-gate";
import { Button } from "@/components/ui/button";
import { useConversationActions } from "@/hooks/use-conversation-list";
import { conversationService } from "@/services/conversation-service";
import { useAgentStore } from "@/stores/agent-store";
import { cn } from "@/lib/utils";
import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  BranchPickerPrimitive,
  ComposerPrimitive,
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartComponent,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CopyIcon,
  DownloadIcon,
  HistoryIcon,
  MicIcon,
  MoreHorizontalIcon,
  PencilIcon,
  RefreshCwIcon,
  SparklesIcon,
  SquareIcon,
} from "lucide-react";
import {
  createContext,
  useContext,
  useEffect,
  type ComponentType,
  type FC,
  type PropsWithChildren,
} from "react";

export type ThreadGroupPart = MessagePrimitive.GroupedParts.GroupPart;

/**
 * Optional component overrides for the thread. `AssistantMessage` and
 * `Welcome` replace whole sections; the remaining slots override how the
 * assistant message renders tool calls and part groups. Tool UIs registered
 * by name (toolkit `render`, `useAssistantDataUI`) take precedence over
 * `ToolFallback`.
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
  ToolGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
  ReasoningGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
};

export type ThreadProps = {
  components?: ThreadComponents | undefined;
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext =
  createContext<ThreadComponents>(EMPTY_COMPONENTS);

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);

export const Thread: FC<ThreadProps> = ({ components = EMPTY_COMPONENTS }) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot isEmpty={isEmpty} />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{ isEmpty: boolean }> = ({ isEmpty }) => {
  const { Welcome = ThreadWelcome } = useContext(ThreadComponentsContext);

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root @container flex h-full flex-col"
      style={{
        ["--thread-max-width" as string]: "44rem",
        ["--composer-bg" as string]: "var(--color-card)",
        ["--composer-radius" as string]: "1.5rem",
        ["--composer-padding" as string]: "8px",
      }}
    >
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll scroll-smooth"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-4 pt-4",
            isEmpty && "justify-center",
          )}
        >
          <AuiIf condition={isNewChatView}>
            <Welcome />
          </AuiIf>

          <div
            data-slot="aui_message-group"
            className="mb-14 flex flex-col gap-y-6 empty:hidden"
          >
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer bg-background/80 flex flex-col gap-4 overflow-visible pb-4 backdrop-blur-sm md:pb-6",
              !isEmpty &&
                "sticky bottom-0 mt-auto rounded-t-(--composer-radius)",
            )}
          >
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            {/* 新会话先选智能体：分段 pills 只在无消息视图出现，会话开始后
                不再切换（绑定在会话上，见 agent-selector.tsx 模块注释） */}
            <AuiIf condition={isNewChatView}>
              <AgentModeSwitch />
            </AuiIf>
            <Composer />
            <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC = () => {
  const { AssistantMessage: AssistantMessageComponent = AssistantMessage } =
    useContext(ThreadComponentsContext);
  const role = useAuiState((s) => s.message.role);
  const isEditing = useAuiState((s) => s.message.composer.isEditing);

  if (isEditing) return <EditComposer />;
  if (role === "user") return <UserMessage />;
  return <AssistantMessageComponent />;
};

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom render={<TooltipIconButton tooltip="滚动到底部" variant="outline" className="aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent absolute -top-12 z-10 self-center rounded-full p-4 disabled:invisible" />}><ArrowDownIcon /></ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-6 flex flex-col items-center px-4 text-center">
      {/* 品牌呼应：焦糖橘标志 + 衬线 display 大标题（暖砂编辑部风） */}
      <span className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both mb-5 flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-card">
        <SparklesIcon className="size-6" />
      </span>
      <h1 className="aui-thread-welcome-message-inner fade-in slide-in-from-bottom-1 animate-in fill-mode-both font-heading text-3xl font-semibold tracking-tight duration-200 md:text-4xl">
        今天想聊些什么？
      </h1>
      <p className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both mt-3 text-sm text-muted-foreground duration-300">
        询问任何问题，或在对话中引用知识库内容
      </p>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-wrap items-center justify-center gap-2 px-4">
      <ThreadPrimitive.Suggestions>
        {() => <ThreadSuggestionItem />}
      </ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <SuggestionPrimitive.Trigger send render={<Button variant="ghost" className="aui-thread-welcome-suggestion text-foreground hover:bg-muted border-border/60 h-auto gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-normal whitespace-nowrap transition-colors" />}><SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1" /><SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 empty:hidden" /></SuggestionPrimitive.Trigger>
    </div>
  );
};

const Composer: FC = () => {
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <ComposerPrimitive.AttachmentDropzone render={<div data-slot="aui_composer-shell" className="border-border/70 focus-within:border-primary/40 flex w-full flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) shadow-card transition-[border-color,box-shadow] focus-within:shadow-card-hover data-[dragging=true]:border-dashed data-[dragging=true]:bg-[color-mix(in_oklab,var(--color-accent)_50%,var(--color-background))]" />}><ComposerAttachments /><ComposerPrimitive.Input
                      placeholder="输入消息， Shift + Enter 换行…"
                      className="aui-composer-input caret-primary placeholder:text-muted-foreground/80 max-h-32 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base outline-none"
                      rows={1}
                      enterKeyHint="send"
                      aria-label="消息输入框"
                    /><ComposerAction /></ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  );
};

const ComposerAction: FC = () => {
  // 图片入口按智能体能力显隐：新会话视图读选中智能体的能力目录
  // （未声明 vision 的收不到图片，服务端会降级丢图，入口直接隐藏）；
  // 既有会话绑定对前端不可知，保守显示（服务端兜底降级，行为不变）。
  const isNewChat = useAuiState(isNewChatView);
  const agents = useAgentStore((s) => s.agents);
  const defaultAgentId = useAgentStore((s) => s.defaultAgentId);
  const selectedAgentId = useAgentStore((s) => s.selectedAgentId);
  const activeAgent = agents.find(
    (a) => a.id === (selectedAgentId ?? defaultAgentId),
  );
  const supportsVision = !isNewChat || (activeAgent?.supportsVision ?? true);

  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-between">
      <div className="flex items-center gap-1">
        {supportsVision && <ComposerAddAttachment />}
      </div>
      <div className="flex items-center gap-1.5">
        <AuiIf condition={(s) => s.thread.capabilities.dictation}>
          <AuiIf condition={(s) => s.composer.dictation == null}>
            <ComposerPrimitive.Dictate render={<TooltipIconButton tooltip="语音输入" side="bottom" type="button" variant="ghost" size="icon" className="aui-composer-dictate size-7 rounded-full max-md:size-9" aria-label="开始语音输入" />}><MicIcon className="aui-composer-dictate-icon size-4" /></ComposerPrimitive.Dictate>
          </AuiIf>
          <AuiIf condition={(s) => s.composer.dictation != null}>
            <ComposerPrimitive.StopDictation render={<TooltipIconButton tooltip="停止语音输入" side="bottom" type="button" variant="ghost" size="icon" className="aui-composer-stop-dictation text-destructive size-7 rounded-full max-md:size-9" aria-label="停止语音输入" />}><SquareIcon className="aui-composer-stop-dictation-icon size-3.5 animate-pulse fill-current" /></ComposerPrimitive.StopDictation>
          </AuiIf>
        </AuiIf>
        <AuiIf condition={(s) => !s.thread.isRunning}>
          <ComposerPrimitive.Send render={<TooltipIconButton tooltip="发送消息" side="bottom" type="button" variant="default" size="icon" className="aui-composer-send size-7 rounded-full max-md:size-9" aria-label="发送消息" />}><ArrowUpIcon className="aui-composer-send-icon size-4.5" /></ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={(s) => s.thread.isRunning}>
          <ComposerPrimitive.Cancel render={<Button type="button" variant="default" size="icon" className="aui-composer-cancel size-7 rounded-full max-md:size-9" aria-label="停止生成" />}><SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" /></ComposerPrimitive.Cancel>
        </AuiIf>
      </div>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-destructive bg-destructive/10 text-destructive dark:bg-destructive/5 mt-2 rounded-md border p-3 text-sm dark:text-red-200">
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

const AssistantMessage: FC = () => {
  const {
    ToolFallback: ToolFallbackComponent = ToolFallback,
    ToolGroup,
    ReasoningGroup,
  } = useContext(ThreadComponentsContext);

  const ACTION_BAR_PT = "pt-1.5";
  // Keep the action bar inside the contained root's paint box, then cancel its reserved space in flow.
  const ACTION_BAR_HEIGHT = `min-h-7.5 ${ACTION_BAR_PT}`;

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative -mb-7.5 pb-7.5 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="text-foreground px-2 leading-relaxed wrap-break-word"
      >
        <MessagePrimitive.GroupedParts
          groupBy={groupPartByType({
            reasoning: ["group-chainOfThought", "group-reasoning"],
            "tool-call": ["group-chainOfThought", "group-tool"],
            "standalone-tool-call": [],
          })}
        >
          {({ part, children }) => {
            switch (part.type) {
              case "group-chainOfThought":
                return <div data-slot="aui_chain-of-thought">{children}</div>;
              case "group-tool":
                if (ToolGroup) {
                  return <ToolGroup group={part}>{children}</ToolGroup>;
                }
                return (
                  <ToolGroupRoot variant="ghost">
                    <ToolGroupTrigger
                      count={part.indices.length}
                      active={part.status.type === "running"}
                    />
                    <ToolGroupContent>{children}</ToolGroupContent>
                  </ToolGroupRoot>
                );
              case "group-reasoning": {
                if (ReasoningGroup) {
                  return (
                    <ReasoningGroup group={part}>{children}</ReasoningGroup>
                  );
                }
                const running = part.status.type === "running";
                return (
                  <ReasoningRoot streaming={running}>
                    <ReasoningTrigger active={running} />
                    <ReasoningContent aria-busy={running}>
                      <ReasoningText>{children}</ReasoningText>
                    </ReasoningContent>
                  </ReasoningRoot>
                );
              }
              case "text":
                return <MarkdownText />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return part.toolUI ?? <ToolFallbackComponent {...part} />;
              case "data":
                return part.dataRendererUI;
              case "indicator":
                return (
                  <span
                    data-slot="aui_assistant-message-indicator"
                    className="animate-pulse font-sans"
                    aria-label="助手正在回复"
                  >
                    {"●"}
                  </span>
                );
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessageError />
      </div>

      <div
        data-slot="aui_assistant-message-footer"
        className={cn("ms-2 flex items-center", ACTION_BAR_HEIGHT)}
      >
        <BranchPicker />
        <AssistantActionBar />
      </div>
    </MessagePrimitive.Root>
  );
};

/**
 * 分支基点信号：重新生成/编辑在触发时经 RunConfig.custom 携带 branchBaseMessageId，
 * runtime 组装请求时落在 forwardedProps.runConfig，由 agentic-runtime 的
 * prepareRunAgentInput 覆写提升为 forwardedProps.branch（后端 open_turn 据此把
 * 新轮次定位为基点轮次的兄弟变体，即同一问答的重试/编辑分支）。
 */
const BRANCH_BASE_KEY = "branchBaseMessageId";

/** 编辑基点：被编辑问题**之后**最近的 assistant 消息——即该轮自己的回答
 * （流式注入 id 与库中 message_id 同源）。服务端把新轮次挂为"基点所在轮次"
 * 的兄弟变体，因此基点必须是目标轮次本身而非其前驱。取不到（失败轮无
 * 回答）时返回 undefined —— 不带信号，服务端走「编辑最新一轮」自动检测兜底。 */
const useEditBranchBaseId = (): string | undefined =>
  useAuiState((s) =>
    s.thread.messages
      .slice(s.message.index + 1)
      .find((m) => m.role === "assistant")?.id,
  );

const AssistantActionBar: FC = () => {
  // 重新生成带分支信号：本条 assistant 消息 id 即其所属轮次的定位键。不能用
  // ActionBarPrimitive.Reload —— 其 hook 无参，带不了 runConfig；运行中禁用
  // 由外层 ActionBarPrimitive.Root 的 hideWhenRunning 承担，行为不变。
  const aui = useAui();
  const messageId = useAuiState((s) => s.message.id);
  // 末梢锁：重新生成只出现在最后一条消息（定型节点不可再开分支）
  const isLast = useAuiState((s) => s.message.isLast);
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-assistant-action-bar-root text-muted-foreground animate-in fade-in col-start-3 row-start-2 -ms-1 flex gap-1 duration-200"
    >
      <ActionBarPrimitive.Copy render={<TooltipIconButton tooltip="复制" />}><AuiIf condition={(s) => s.message.isCopied}>
                      <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
                    </AuiIf><AuiIf condition={(s) => !s.message.isCopied}>
                      <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
                    </AuiIf></ActionBarPrimitive.Copy>
      {/* 重新生成只在末梢可用（同编辑：定型节点不支持再开分支），复制/导出不受限 */}
      {isLast && (
        <TooltipIconButton
          tooltip="重新生成"
          onClick={() =>
            aui
              .message()
              .reload({ runConfig: { custom: { [BRANCH_BASE_KEY]: messageId } } })
          }
        >
          <RefreshCwIcon />
        </TooltipIconButton>
      )}
      <ActionBarMorePrimitive.Root>
        <ActionBarMorePrimitive.Trigger render={<TooltipIconButton tooltip="更多" className="data-[state=open]:bg-accent" />}><MoreHorizontalIcon /></ActionBarMorePrimitive.Trigger>
        <ActionBarMorePrimitive.Content
          side="bottom"
          align="start"
          sideOffset={6}
          className="aui-action-bar-more-content bg-popover/95 text-popover-foreground data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:animate-out data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 min-w-[8rem] overflow-hidden rounded-xl border p-1.5 shadow-lg backdrop-blur-sm"
        >
          <ActionBarPrimitive.ExportMarkdown render={<ActionBarMorePrimitive.Item className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none" />}><DownloadIcon className="size-4" />导出 Markdown
                              </ActionBarPrimitive.ExportMarkdown>
        </ActionBarMorePrimitive.Content>
      </ActionBarMorePrimitive.Root>
    </ActionBarPrimitive.Root>
  );
};

const UserMessage: FC = () => {
  // 「已更新」标记：该问答被重新生成/编辑过（attempt_no > 1，历史加载时算好
  // 的消息 id 集合经 context 下发）。刷新后仍可见，作为无分支切换时的替代指示。
  const { updatedMessageIds } = useConversationActions();
  const messageId = useAuiState((s) => s.message.id);
  const isUpdated = messageId != null && updatedMessageIds.has(messageId);

  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        {isUpdated && (
          <span
            data-slot="aui-user-message-updated"
            className="text-muted-foreground mb-1 flex items-center gap-1 text-xs"
          >
            <HistoryIcon className="size-3" />
            已更新
          </span>
        )}
        <div className="aui-user-message-content peer rounded-2xl bg-primary/10 px-4 py-2.5 text-foreground wrap-break-word empty:hidden">
          <MessagePrimitive.Parts />
        </div>
        <div className="aui-user-action-bar-wrapper absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 peer-empty:hidden rtl:translate-x-full">
          <UserActionBar />
        </div>
      </div>

      <BranchPicker
        data-slot="aui_user-branch-picker"
        className="col-span-full col-start-1 row-start-3 -me-1 justify-end"
      />
    </MessagePrimitive.Root>
  );
};

const UserActionBar: FC = () => {
  // 编辑只在末梢（最后一条消息）可用：续聊定型后历史节点冻结，不支持在
  // 旧节点上开分支（服务端 activate/base 守卫兜底，这里从入口隐藏）
  const isLast = useAuiState((s) => s.message.isLast);
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-user-action-bar-root flex flex-col items-end"
    >
      {isLast && (
        <ActionBarPrimitive.Edit render={<TooltipIconButton tooltip="编辑" className="aui-user-action-edit" />}><PencilIcon /></ActionBarPrimitive.Edit>
      )}
    </ActionBarPrimitive.Root>
  );
};

const EditComposer: FC = () => {
  // 编辑保存 = 对被编辑问题轮次的兄弟变体重跑：挂载时把基点（前驱 assistant
  // 消息 id）写进 composer runConfig，send() 会随消息带给 runtime（append →
  // startRun → forwardedProps.runConfig → 提升为 forwardedProps.branch）。
  // 首条消息编辑（无前驱）不带信号，服务端走「编辑最新一轮」自动检测兜底。
  const aui = useAui();
  const branchBaseId = useEditBranchBaseId();
  useEffect(() => {
    if (!branchBaseId) return;
    aui
      .message()
      .composer()
      .setRunConfig({ custom: { [BRANCH_BASE_KEY]: branchBaseId } });
  }, [aui, branchBaseId]);

  return (
    <MessagePrimitive.Root
      data-slot="aui_edit-composer-wrapper"
      className="flex flex-col px-2 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <ComposerPrimitive.Root className="aui-edit-composer-root border-border/70 ms-auto flex w-full max-w-[85%] flex-col rounded-(--composer-radius) border bg-(--composer-bg) shadow-card">
        <ComposerPrimitive.Input
          className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-base outline-none"
          autoFocus
        />
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel render={<Button variant="ghost" size="sm" className="h-8 rounded-full px-3.5" />}>取消
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send render={<Button size="sm" className="h-8 rounded-full px-3.5" />}>更新
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};

/** 变体切换单键：调用运行时切到相邻分支，并把服务端活跃叶子同步到目标
 * 轮次（POST activate-turn）——「切换即对比基准移动」，后续对话与多轮
 * 回放都沿所选分支。目标消息映射不到轮次（极端时序）时只做视觉切换。 */
const BranchPickButton: FC<{ direction: "previous" | "next" }> = ({
  direction,
}) => {
  const aui = useAui();
  const { turnByMessageId, currentThreadId } = useConversationActions();
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const atBound = useAuiState((s) =>
    direction === "previous"
      ? s.message.branchNumber <= 1
      : s.message.branchNumber >= s.message.branchCount,
  );

  const switchBranch = () => {
    aui.message().switchToBranch({ position: direction });
    // 仓库切换是同步的，但 aui 状态快照要等 React 提交后才更新 —— 立即读
    // 末梢会拿到切换前的旧消息，activate-turn 会映射失败。推迟到下一个
    // 宏任务（通知已 flush）再读。
    window.setTimeout(() => {
      const tail = aui.thread().getState().messages.at(-1);
      const turnId = tail ? turnByMessageId.get(tail.id) : undefined;
      if (turnId && currentThreadId) {
        void conversationService
          .activateTurn(currentThreadId, turnId)
          .catch(() => {});
      }
    }, 0);
  };

  return (
    <TooltipIconButton
      tooltip={direction === "previous" ? "上一个分支" : "下一个分支"}
      disabled={isRunning || atBound}
      onClick={switchBranch}
    >
      {direction === "previous" ? (
        <ChevronLeftIcon />
      ) : (
        <ChevronRightIcon />
      )}
    </TooltipIconButton>
  );
};

const BranchPicker: FC<BranchPickerPrimitive.Root.Props> = ({
  className,
  ...rest
}) => {
  // 末梢锁（"续聊即定型"）：仅末梢轮次允许切换变体 —— 末梢 assistant
  // （isLast，重新生成型扇形挂回答下方）与末梢 user（其后仅剩末梢回答，
  // 编辑型扇形挂问题行）。沿所选分支发出新消息后该节点不再是末梢，切换
  // 入口随之消失，历史由此保持线性。判定逻辑见 branch-picker-gate.ts。
  const isTailTurn = useAuiState((s) =>
    isTailTurnMessage(
      s.message.isLast,
      s.message.index,
      s.thread.messages.length,
    ),
  );
  if (!isTailTurn) return null;
  return (
    <BranchPickerPrimitive.Root
      hideWhenSingleBranch
      className={cn(
        "aui-branch-picker-root text-muted-foreground -ms-2 me-2 inline-flex items-center text-xs",
        className,
      )}
      {...rest}
    >
      <BranchPickButton direction="previous" />
      <span className="aui-branch-picker-state font-medium">
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickButton direction="next" />
    </BranchPickerPrimitive.Root>
  );
};
