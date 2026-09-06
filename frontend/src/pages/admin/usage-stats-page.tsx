import { useState, type FC } from "react";
import { RefreshCwIcon } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminPageShell } from "@/components/shared/admin-page-shell";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useUsageStats, type UsageRangeKey } from "@/hooks/use-usage-stats";
import { formatDateTime } from "@/lib/format";
import type { UsageRecord, UsageScene, UsageSlice } from "@/services/types";

/**
 * 我的用量页（/admin/usage，管理侧用量统计模块）。
 *
 * 个人用量视角：时间范围（近 7/30 天）+ 四张总量卡片 + 每日 token 柱状图
 * （输入/输出堆叠）+ 按场景/按模型分布表 + 明细流水分页表。数据源为
 * /stats/usage/*（后端按 UTC 日分桶，半开区间 [start, end)）。
 */

const SCENE_LABELS: Record<UsageScene, string> = {
  chat: "对话",
  title: "标题生成",
  memory: "记忆巩固",
};

/** 千分位数字（undefined/缺省按 0） */
const num = (n: number | null | undefined): string =>
  (n ?? 0).toLocaleString("zh-CN");

const RANGE_OPTIONS: { key: UsageRangeKey; label: string }[] = [
  { key: "7d", label: "近 7 天" },
  { key: "30d", label: "近 30 天" },
];

/** 明细表的场景徽章（未知场景按原样展示，防后端扩展后前端崩） */
const SceneBadge: FC<{ scene: string }> = ({ scene }) => (
  <Badge variant="secondary">
    {SCENE_LABELS[scene as UsageScene] ?? scene}
  </Badge>
);

