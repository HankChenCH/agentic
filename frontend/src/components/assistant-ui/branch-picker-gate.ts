/**
 * BranchPicker 末梢锁的纯逻辑判定（无 React 依赖，供单测）。
 *
 * 「续聊即定型」：仅末梢轮次允许切换变体，中途扇形锁死。末梢轮次包含
 * 两种消息位置：
 *   - 末梢 assistant：线程最后一条（isLast），重新生成型扇形的 "1/2" 挂在
 *     回答下方；
 *   - 末梢 user：其后仅剩末梢回答（index === messageCount - 2），编辑/换问法
 *     型扇形（用户消息互为兄弟）的 "1/2" 挂在问题行。
 *
 * 运行中新消息（含乐观 assistant 占位）入列后，原末梢 user 退居倒数第三，
 * 判定自动转 false —— 选择器随新轮次开跑而隐藏。
 */
export function isTailTurnMessage(
  isLast: boolean,
  index: number,
  messageCount: number,
): boolean {
  if (isLast) return true;
  return messageCount - 2 === index;
}
