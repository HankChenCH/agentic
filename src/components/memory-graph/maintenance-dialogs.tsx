import { useEffect, useState, type FC, type FormEvent } from "react";
import { Loader2Icon, SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  conversationService,
} from "@/services/conversation-service";
import type {
  PurgePreview,
  PurgeRequest,
  PurgeResult,
} from "@/services/memory-service";

/**
 * 危险操作区弹窗（L4）：当日清除 / 按会话遗忘 / 整体重置。
 *
 * 共同约定：影响面预览先行（先查询再解锁确认按钮）、destructive 红色
 * 确认、提交经 onSubmit（返回 true 才关闭）。时区约定：自然日起止由
 * 前端按本地时区折算成 ISO 时刻传给后端，服务端不做时区假设。
 */

const todayLocalDate = (): string => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
};

/** 本地日期 → [当日 00:00, 23:59:59] 的 ISO 时刻（带本机时区偏移） */
const dayRangeIso = (date: string): { from: string; to: string } => {
  const [y, m, d] = date.split("-").map(Number);
  const from = new Date(y, m - 1, d, 0, 0, 0, 0);
  const to = new Date(y, m - 1, d, 23, 59, 59, 999);
  const iso = (dt: Date) =>
    new Date(dt.getTime() - dt.getTimezoneOffset() * 60_000).toISOString().slice(0, 19);
  return { from: iso(from), to: iso(to) };
};

const PurgePreviewBox: FC<{ preview: PurgePreview | null; loading: boolean }> = ({
  preview,
  loading,
}) => {
  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border/60 bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
        <Loader2Icon className="size-3.5 animate-spin" />
        正在查询影响面…
      </div>
    );
  }
  if (!preview) return null;
  return (
    <div className="rounded-lg border border-border/60 bg-muted/50 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
      将归档 <span className="font-medium text-foreground">{preview.statements}</span> 条事实
      （其中在效 {preview.activeStatements} 条，历史行原样保留、时点回放仍可溯）、
      删除 <span className="font-medium text-foreground">{preview.episodes}</span> 个事件
      （不可恢复）
      {preview.entities > 0 && (
        <>
          、清理 <span className="font-medium text-foreground">{preview.entities}</span> 个不再被引用的孤立实体
        </>
      )}
      。
    </div>
  );
};

// ---------------- 当日清除 ----------------

interface DayPurgeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 预览 */
  onPreview: (payload: PurgeRequest) => Promise<PurgePreview | null>;
  /** 提交；返回结果才关闭弹窗 */
  onSubmit: (payload: PurgeRequest) => Promise<PurgeResult | null>;
  confirmText?: string;
}

