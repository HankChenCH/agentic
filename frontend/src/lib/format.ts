import type { KnowledgeSource } from "@/services/types";

/**
 * 展示层格式化工具（纯函数，无副作用）。
 */

/** 字节数 → 人类可读大小（B/KB/MB/GB），未知大小时返回 "-" */
export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return "-";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = -1;
  do {
    value /= 1024;
    unit += 1;
  } while (value >= 1024 && unit < units.length - 1);
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/** ISO 时间串 → 本地化时间（YYYY-MM-DD HH:mm） */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "-";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** 检索来源页码（后端 0 起存储，展示 +1；起止同页合并），无页码信息返回空串 */
export function formatSourcePages(source: KnowledgeSource): string {
  if (source.page_start == null) return "";
  if (source.page_end == null || source.page_start === source.page_end) {
    return `p.${source.page_start + 1}`;
  }
  return `p.${source.page_start + 1}-${source.page_end + 1}`;
}
