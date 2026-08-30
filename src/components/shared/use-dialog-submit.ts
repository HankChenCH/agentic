import { useState, type FormEvent } from "react";

/**
 * 弹窗表单提交钩子。包装 form onSubmit：preventDefault → 执行 action →
 * 返回 falsy（null/false/undefined）视为失败或校验未过，保持打开；
 * 返回 truthy 则关闭弹窗。loading 态由 submitting 暴露给提交按钮。
 */
export const useDialogSubmit = (
  action: () => Promise<unknown> | unknown,
  onOpenChange: (open: boolean) => void,
): {
  submitting: boolean;
  handleSubmit: (e: FormEvent) => Promise<void>;
} => {
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    try {
      const result = await action();
      if (result !== null && result !== undefined && result !== false) {
        onOpenChange(false);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return { submitting, handleSubmit };
};
