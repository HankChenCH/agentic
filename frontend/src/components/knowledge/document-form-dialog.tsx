import { useEffect, useState, type FC } from "react";

import { useDialogSubmit } from "@/components/shared/use-dialog-submit";
import { DialogFormFooter } from "@/components/shared/dialog-footer";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { BackendKnowledgeDocument } from "@/services/types";

/**
 * 编辑文档元数据弹窗。
 *
 * 字段约束镜像后端 KnowledgeDocumentUpdateRequest：
 *   name ≤250；description ≤500；weight 整数。
 * doc_path 不可变（更换文件需删除后重新上传），因此这里只改元数据。
 */

const NAME_MAX = 250;
const DESCRIPTION_MAX = 500;

export interface DocumentFormValues {
  name: string;
  description: string;
  weight: number;
}

interface DocumentFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 编辑目标文档 */
  document: BackendKnowledgeDocument | null;
  /** 提交（已通过前端校验）；返回 true 才关闭弹窗 */
  onSubmit: (values: DocumentFormValues) => Promise<boolean>;
}

export const DocumentFormDialog: FC<DocumentFormDialogProps> = ({
  open,
  onOpenChange,
  document,
  onSubmit,
}) => {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [weight, setWeight] = useState("0");
  const [nameError, setNameError] = useState<string | null>(null);
  const [weightError, setWeightError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(document?.name ?? "");
    setDescription(document?.description ?? "");
    setWeight(String(document?.weight ?? 0));
    setNameError(null);
    setWeightError(null);
  }, [open, document]);

  const { submitting, handleSubmit } = useDialogSubmit(async () => {
    const trimmedName = name.trim();
    const nextNameError = !trimmedName
      ? "请输入文档名称"
      : trimmedName.length > NAME_MAX
        ? `名称不能超过 ${NAME_MAX} 个字符`
        : null;
    const weightNum = Number(weight);
    const nextWeightError =
      weight === "" || !Number.isInteger(weightNum)
        ? "权重需为整数"
        : null;

    setNameError(nextNameError);
    setWeightError(nextWeightError);
    if (nextNameError || nextWeightError) return null;

    return onSubmit({
      name: trimmedName,
      description: description.trim(),
      weight: weightNum,
    });
  }, onOpenChange);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑文档信息</DialogTitle>
          <DialogDescription>
            仅更新元数据；更换文件请删除后重新上传。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label htmlFor="doc-name">名称</Label>
            <Input
              id="doc-name"
              value={name}
              maxLength={NAME_MAX}
              aria-invalid={nameError != null}
              onChange={(e) => setName(e.target.value)}
            />
            {nameError && (
              <p className="text-xs text-destructive">{nameError}</p>
            )}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="doc-description">描述</Label>
            <Textarea
              id="doc-description"
              value={description}
              rows={3}
              maxLength={DESCRIPTION_MAX}
              placeholder="文档内容说明（可选）"
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="doc-weight">权重</Label>
            <Input
              id="doc-weight"
              type="number"
              value={weight}
              aria-invalid={weightError != null}
              onChange={(e) => setWeight(e.target.value)}
            />
            {weightError && (
              <p className="text-xs text-destructive">{weightError}</p>
            )}
          </div>

          <DialogFormFooter submitting={submitting} label="保存" />
        </form>
      </DialogContent>
    </Dialog>
  );
};
