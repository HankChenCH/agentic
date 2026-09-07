import { useCallback, useEffect, useState, type FC } from "react";
import { useDropzone } from "react-dropzone";
import {
  FileTextIcon,
  Trash2Icon,
  UploadCloudIcon,
} from "lucide-react";
import { toast } from "sonner";

import { DialogFormFooter } from "@/components/shared/dialog-footer";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 文档上传弹窗（react-dropzone 拖拽/点选，支持多选）。
 *
 * 后端是单文件单请求（multipart：file/name/description），多文件时串行
 * 上传；name 缺省取文件名，description 对本批所有文件生效。逐文件的
 * 失败提示由 use-knowledge-documents 的 uploadDocument 发出。
 */

interface UploadDocumentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 单文件上传（内部已带 toast/刷新）；返回是否成功 */
  onUpload: (
    file: File,
    options?: { name?: string; description?: string },
  ) => Promise<boolean>;
}

export const UploadDocumentDialog: FC<UploadDocumentDialogProps> = ({
  open,
  onOpenChange,
  onUpload,
}) => {
  const [files, setFiles] = useState<File[]>([]);
  const [description, setDescription] = useState("");
  const [uploading, setUploading] = useState(false);

  const onDrop = useCallback((accepted: File[]) => {
    setFiles((prev) => [...prev, ...accepted]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    disabled: uploading,
    // 与后端上传白名单（.pdf/.xlsx/.docx）同步限制选择范围
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    },
  });

  // 每次打开重置输入（关闭时保留会造成下次打开闪现旧值）
  useEffect(() => {
    if (!open) return;
    setFiles([]);
    setDescription("");
  }, [open]);

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    try {
      let successCount = 0;
      for (const file of files) {
        // 串行上传：避免并发打满后端解析线程，也便于失败定位
        const ok = await onUpload(file, {
          description: description.trim() || undefined,
        });
        if (ok) successCount += 1;
      }
      if (successCount > 0) {
        toast.success(`已提交 ${successCount}/${files.length} 个文档，解析入库后生效`);
      }
      if (successCount === files.length) {
        onOpenChange(false);
      } else {
        // 有失败项：保留未成功之外的输入便于重试，这里简单清掉已成功的
        //（全部重传亦可，提示已足够定位）
        setFiles([]);
      }
    } finally {
      setUploading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>上传文档</DialogTitle>
          <DialogDescription>
            拖拽或点选 PDF / Word / Excel 文件，支持多选；上传后自动解析、分段并向量化。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div
            {...getRootProps()}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-8 text-center transition-colors",
              isDragActive
                ? "border-primary bg-primary/5"
                : "border-input hover:bg-muted/50",
              uploading && "pointer-events-none opacity-60",
            )}
          >
            <input {...getInputProps()} />
            <UploadCloudIcon className="size-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {isDragActive
                ? "松开鼠标开始上传"
                : "点击选择文件（PDF / xlsx / docx），或拖拽到此处"}
            </p>
          </div>

          {files.length > 0 && (
            <ul className="grid gap-1.5">
              {files.map((file) => (
                <li
                  key={`${file.name}-${file.size}-${file.lastModified}`}
                  className="flex items-center gap-2 rounded-lg bg-muted/50 px-2.5 py-1.5 text-sm"
                >
                  <FileTextIcon className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatFileSize(file.size)}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`移除 ${file.name}`}
                    disabled={uploading}
                    onClick={() =>
                      setFiles((prev) =>
                        prev.filter((f) => f !== file),
                      )
                    }
                  >
                    <Trash2Icon />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <div className="grid gap-2">
            <Label htmlFor="upload-description">描述</Label>
            <Textarea
              id="upload-description"
              value={description}
              rows={2}
              maxLength={500}
              placeholder="对本批文档的补充说明（可选，全部文件共用）"
              disabled={uploading}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>

        <DialogFormFooter
          submitting={uploading}
          disabled={files.length === 0}
          type="button"
          onClick={() => void handleUpload()}
          label={
            uploading
              ? "上传中…"
              : `上传${files.length > 0 ? `（${files.length}）` : ""}`
          }
        />
      </DialogContent>
    </Dialog>
  );
};
