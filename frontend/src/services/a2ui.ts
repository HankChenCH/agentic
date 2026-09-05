/**
 * A2UI 载荷解析（纯函数，与后端 `app/packages/a2ui` 契约对称）。
 *
 * 后端把 A2UI v0.9 消息数组（createSurface/updateComponents/updateDataModel/
 * deleteSurface 的信封，见 https://a2ui.org/specification/v0_9/json/
 * server_to_client_list.json）作为 ag-ui CUSTOM 事件（name="a2ui"）的 value
 * 下发；本模块负责在渲染前做防御性校验——载荷非法时返回 null，渲染器降级
 * 为折叠 JSON，绝不让坏载荷炸掉消息流。
 *
 * 类型直接复用官方 zod 推导的 `A2uiMessage`（@a2ui/web_core/v0_9）；运行时
 * 校验刻意比官方类型严格（只认 version="v0.9"）——本仓库后端只发 v0.9，
 * 官方类型为兼容 v0.9.1 留的宽口径不放进来。
 *
 * 注意与后端常量保持同步：改事件名必须两侧同时改（各自 AGENTS.md 有记录）。
 */

import type { A2uiMessage } from "@a2ui/web_core/v0_9";

/** ag-ui CUSTOM 事件名 / data part 名（与后端 A2UI_CUSTOM_EVENT_NAME 对齐） */
export const A2UI_DATA_PART_NAME = "a2ui";

/** 本仓库后端当前生成的协议版本常量 */
export const A2UI_SPEC_VERSION = "v0.9";

/** A2UI v0.9 信封的四种消息键（server_to_client.json 的 oneOf） */
const UPDATE_KEYS = [
  "createSurface",
  "updateComponents",
  "updateDataModel",
  "deleteSurface",
] as const;

function isA2uiMessage(value: unknown): value is A2uiMessage {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const message = value as Record<string, unknown>;
  if (message.version !== A2UI_SPEC_VERSION) return false;
  const keys = UPDATE_KEYS.filter((key) => key in message);
  // 恰好携带一种消息体（多键信封是非法载荷）
  if (keys.length !== 1) return false;
  const payload = message[keys[0]];
  return (
    payload !== null &&
    typeof payload === "object" &&
    typeof (payload as { surfaceId?: unknown }).surfaceId === "string"
  );
}

/**
 * 解析 A2UI 载荷 → 消息数组；非法返回 null（调用方降级渲染）。
 *
 * 双形态兼容（同 knowledge 工具 parseResult 的先例）：实时流的 data part
 * value 是对象数组；历史回放/防御性路径可能是 JSON 字符串。数组为空、
 * 任一消息结构非法都整体判非法——A2UI 消息序是原子的，半渲染比不渲染更糟。
 */
export function parseA2uiPayload(value: unknown): A2uiMessage[] | null {
  let raw = value;
  if (typeof raw === "string") {
    try {
      raw = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!Array.isArray(raw) || raw.length === 0) return null;
  return raw.every((message) => isA2uiMessage(message))
    ? (raw as A2uiMessage[])
    : null;
}

export type { A2uiMessage };
