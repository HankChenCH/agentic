import type { FC } from "react";

import {
  ENTITY_TYPE_COLORS,
  ENTITY_TYPE_LABELS,
} from "@/components/memory-graph/layout";

/**
 * 记忆图谱图例：实体类型色点、节点/边语义、特殊样式说明。
 * 固定悬浮在画布左下角、缩放控件右侧：右侧是可全高展开的详情面板，
 * 底部居中会被其下沿压住，署名又占右下角，只有这块空位确定不重叠。
 */

const LEGEND_TYPES = ["PERSON", "PLACE", "ORG", "OBJECT", "CONCEPT"] as const;

export const MemoryGraphLegend: FC = () => (
  <div className="absolute bottom-4 left-20 z-10 flex max-w-[420px] flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border/70 bg-card/90 px-3 py-2 text-[11px] text-muted-foreground shadow-card backdrop-blur">
    {LEGEND_TYPES.map((type) => (
      <span key={type} className="inline-flex items-center gap-1">
        <span
          aria-hidden
          className="size-2 rounded-full"
          style={{ backgroundColor: ENTITY_TYPE_COLORS[type] }}
        />
        {ENTITY_TYPE_LABELS[type]}
      </span>
    ))}
    <span className="inline-flex items-center gap-1">
      <span aria-hidden className="size-2 rounded-sm bg-indigo-400" />
      事件
    </span>
    <span>实线=事实</span>
    <span>虚线=参与/历史</span>
    <span className="text-amber-600">橙边=人工维护</span>
  </div>
);
