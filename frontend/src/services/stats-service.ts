import { getJson } from "@/lib/http";
import type {
  UsageDailyResult,
  UsageRecordListResult,
  UsageScene,
  UsageSummary,
} from "@/services/types";

/**
 * 用量统计 REST 服务（个人用量视角：后端以当前登录用户圈定数据）。
 *
 * 所有方法都经 @/lib/http 的 axios 实例（拦截器自动剥去响应信封）。
 * 时间范围语义为半开区间 [start, end)：start 闭、end 开，ISO 8601 字符串；
 * 后端按天分桶取 UTC 日。
 */

/** 查询参数形态（undefined 的键由 axios 序列化时省略） */
export interface UsageRangeParams {
  start?: string;
  end?: string;
  scene?: UsageScene;
}

export const statsService = {
  /**
   * 区间总量 + 按场景/按模型分布。
   * GET /stats/usage/summary?start=&end=
   */
  async getUsageSummary(range: UsageRangeParams = {}): Promise<UsageSummary> {
    return getJson<UsageSummary>("/stats/usage/summary", { params: range });
  },

  /**
   * 按天时间序列（UTC 日分桶，日期升序）。
   * GET /stats/usage/daily?start=&end=&scene=
   */
  async getUsageDaily(range: UsageRangeParams = {}): Promise<UsageDailyResult> {
    return getJson<UsageDailyResult>("/stats/usage/daily", { params: range });
  },

  /**
   * 用量流水分页（时间倒序，一行 = 一次 LLM 调用）。
   * GET /stats/usage/records?page=&pageSize=&start=&end=&scene=
   */
  async getUsageRecords(
    range: UsageRangeParams = {},
    page = 1,
    pageSize = 10,
  ): Promise<UsageRecordListResult> {
    return getJson<UsageRecordListResult>("/stats/usage/records", {
      params: { ...range, page, pageSize },
    });
  },
};
