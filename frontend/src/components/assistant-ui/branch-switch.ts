/**
 * 分支切换的「本地乐观 + 服务端激活」编排与失败回滚（无 React 依赖，供单测）。
 *
 * 切换 = 本地消息仓库先切（乐观，即时反馈）+ POST activate-turn 把服务端
 * 活跃叶子同步到目标轮次。两端必须一致——后续 run 的 parent 与多轮回放都
 * 从活跃叶子派生——因此：
 *   - 激活成功 → 静默，不打扰用户；
 *   - 激活失败（网络/业务错误），或目标消息映射不到轮次（无法同步服务端）→
 *     toast 提示并回滚到切换前分支（服务端仍激活的锚点），本地视图不与服务
 *     端脱节到刷新。
 *
 * 连点口径：seq 自增标记最新一次切换，只有最新一次的失败会回滚/提示——
 * 旧请求迟到的失败响应（响应乱序到达）不得把更新一次已生效的切换滚回去。
 *
 * 仓库切换是同步的，但 aui 状态快照要等 React 提交后才更新——立即读末梢会
 * 拿到切换前的旧消息，activate-turn 会映射失败。因此读取末梢推迟一个宏任务
 * （通知已 flush）再执行（生产由 defer = setTimeout(0) 承担，测试手动冲刷）。
 */
import { BizError } from "@/lib/http";

/** 宿主端口：React 侧注入 aui 作用域 / 服务 / toast，纯逻辑不直接依赖它们 */
export interface BranchSwitchPorts {
  /** 本地切到相邻分支（乐观切换） */
  switchLocal(position: "previous" | "next"): void;
  /** 本地切回指定消息（失败回滚；branchId 即兄弟消息 id） */
  switchLocalTo(messageId: string): void;
  /** 服务端激活目标轮次 */
  activateTurn(threadId: string, turnId: string): Promise<void>;
  /** 失败提示（成功静默，不经过这里） */
  toastError(message: string): void;
  /** 推迟一个宏任务（生产 setTimeout(0)；测试手动冲刷） */
  defer(fn: () => void): void;
}

export interface BranchSwitchInput {
  position: "previous" | "next";
  /** 切换前消息 id：回滚锚点（服务端仍激活的分支） */
  previousMessageId: string;
  threadId: string | null | undefined;
  /** 消息 id → 轮次 id（历史加载时由 collectTurnIdByMessageId 构建） */
  turnByMessageId: ReadonlyMap<string, string>;
  /** 切换生效后读取末梢消息 id（须在 React 提交后调用） */
  getTailMessageId(): string | undefined;
}

export class BranchSwitchController {
  /** 最新一次切换的序号（模块级共享：点击与迟到回调之间组件已重渲染） */
  private seq = 0;

  switch(ports: BranchSwitchPorts, input: BranchSwitchInput): void {
    const seq = ++this.seq;
    ports.switchLocal(input.position);
    ports.defer(() => {
      const tailId = input.getTailMessageId();
      const turnId = tailId ? input.turnByMessageId.get(tailId) : undefined;
      if (!input.threadId || !turnId) {
        // 映射不到轮次：服务端同步无从发起，按失败处理（旧行为是静默视觉
        // 切换，会让本地视图与服务端活跃叶子脱节直到刷新）
        this.rollbackIfLatest(seq, ports, input, "无法定位目标轮次");
        return;
      }
      ports.activateTurn(input.threadId, turnId).catch((err: unknown) => {
        this.rollbackIfLatest(
          seq,
          ports,
          input,
          err instanceof BizError ? err.message : "网络异常",
        );
      });
    });
  }

  /** 非最新一次切换：旧请求的失败静默丢弃（不得回滚/提示，以最新一次为准） */
  private rollbackIfLatest(
    seq: number,
    ports: BranchSwitchPorts,
    input: BranchSwitchInput,
    reason: string,
  ): void {
    if (seq !== this.seq) return;
    ports.toastError(`分支切换失败：${reason}，已回滚到原分支`);
    ports.switchLocalTo(input.previousMessageId);
  }
}
