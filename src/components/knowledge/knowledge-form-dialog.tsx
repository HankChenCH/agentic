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
import type { BackendKnowledgeBase } from "@/services/types";

/**
 * 新建/编辑知识库共用表单弹窗。
 *
 * 字段约束镜像后端 KnowledgeBaseCreateRequest：
 *   name 必填 ≤25；description ≤500；weight 整数（越大越靠前）。
 * embedding_model 由后端建库时从 llm.yaml 取默认值，前端不采集。
 * 受控组件 + 行内校验，不引入表单库（字段少，保持项目轻量模式）。
 */

const NAME_MAX = 25;
const DESCRIPTION_MAX = 500;

export interface KnowledgeFormValues {
  name: string;
  description: string;
  weight: number;
}

interface KnowledgeFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 编辑目标；null = 新建 */
  initial?: BackendKnowledgeBase | null;
  /** 提交（已通过前端校验）；返回 true 才关闭弹窗 */
  onSubmit: (values: KnowledgeFormValues) => Promise<boolean>;
}

export const KnowledgeFormDialog: FC<KnowledgeFormDialogProps> = ({
  open,
  onOpenChange,
  initial = null,
  onSubmit,
}) => {
  const isEdit = initial != null;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [weight, setWeight] = useState("0");
  const [nameError, setNameError] = useState<string | null>(null);
  const [weightError, setWeightError] = useState<string | null>(null);

  // 每次打开按 initial 重置表单（关闭时不清，避免输入闪烁）
  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? "");
    setDescription(initial?.description ?? "");
    setWeight(String(initial?.weight ?? 0));
    setNameError(null);
    setWeightError(null);
  }, [open, initial]);

  const { submitting, handleSubmit } = useDialogSubmit(async () => {
    const trimmedName = name.trim();
    const nextNameError = !trimmedName
      ? "请输入知识库名称"
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
          <DialogTitle>{isEdit ? "编辑知识库" : "新建知识库"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "更新知识库的基本信息，不影响库内文档。"
              : "创建后可上传文档，供对话检索引用。"}
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label htmlFor="kb-name">名称</Label>
            <Input
              id="kb-name"
              value={name}
              maxLength={NAME_MAX}
              placeholder="例如：产品手册"
              aria-invalid={nameError != null}
              onChange={(e) => setName(e.target.value)}
            />
            {nameError ? (
              <p className="text-xs text-destructive">{nameError}</p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {name.trim().length}/{NAME_MAX}
              </p>
            )}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="kb-description">描述</Label>
            <Textarea
              id="kb-description"
              value={description}
              rows={3}
              maxLength={DESCRIPTION_MAX}
              placeholder="知识库用途说明（可选）"
              onChange={(e) => setDescription(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {description.length}/{DESCRIPTION_MAX}
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="kb-weight">权重</Label>
            <Input
              id="kb-weight"
              type="number"
              value={weight}
              aria-invalid={weightError != null}
              onChange={(e) => setWeight(e.target.value)}
            />
            {weightError && (
              <p className="text-xs text-destructive">{weightError}</p>
            )}
            <p className="text-xs text-muted-foreground">
              管理侧排序权重，越大越靠前。
            </p>
          </div>

          <DialogFormFooter
            submitting={submitting}
            label={isEdit ? "保存" : "创建"}
          />
        </form>
      </DialogContent>
    </Dialog>
  );
};
