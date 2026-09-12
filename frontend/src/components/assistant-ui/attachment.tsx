
import { type PropsWithChildren, useEffect, useState, type FC } from "react";
import {
  XIcon,
  PlusIcon,
  FileText,
  Loader2Icon,
  AlertCircleIcon,
} from "lucide-react";
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  useAuiState,
  useAui,
} from "@assistant-ui/react";
import { useShallow } from "zustand/shallow";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Avatar,
  AvatarImage,
  AvatarFallback,
} from "@/components/ui/avatar";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { isAttachmentRef } from "@/lib/attachment-url";
import { resolveDisplayUrl } from "@/lib/attachment-display-url";
import { cn } from "@/lib/utils";

const useFileSrc = (file: File | undefined) => {
  const [src, setSrc] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!file) {
      setSrc(undefined);
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    setSrc(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  return src;
};

// 本域附件引用判定与 URL 拼接在 lib/attachment-url.ts，预签名换址 + TTL
// 缓存 + blob 降级在 lib/attachment-display-url.ts（均为可单测的纯模块）：
// REST_BASE 为相对形态（同源反代部署）时不能走 new URL(ref, REST_BASE)，
// 否则 URL 构造器抛错、判域恒 false、换签永不发生（生产裂图根因）。

/**
 * 本域附件引用 → 可展示 src（薄壳）：解析逻辑在
 * lib/attachment-display-url.ts 的 resolveDisplayUrl，这里只保留 React
 * 侧的加载态与卸载竞态（cancelled）处理。非本域引用（外部 http(s) /
 * data URL）原样透传，解析失败回落 undefined（缩略图显示图标），
 * 不打断消息渲染。
 */
const useAttachmentDisplaySrc = (ref: string | undefined) => {
  const [src, setSrc] = useState<string | undefined>(ref);

  useEffect(() => {
    if (!ref || !isAttachmentRef(ref)) {
      setSrc(ref);
      return;
    }
    let cancelled = false;
    setSrc(undefined);
    resolveDisplayUrl(ref).then((resolved) => {
      if (cancelled || !resolved) return;
      setSrc(resolved);
    });
    return () => {
      cancelled = true;
    };
  }, [ref]);

  return src;
};

const useAttachmentSrc = () => {
  const { file, src } = useAuiState(
    useShallow((s): { file?: File; src?: string } => {
      if (s.attachment.type !== "image") return {};
      if (s.attachment.file) return { file: s.attachment.file };
      const image = s.attachment.content?.filter((c) => c.type === "image")[0]
        ?.image;
      if (!image) return {};
      return { src: image };
    }),
  );

  const displaySrc = useAttachmentDisplaySrc(src);
  return useFileSrc(file) ?? displaySrc;
};

type AttachmentPreviewProps = {
  src: string;
};

const AttachmentPreview: FC<AttachmentPreviewProps> = ({ src }) => {
  const [isLoaded, setIsLoaded] = useState(false);
  return (
    <img
      src={src}
      alt="Attachment preview"
      className={cn(
        "block h-auto max-h-[80vh] w-auto max-w-full object-contain",
        isLoaded
          ? "aui-attachment-preview-image-loaded"
          : "aui-attachment-preview-image-loading invisible",
      )}
      onLoad={() => setIsLoaded(true)}
    />
  );
};

const AttachmentPreviewDialog: FC<PropsWithChildren> = ({ children }) => {
  const src = useAttachmentSrc();

  if (!src) return children;

  return (
    <Dialog>
      <DialogTrigger
        className="aui-attachment-preview-trigger hover:bg-accent/50 cursor-pointer transition-colors"
      >
        {children}
      </DialogTrigger>
      <DialogContent className="aui-attachment-preview-dialog-content [&>button]:bg-foreground/60 [&_svg]:text-background [&>button]:hover:[&_svg]:text-destructive p-2 sm:max-w-3xl [&>button]:rounded-full [&>button]:p-1 [&>button]:opacity-100 [&>button]:ring-0!">
        <DialogTitle className="aui-sr-only sr-only">
          Image Attachment Preview
        </DialogTitle>
        <div className="aui-attachment-preview bg-background relative mx-auto flex max-h-[80dvh] w-full items-center justify-center overflow-hidden">
          <AttachmentPreview src={src} />
        </div>
      </DialogContent>
    </Dialog>
  );
};

const AttachmentThumb: FC = () => {
  const src = useAttachmentSrc();

  return (
    <Avatar className="aui-attachment-tile-avatar h-full w-full rounded-none">
      <AvatarImage
        src={src}
        alt="Attachment preview"
        className="aui-attachment-tile-image object-cover"
      />
      <AvatarFallback>
        <FileText className="aui-attachment-tile-fallback-icon text-muted-foreground size-8" />
      </AvatarFallback>
    </Avatar>
  );
};

const AttachmentUI: FC = () => {
  const aui = useAui();
  const isComposer = aui.attachment.source !== "message";

  const isImage = useAuiState((s) => s.attachment.type === "image");
  const typeLabel = useAuiState((s) => {
    const type = s.attachment.type;
    switch (type) {
      case "image":
        return "Image";
      case "document":
        return "Document";
      case "file":
        return "File";
      default:
        return type;
    }
  });

  const uploadState = useAuiState((s) =>
    s.attachment.status.type === "running"
      ? "uploading"
      : s.attachment.status.type === "incomplete" &&
          s.attachment.status.reason === "error"
        ? "error"
        : undefined,
  );
  const isUploading = uploadState === "uploading";
  const isError = uploadState === "error";

  const errorMessage = useAuiState((s) =>
    s.attachment.status.type === "incomplete" &&
    s.attachment.status.reason === "error"
      ? "Upload failed"
      : undefined,
  );

  return (
    <Tooltip>
      <AttachmentPrimitive.Root
        className={cn(
          "aui-attachment-root relative",
          isImage &&
            !isComposer &&
            "aui-attachment-root-message only:*:first:size-24",
        )}
      >
        <AttachmentPreviewDialog>
          <TooltipTrigger render={<div className={cn(
                                  "aui-attachment-tile bg-muted relative size-14 cursor-pointer overflow-hidden rounded-[calc(var(--composer-radius)-var(--composer-padding))] border transition-opacity hover:opacity-75",
                                  isError && "border-destructive",
                                )} role="button" tabIndex={0} aria-label={`${typeLabel} attachment${
                                  isError ? ", upload failed" : isUploading ? ", uploading" : ""
                                }`} />}><AttachmentThumb />{isUploading && (
                                  <div
                                    aria-hidden="true"
                                    className="aui-attachment-tile-uploading bg-background/60 absolute inset-0 flex items-center justify-center backdrop-blur-[1px]"
                                  >
                                    <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
                                  </div>
                                )}{isError && (
                                  <div
                                    aria-hidden="true"
                                    className="aui-attachment-tile-error bg-destructive/10 absolute inset-0 flex items-center justify-center"
                                  >
                                    <AlertCircleIcon className="text-destructive size-5" />
                                  </div>
                                )}</TooltipTrigger>
        </AttachmentPreviewDialog>
        {isComposer && <AttachmentRemove />}
      </AttachmentPrimitive.Root>
      <TooltipContent side="top">
        <AttachmentPrimitive.Name />
        {errorMessage && (
          <p className="aui-attachment-error-message">{errorMessage}</p>
        )}
      </TooltipContent>
    </Tooltip>
  );
};

const AttachmentRemove: FC = () => {
  return (
    <AttachmentPrimitive.Remove render={<TooltipIconButton tooltip="移除附件" className="aui-attachment-tile-remove text-muted-foreground hover:[&_svg]:text-destructive absolute end-1.5 top-1.5 size-3.5 rounded-full bg-white opacity-100 shadow-sm hover:bg-white! [&_svg]:text-black max-md:size-6" side="top" />}><XIcon className="aui-attachment-remove-icon size-3 dark:stroke-[2.5px]" /></AttachmentPrimitive.Remove>
  );
};

export const UserMessageAttachments: FC = () => {
  return (
    <div className="aui-user-message-attachments-end col-span-full col-start-1 row-start-1 flex w-full flex-row justify-end gap-2">
      <MessagePrimitive.Attachments>
        {() => <AttachmentUI />}
      </MessagePrimitive.Attachments>
    </div>
  );
};

export const ComposerAttachments: FC = () => {
  return (
    <div className="aui-composer-attachments flex w-full flex-row items-center gap-2 overflow-x-auto empty:hidden">
      <ComposerPrimitive.Attachments>
        {() => <AttachmentUI />}
      </ComposerPrimitive.Attachments>
    </div>
  );
};

export const ComposerAddAttachment: FC = () => {
  return (
    <ComposerPrimitive.AddAttachment render={<TooltipIconButton tooltip="添加附件" side="bottom" variant="ghost" size="icon" className="aui-composer-add-attachment hover:bg-muted-foreground/15 dark:border-muted-foreground/15 dark:hover:bg-muted-foreground/30 size-7 rounded-full p-1 text-xs font-semibold max-md:size-9" aria-label="添加附件" />}><PlusIcon className="aui-attachment-add-icon size-4.5 stroke-[1.5px]" /></ComposerPrimitive.AddAttachment>
  );
};
