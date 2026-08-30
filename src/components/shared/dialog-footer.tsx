import { type FC, type ReactNode } from "react";
import { Loader2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";

interface DialogFormFooterProps {
  submitting: boolean;
  /** 提交按钮文案（可为动态节点，如「上传（3）」） */
  label: ReactNode;
  /** 提交按钮的额外禁用条件（与 submitting 相与） */
  disabled?: boolean;
  /** 危险操作（删除/重置）用红色按钮 */
  destructive?: boolean;
  /** 非 <form> 场景（如上传弹窗直接 onClick）用 button + onClick */
  type?: "submit" | "button";
  onClick?: () => void;
}

/** 弹窗 footer 标准排布：左侧取消（DialogClose），右侧提交（带 loading 图标）。 */
export const DialogFormFooter: FC<DialogFormFooterProps> = ({
  submitting,
  label,
  disabled = false,
  destructive = false,
  type = "submit",
  onClick,
}) => (
  <DialogFooter>
    <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
    <Button
      type={type}
      variant={destructive ? "destructive" : undefined}
      disabled={disabled || submitting}
      onClick={onClick}
    >
      {submitting && <Loader2Icon className="animate-spin" />}
      {label}
    </Button>
  </DialogFooter>
);
