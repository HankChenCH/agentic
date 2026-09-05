import { useMemo, useState } from "react";
import { LoaderIcon, ChevronDownIcon, SearchIcon } from "lucide-react";
import { useAssistantToolUI, type ToolCallMessagePartComponent } from "@assistant-ui/react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { usePdfPreview } from "@/components/shared/pdf-preview-provider";
import { formatSourcePages } from "@/lib/format";
import type {
  KnowledgeSearchResult,
  KnowledgeSource,
} from "@/services/types";

/**
 * knowledge_search / knowledge_context 工具的溯源卡片渲染（后端两者返回
 * 同形的 ``{"sources": [...], "notes": [...]}`` JSON，前端共用同一套卡片
 * 与 PDF 溯源抽屉，仅折叠标题不同；定位读取无检索分，卡片不显示相关度）。
 *
 * 后端把结果序列化为 JSON 字符串（LLM 与前端共用），经 ag-ui
 * ToolCallResultEvent.content 与历史 TOOL_RESULT 行原样透传到 tool-call
 * part 的 result——实时流与刷新后的历史回放走同一条渲染路径。
 *
 * 注册方式：``useAssistantToolUI`` 挂载即注册（仅影响渲染，不改变发给
 * 服务端的工具清单——工具由后端定义并执行）。
 */

/**
 * 解析工具结果 → 结构化来源；返回 null 走降级展示（纯文本/空态）。
 *
 * 实时路径：ag-ui runtime 对合法 JSON 的 ToolCallResultEvent.content 会自动
 * tryParseJSON 成对象再放进 part.result；历史回放路径（thread-message-translator）
 * 则原样给字符串——两种形态都要兼容。
 */
function parseResult(result: unknown): KnowledgeSearchResult | null {
  let parsed: unknown = result;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return null;
    }
  }
  if (
    parsed != null &&
    typeof parsed === "object" &&
    Array.isArray((parsed as KnowledgeSearchResult).sources)
  ) {
    return parsed as KnowledgeSearchResult;
  }
  return null;
}

function SourceCard({
  source,
  onOpen,
}: {
  source: KnowledgeSource;
  onOpen: () => void;
}) {
  const pages = formatSourcePages(source);
  return (
    <div className="rounded-lg border bg-muted/30 p-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Badge variant="secondary" className="shrink-0 tabular-nums">
          {source.index}
        </Badge>
        <span className="truncate text-sm font-medium">{source.doc_name}</span>
        {pages && (
          <span className="shrink-0 text-xs text-muted-foreground">
            {pages}
          </span>
        )}
        {typeof source.score === "number" && (
          <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
            相关度 {(source.score * 100).toFixed(0)}%
          </span>
        )}
      </div>
      {source.heading_path.length > 0 && (
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {source.heading_path.join(" > ")}
        </p>
      )}
      <p className="mt-1.5 line-clamp-3 text-xs leading-relaxed text-foreground/80">
        {source.content}
      </p>
      <Button
        variant="outline"
        size="sm"
        className="mt-2"
        onClick={onOpen}
      >
        <SearchIcon />
        查看原文
      </Button>
    </div>
  );
}

/** 折叠标题文案：检索与定位读取共用渲染体，仅标题不同 */
interface ToolLabels {
  running: string;
  plain: string;
  result: (count: number) => string;
}

function makeKnowledgeToolRender({
  running: runningLabel,
  plain: plainLabel,
  result: resultLabel,
}: ToolLabels): ToolCallMessagePartComponent {
  const Render: ToolCallMessagePartComponent = ({ result, status }) => {
    const [open, setOpen] = useState(false);
    const pdfPreview = usePdfPreview();
    const parsed = useMemo(() => parseResult(result), [result]);
    const running = status?.type === "running";

    const openSource = (index: number) => {
      // 溯源走右侧抽屉（非模态）：携带整批来源，打开后可随时切换
      if (!parsed) return;
      pdfPreview.openPanel({ sources: parsed.sources, activeIndex: index });
    };

    const label = running
      ? runningLabel
      : parsed
        ? resultLabel(parsed.sources.length)
        : plainLabel;

    return (
      <Collapsible open={open} onOpenChange={setOpen} className="w-full">
        <CollapsibleTrigger
          className={cn(
            "group/trigger text-muted-foreground hover:text-foreground flex w-fit items-center gap-2 py-1.5 text-sm transition-colors",
          )}
        >
          {running ? (
            <LoaderIcon className="size-4 shrink-0 animate-spin [animation-duration:0.6s]" />
          ) : (
            <SearchIcon className="size-4 shrink-0" />
          )}
          <span>{label}</span>
          <ChevronDownIcon className="size-4 shrink-0 -rotate-90 transition-transform duration-200 group-data-open/trigger:rotate-0 group-data-panel-open/trigger:rotate-0" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex flex-col gap-2 ps-6 pt-1 pb-2">
            {parsed ? (
              <>
                {parsed.sources.map((source, index) => (
                  <SourceCard
                    key={`${source.doc_id}-${source.position}`}
                    source={source}
                    onOpen={() => openSource(index)}
                  />
                ))}
                {parsed.notes.map((note) => (
                  <p key={note} className="text-xs text-muted-foreground">
                    {note}
                  </p>
                ))}
              </>
            ) : typeof result === "string" ? (
              // 历史纯文本结果（旧格式/未检索到/引导话术）：原样展示
              <pre className="bg-muted/50 rounded-md p-2.5 text-xs whitespace-pre-wrap">
                {result}
              </pre>
            ) : null}
          </div>
        </CollapsibleContent>
      </Collapsible>
    );
  };
  return Render;
}

const KnowledgeSearchRender = makeKnowledgeToolRender({
  running: "知识库检索中…",
  plain: "知识库检索",
  result: (count) => `知识库检索 · ${count} 个来源`,
});

const KnowledgeContextRender = makeKnowledgeToolRender({
  running: "片段读取中…",
  plain: "片段读取",
  result: (count) => `片段读取 · ${count} 个片段`,
});

/** 挂载即注册 knowledge_search 的渲染器（自身不渲染内容） */
export const KnowledgeSearchToolUI = () => {
  useAssistantToolUI({
    toolName: "knowledge_search",
    render: KnowledgeSearchRender,
  });
  return null;
};

/** 挂载即注册 knowledge_context 的渲染器（与检索共用溯源卡片，仅标题不同） */
export const KnowledgeContextToolUI = () => {
  useAssistantToolUI({
    toolName: "knowledge_context",
    render: KnowledgeContextRender,
  });
  return null;
};
