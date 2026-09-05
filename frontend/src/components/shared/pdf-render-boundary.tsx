import { Component, type ReactNode } from "react";
import { AlertCircleIcon, RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

interface PdfRenderBoundaryProps {
  children: ReactNode;
  /** 重试回调：交由数据层重新拉取文件字节（ bump reloadKey ） */
  onRetry: () => void;
}

interface PdfRenderBoundaryState {
  error: Error | null;
}

/**
 * PDF 渲染错误边界：兜住 react-pdf / pdf.js 在渲染期抛出的异常
 * （如 file 缓冲区已被 worker 转移（detach）后再被深比较的
 * "Cannot perform Construct on a detached ArrayBuffer"），避免异常沿
 * 组件树冒泡到路由层把整个应用打成错误屏——预览失败只应影响预览区。
 *
 * 必须用 key 绑定文档/重试代际（见 PdfViewer 用法）：文档切换或点
 * 「重试」时边界连同子树一起重挂载，错误状态自然清零，否则一旦出错
 * 即使换成新文档也永远停在降级 UI。
 */
export class PdfRenderBoundary extends Component<
  PdfRenderBoundaryProps,
  PdfRenderBoundaryState
> {
  state: PdfRenderBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): PdfRenderBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: unknown) {
    // 渲染已被边界拦截，留一条现场日志便于排查
    console.error("PDF 渲染失败：", error);
  }

  render() {
    const { error } = this.state;
    if (error) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3">
          <AlertCircleIcon className="size-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            PDF 渲染失败：{error.message}
          </p>
          <Button variant="outline" size="sm" onClick={this.props.onRetry}>
            <RefreshCwIcon />
            重试
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