export const DayPurgeDialog: FC<DayPurgeDialogProps> = ({
  open,
  onOpenChange,
  onPreview,
  onSubmit,
  confirmText = "清除",
}) => {
  const [date, setDate] = useState(todayLocalDate());
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setDate(todayLocalDate());
      setPreview(null);
    }
  }, [open]);

  const payload: PurgeRequest | null = date
    ? { scope: "day", ...dayRangeIso(date) }
    : null;

  const handlePreview = async () => {
    if (!payload) return;
    setPreviewing(true);
    try {
      setPreview(await onPreview(payload));
    } finally {
      setPreviewing(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!payload || !preview) return;
    setSubmitting(true);
    try {
      const result = await onSubmit(payload);
      if (result !== null) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>当日清除</DialogTitle>
          <DialogDescription>
            归档所选自然日沉淀的全部事实（软删，时点回放仍可追溯），删除当日事件
            （不可恢复），并清理因此不再被引用的孤立实体。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label htmlFor="purge-date">清除哪一天？</Label>
            <div className="flex items-center gap-2">
              <Input
                id="purge-date"
                type="date"
                className="flex-1"
                value={date}
                onChange={(e) => {
                  setDate(e.target.value);
                  setPreview(null);
                }}
              />
              <Button
                type="button"
                variant="outline"
                disabled={!date || previewing}
                onClick={() => void handlePreview()}
              >
                {previewing ? <Loader2Icon className="animate-spin" /> : <SearchIcon />}
                查询影响
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              按本机时区折算当日 00:00–23:59:59。
            </p>
          </div>

          <PurgePreviewBox preview={preview} loading={previewing} />

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" variant="destructive" disabled={!preview || submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              {confirmText}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

// ---------------- 按会话遗忘 ----------------

interface ThreadForgetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 预览 */
  onPreview: (payload: PurgeRequest) => Promise<PurgePreview | null>;
  /** 提交；返回结果才关闭弹窗 */
  onSubmit: (payload: PurgeRequest) => Promise<PurgeResult | null>;
  confirmText?: string;
}

interface ConversationOption {
  threadId: string;
  title: string;
}

export const ThreadForgetDialog: FC<ThreadForgetDialogProps> = ({
  open,
  onOpenChange,
  onPreview,
  onSubmit,
  confirmText = "遗忘",
}) => {
  const [conversations, setConversations] = useState<ConversationOption[]>([]);
  const [threadId, setThreadId] = useState("");
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setThreadId("");
    setPreview(null);
    setConversations([]);
    // 弹窗打开时拉取会话清单（管理页不挂聊天 runtime，直接走 REST）
    void conversationService
      .listConversations(1, 100)
      .then((result) => {
        setConversations(
          result.items.map((c) => ({
            threadId: c.thread_id,
            title: c.conversation_title || c.thread_id.slice(0, 8),
          })),
        );
      })
      .catch(() => setConversations([]));
  }, [open]);

  const items: Record<string, string> = Object.fromEntries(
    conversations.map((c) => [c.threadId, c.title]),
  );

  const handlePreview = async () => {
    if (!threadId) return;
    setPreviewing(true);
    try {
      setPreview(await onPreview({ scope: "thread", threadId }));
    } finally {
      setPreviewing(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!threadId || !preview) return;
    setSubmitting(true);
    try {
      const result = await onSubmit({ scope: "thread", threadId });
      if (result !== null) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>按会话遗忘</DialogTitle>
          <DialogDescription>
            归档某次会话沉淀的全部事实（软删可追溯），删除其事件（不可恢复）。
            实体是跨会话的，不会被本操作删除。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label>遗忘哪次会话？</Label>
            <div className="flex items-center gap-2">
              <Select
                items={items}
                value={threadId || null}
                onValueChange={(value) => {
                  setThreadId(value ?? "");
                  setPreview(null);
                }}
              >
                <SelectTrigger className="w-full flex-1" aria-label="选择会话">
                  <SelectValue placeholder={conversations.length ? "选择会话" : "暂无会话"} />
                </SelectTrigger>
                <SelectContent>
                  {conversations.map((c) => (
                    <SelectItem key={c.threadId} value={c.threadId}>
                      {c.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                variant="outline"
                disabled={!threadId || previewing}
                onClick={() => void handlePreview()}
              >
                {previewing ? <Loader2Icon className="animate-spin" /> : <SearchIcon />}
                查询影响
              </Button>
            </div>
          </div>

          <PurgePreviewBox preview={preview} loading={previewing} />

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" variant="destructive" disabled={!preview || submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              {confirmText}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

// ---------------- 整体重置 ----------------

const RESET_WORD = "重置";

interface ResetMemoryDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 确认提交（导出已在其内完成）；返回 true 才关闭弹窗 */
  onConfirm: (exportFirst: boolean) => Promise<boolean>;
}

export const ResetMemoryDialog: FC<ResetMemoryDialogProps> = ({
  open,
  onOpenChange,
  onConfirm,
}) => {
  const [confirmText, setConfirmText] = useState("");
  const [exportChecked, setExportChecked] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setConfirmText("");
      setExportChecked(true);
    }
  }, [open]);

  const ready = confirmText.trim() === RESET_WORD;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    try {
      const ok = await onConfirm(exportChecked);
      if (ok) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>整体重置记忆</DialogTitle>
          <DialogDescription>
            清空全部长期记忆（实体/事实/事件/参与）并重建检索索引。此操作
            不可恢复——建议先导出 JSON 备份。时点回放能力随之清空。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-border/60 px-3 py-2 text-xs hover:bg-muted/60">
            <input
              type="checkbox"
              checked={exportChecked}
              onChange={(e) => setExportChecked(e.target.checked)}
              className="accent-[var(--primary)]"
            />
            重置前导出 JSON 备份（推荐）
          </label>

          <div className="grid gap-2">
            <Label htmlFor="reset-confirm">
              请输入「{RESET_WORD}」以确认
            </Label>
            <Input
              id="reset-confirm"
              value={confirmText}
              placeholder={RESET_WORD}
              onChange={(e) => setConfirmText(e.target.value)}
            />
          </div>

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" variant="destructive" disabled={!ready || submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              重置全部记忆
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};