/** 切片行数据（按场景/按模型共用，卡片与表格两形态渲染同一数据） */
interface SliceRow {
  key: string;
  label: string;
  calls: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

/** 窄屏切片卡：标签 + 总计一行，次数/输入/输出一行小字（四项并排会挤截标签） */
const SliceCard: FC<{ row: SliceRow }> = ({ row }) => (
  <div className="flex flex-col gap-1 rounded-lg border border-border/70 bg-card px-3 py-2.5">
    <div className="flex items-baseline justify-between gap-3">
      <span className="min-w-0 truncate text-sm font-medium">{row.label}</span>
      <span className="shrink-0 text-sm font-medium tabular-nums">
        总计 {num(row.totalTokens)}
      </span>
    </div>
    <div className="flex items-baseline gap-3 text-xs text-muted-foreground tabular-nums">
      <span>次数 {num(row.calls)}</span>
      <span>输入 {num(row.promptTokens)}</span>
      <span>输出 {num(row.completionTokens)}</span>
    </div>
  </div>
);

/** 切片表（按场景/按模型共用）：md+ 表格，窄屏卡片列表 */
const SliceTable: FC<{ slices: UsageSlice[]; labelHeader: string; renderKey: (key: string) => string }> = ({
  slices,
  labelHeader,
  renderKey,
}) => {
  const rows: SliceRow[] = slices.map((slice) => ({
    key: slice.key,
    label: renderKey(slice.key),
    calls: slice.calls,
    promptTokens: slice.promptTokens,
    completionTokens: slice.completionTokens,
    totalTokens: slice.totalTokens,
  }));

  if (rows.length === 0) {
    return (
      <p className="py-4 text-center text-sm text-muted-foreground">
        暂无数据
      </p>
    );
  }

  return (
    <>
      {/* 窄屏：紧凑卡片列表 */}
      <div className="flex flex-col gap-2 md:hidden">
        {rows.map((row) => (
          <SliceCard key={row.key} row={row} />
        ))}
      </div>
      {/* md+：完整表格 */}
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{labelHeader}</TableHead>
              <TableHead className="text-right">次数</TableHead>
              <TableHead className="text-right">输入</TableHead>
              <TableHead className="text-right">输出</TableHead>
              <TableHead className="text-right">总计</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableCell>{row.label}</TableCell>
                <TableCell className="text-right tabular-nums">{num(row.calls)}</TableCell>
                <TableCell className="text-right tabular-nums">{num(row.promptTokens)}</TableCell>
                <TableCell className="text-right tabular-nums">{num(row.completionTokens)}</TableCell>
                <TableCell className="text-right font-medium tabular-nums">{num(row.totalTokens)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  );
};

/** 明细表窄屏卡片：时间/场景一行、模型一行、token 三项一行 */
const RecordCard: FC<{ row: UsageRecord }> = ({ row }) => (
  <div className="flex flex-col gap-1.5 rounded-lg border border-border/70 bg-card px-3 py-2.5">
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-muted-foreground tabular-nums">
        {formatDateTime(row.createdAt)}
      </span>
      <SceneBadge scene={row.scene} />
    </div>
    <div className="truncate text-sm font-medium" title={row.model}>
      {row.model}
    </div>
    <div className="flex items-baseline justify-between gap-3 text-xs text-muted-foreground tabular-nums">
      <span>输入 {num(row.promptTokens)}</span>
      <span>输出 {num(row.completionTokens)}</span>
      <span className="text-sm font-medium text-foreground">
        总计 {num(row.totalTokens)}
      </span>
    </div>
  </div>
);

export const UsageStatsPage: FC = () => {
  const [rangeKey, setRangeKey] = useState<UsageRangeKey>("7d");
  const { summary, daily, records, setPage, isLoading, error, refresh } =
    useUsageStats(rangeKey);

  const rangeLabel = RANGE_OPTIONS.find((o) => o.key === rangeKey)?.label ?? "";
  const totalPages =
    records == null ? 1 : Math.max(1, Math.ceil(records.total / records.pageSize));
  const isEmpty = !isLoading && (summary?.totals.calls ?? 0) === 0;

  return (
    <AdminPageShell
      backTo="/admin"
      backLabel="返回控制台"
      title="我的用量"
      description="LLM 调用与 token 消耗统计（个人视角；按 UTC 日分桶，统计自用量流水上线起）"
      width="medium"
      actions={
        <>
          {/* 预置范围切换（[start, end) 半开区间，UTC 整日对齐） */}
          {RANGE_OPTIONS.map((option) => (
            <Button
              key={option.key}
              size="sm"
              variant={option.key === rangeKey ? "default" : "outline"}
              onClick={() => setRangeKey(option.key)}
            >
              {option.label}
            </Button>
          ))}
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground"
            onClick={refresh}
            aria-label="刷新用量"
          >
            <RefreshCwIcon />
          </Button>
        </>
      }
    >
      {error != null && (
          <Card className="border-destructive/40">
            <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
              <p className="text-sm text-destructive">用量数据加载失败：{error.message}</p>
              <Button size="sm" variant="outline" onClick={refresh}>
                重试
              </Button>
            </CardContent>
          </Card>
        )}

        {/* 总量卡片：调用次数 / 输入 / 输出 / 总 token */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {(
            [
              { label: "调用次数", value: summary?.totals.calls, hint: "LLM 调用次数" },
              { label: "输入 Tokens", value: summary?.totals.promptTokens, hint: "Prompt tokens" },
              { label: "输出 Tokens", value: summary?.totals.completionTokens, hint: "Completion tokens" },
              { label: "总 Tokens", value: summary?.totals.totalTokens, hint: `${rangeLabel}合计` },
            ] as const
          ).map(({ label, value, hint }) => (
            <Card key={label}>
              <CardHeader>
                <CardDescription>{label}</CardDescription>
                <CardTitle className="tabular-nums">
                  {isLoading ? <Skeleton className="h-7 w-20" /> : num(value)}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">{hint}</CardContent>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>每日用量趋势</CardTitle>
            <CardDescription>
              {rangeLabel} · 输入/输出 token 堆叠（UTC 日分桶）
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading || daily == null ? (
              <Skeleton className="h-64 w-full" />
            ) : daily.items.length === 0 ? (
              <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
                {isEmpty ? "暂无用量数据" : "该范围内暂无数据"}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={256}>
                <BarChart data={daily.items} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                  <XAxis dataKey="key" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis tickLine={false} axisLine={false} fontSize={12} width={56} allowDecimals={false} />
                  <Tooltip
                    formatter={(value, name) => [num(Number(value)), String(name)]}
                    contentStyle={{
                      backgroundColor: "var(--card)",
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="promptTokens" name="输入" stackId="usage" fill="var(--chart-1)" />
                  <Bar dataKey="completionTokens" name="输出" stackId="usage" fill="var(--chart-3)" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>按场景分布</CardTitle>
              <CardDescription>{rangeLabel} · 对话 / 标题生成 / 记忆巩固</CardDescription>
            </CardHeader>
            <CardContent>
              {isLoading || summary == null ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <SliceTable
                  slices={summary.byScene}
                  labelHeader="场景"
                  renderKey={(key) => SCENE_LABELS[key as UsageScene] ?? key}
                />
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>按模型分布</CardTitle>
              <CardDescription>{rangeLabel} · 各模型的 token 消耗</CardDescription>
            </CardHeader>
            <CardContent>
              {isLoading || summary == null ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <SliceTable slices={summary.byModel} labelHeader="模型" renderKey={(key) => key} />
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>用量明细</CardTitle>
            <CardDescription>
              {rangeLabel} · 一次 LLM 调用一行，时间倒序
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {isLoading || records == null ? (
              <Skeleton className="h-40 w-full" />
            ) : (
              <>
                {/* 窄屏：流水卡片列表 */}
                <div className="flex flex-col gap-2 md:hidden">
                  {records.items.length === 0 ? (
                    <p className="py-6 text-center text-sm text-muted-foreground">
                      暂无流水记录
                    </p>
                  ) : (
                    records.items.map((row: UsageRecord) => (
                      <RecordCard key={row.id} row={row} />
                    ))
                  )}
                </div>
                {/* md+：完整表格 */}
                <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>时间</TableHead>
                      <TableHead>场景</TableHead>
                      <TableHead>模型</TableHead>
                      <TableHead className="text-right">输入</TableHead>
                      <TableHead className="text-right">输出</TableHead>
                      <TableHead className="text-right">总计</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {records.items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="text-center text-muted-foreground">
                          暂无流水记录
                        </TableCell>
                      </TableRow>
                    ) : (
                      records.items.map((row: UsageRecord) => (
                        <TableRow key={row.id}>
                          <TableCell className="whitespace-nowrap">
                            {formatDateTime(row.createdAt)}
                          </TableCell>
                          <TableCell>
                            <SceneBadge scene={row.scene} />
                          </TableCell>
                          <TableCell className="max-w-48 truncate" title={row.model}>
                            {row.model}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">{num(row.promptTokens)}</TableCell>
                          <TableCell className="text-right tabular-nums">{num(row.completionTokens)}</TableCell>
                          <TableCell className="font-medium tabular-nums">{num(row.totalTokens)}</TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
                </div>
                {/* 服务端分页：total/page/pageSize 来自后端 */}
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
                  <span>共 {num(records.total)} 条 · 第 {records.page} / {totalPages} 页</span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={records.page <= 1}
                      onClick={() => setPage(records.page - 1)}
                    >
                      上一页
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={records.page >= totalPages}
                      onClick={() => setPage(records.page + 1)}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
    </AdminPageShell>
  );
};
