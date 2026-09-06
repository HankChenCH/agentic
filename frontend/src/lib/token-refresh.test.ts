import { describe, expect, it } from "vitest";

import { shouldRefresh } from "@/lib/token-refresh";

describe("shouldRefresh（access token 临期判定）", () => {
  const now = Date.parse("2026-01-01T12:00:00Z");
  const threshold = 5 * 60 * 1000;

  it("剩余寿命不足阈值 → 需要刷新", () => {
    const exp = new Date(now + 4 * 60 * 1000).toISOString();
    expect(shouldRefresh(exp, now)).toBe(true);
  });

  it("刚好压线（等于阈值）→ 需要刷新", () => {
    const exp = new Date(now + threshold).toISOString();
    expect(shouldRefresh(exp, now)).toBe(true);
  });

  it("剩余寿命充足 → 不刷新", () => {
    const exp = new Date(now + 25 * 60 * 1000).toISOString();
    expect(shouldRefresh(exp, now)).toBe(false);
  });

  it("已过期 → 需要刷新", () => {
    const exp = new Date(now - 1000).toISOString();
    expect(shouldRefresh(exp, now)).toBe(true);
  });

  it("缺失/非法时间戳（旧持久化会话）→ 不主动刷，交由 401 兜底", () => {
    expect(shouldRefresh(null, now)).toBe(false);
    expect(shouldRefresh(undefined, now)).toBe(false);
    expect(shouldRefresh("not-a-date", now)).toBe(false);
  });
});
