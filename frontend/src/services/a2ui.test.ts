import { describe, expect, it } from "vitest";

import {
  A2UI_DATA_PART_NAME,
  parseA2uiPayload,
} from "@/services/a2ui";

// ---------------------------------------------------------------------------
// fixture：与后端 weather_surface 产物同构的最小 A2UI v0.9 消息序
// ---------------------------------------------------------------------------

const surfaceId = "weather-abc";

const validPayload = [
  {
    version: "v0.9",
    createSurface: {
      surfaceId,
      catalogId: "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json",
    },
  },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId,
      components: [
        { id: "root", component: "Card", child: "body" },
        { id: "body", component: "Column", children: ["city"] },
        { id: "city", component: "Text", text: "中山" },
      ],
    },
  },
];

describe("parseA2uiPayload", () => {
  it("接受对象数组形态（实时流的 data part value）", () => {
    expect(parseA2uiPayload(validPayload)).toEqual(validPayload);
  });

  it("接受 JSON 字符串形态（历史回放/防御路径）", () => {
    expect(parseA2uiPayload(JSON.stringify(validPayload))).toEqual(validPayload);
  });

  it("非数组、空数组、坏 JSON 一律判非法", () => {
    expect(parseA2uiPayload(null)).toBeNull();
    expect(parseA2uiPayload(42)).toBeNull();
    expect(parseA2uiPayload([])).toBeNull();
    expect(parseA2uiPayload("{not json")).toBeNull();
  });

  it("消息缺 version / version 不符 / 携带多种消息体 → 整体判非法", () => {
    expect(
      parseA2uiPayload([{ version: "v0.8", createSurface: { surfaceId } }]),
    ).toBeNull();
    expect(parseA2uiPayload([{ createSurface: { surfaceId } }])).toBeNull();
    expect(
      parseA2uiPayload([
        {
          version: "v0.9",
          createSurface: { surfaceId },
          deleteSurface: { surfaceId },
        },
      ]),
    ).toBeNull();
  });

  it("消息体缺 surfaceId / 非对象信封 → 整体判非法", () => {
    expect(parseA2uiPayload([{ version: "v0.9", createSurface: {} }])).toBeNull();
    expect(parseA2uiPayload([{ version: "v0.9", deleteSurface: null }])).toBeNull();
    expect(parseA2uiPayload(["v0.9"])).toBeNull();
  });

  it("原子性：合法消息与非法消息混排时整体判非法（半渲染比不渲染更糟）", () => {
    expect(parseA2uiPayload([...validPayload, { version: "v0.9" }])).toBeNull();
  });

  it("事件名常量是与后端对齐的传输契约（当前 a2ui）", () => {
    expect(A2UI_DATA_PART_NAME).toBe("a2ui");
  });
});
