// ===========================================================================
// 后端领域类型（与 server/app/models/domain/agentic 对齐）
//
// 字段为 snake_case —— SQLModel model_dump() 默认不带 alias（已确认后端无
// alias 生成器）。若后端将来改用 camelCase alias，同步调整此处字段名即可。
// ===========================================================================

/** GET /conversation 返回的 items 元素（AgenticConversation model_dump） */
export interface BackendConversation {
  id: number;
  agentic_id: string;
  thread_id: string;
  current_turn_id?: string | null;
  conversation_title: string;
  created_at: string;
  updated_at: string;
}

export type BackendRole = "system" | "assistant" | "tool" | "user";

export type BackendMessageType =
  | "thought"
  | "message"
  | "tool_call"
  | "tool_result"
  | "error"
  | "custom";

export type BackendMessageContent =
  | { type: "text"; text: string }
  | {
      type: "tool_call";
      tool_call_id: string;
      name: string;
      args: Record<string, unknown>;
    }
  | { type: "tool_result"; tool_call_id: string; content: unknown };

export interface BackendMessage {
  id: number;
  thread_id: string;
  turn_id: string;
  message_id: string;
  parent_message_id: string | null;
  sequence_num: number;
  role: BackendRole;
  message_type: BackendMessageType;
  content: BackendMessageContent[];
  token_usage: Record<string, unknown>;
  latency_ms: number;
  created_at: string;
  updated_at: string;
}

export type BackendTurnStatus =
  | "running"
  | "completed"
  | "failed"
  | "canceled";

/** GET /conversation/{id}/history 返回的 items 元素（AgenticConversationTurn） */
export interface BackendConversationTurn {
  id: number;
  thread_id: string;
  run_id: string;
  turn_id: string;
  turn_num: number;
  status: BackendTurnStatus;
  token_usage: Record<string, unknown>;
  messages: BackendMessage[];
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// REST 响应 payload 类型（已脱去 { error_code, error_message, response } 信封，
// 对应 http.ts 拦截器拆包后的裸 payload）
// ---------------------------------------------------------------------------

/** 分页响应的公共部分 */
export interface PagedResult<T> {
  items: T[];
  total: number;
}

/** GET /conversation 的响应 payload */
export interface ConversationListResult extends PagedResult<BackendConversation> {
  page: number;
  pageSize: number;
}

/** GET /conversation/{id}/history 的响应 payload */
export interface HistoryResult extends PagedResult<BackendConversationTurn> {
  offset: number;
  limit: number;
}

// ---------------------------------------------------------------------------
// 知识库领域类型（snake_case 镜像后端 app/models/domain/knowledge/knowledge.py）
// ---------------------------------------------------------------------------

/**
 * 知识库/文档/分段共用的状态机：
 * pending → processing → ready → enabled/disabled；删除走 deleting；
 * 解析/嵌入流水线失败进 failed（error_message 记录原因）。
 */
export type KnowledgeStatus =
  | "pending"
  | "processing"
  | "ready"
  | "enabled"
  | "disabled"
  | "deleting"
  | "failed";

/** 知识库实体（GET /knowledge、列表 items 元素） */
export interface BackendKnowledgeBase {
  id: string;
  name: string;
  description: string;
  /** 建库时的嵌入模型标识（创建后不可改，前端只读展示） */
  embedding_model: string;
  weight: number;
  status: KnowledgeStatus;
  /** 文档数（后端冗余计数） */
  doc_num: number;
  created_at: string;
  updated_at: string;
}

/** 知识库文档实体（GET /knowledge/{kbId}/document、列表 items 元素） */
export interface BackendKnowledgeDocument {
  id: string;
  kb_id: string;
  /** 原始文件在对象存储（rustfs）中的 key，前端只读 */
  doc_path: string;
  name: string;
  description: string;
  mime_type: string | null;
  file_size: number | null;
  checksum: string | null;
  weight: number;
  status: KnowledgeStatus;
  /** 处理失败原因（status=failed 时展示，重试成功后置空） */
  error_message: string | null;
  /** 分段数（后端冗余计数） */
  seg_num: number;
  created_at: string;
  updated_at: string;
}

/** GET /knowledge 与 GET /knowledge/{kbId}/document 的响应 payload（同构） */
export interface KnowledgePagedResult<T> extends PagedResult<T> {
  page: number;
  pageSize: number;
}

export type KnowledgeListResult = KnowledgePagedResult<BackendKnowledgeBase>;
export type KnowledgeDocumentListResult =
  KnowledgePagedResult<BackendKnowledgeDocument>;

// ---------------------------------------------------------------------------
// knowledge_search 工具结果（后端 app/components/knowledge/tools.py 输出的
// JSON 字符串，经 ag-ui ToolCallResultEvent.content / 历史 TOOL_RESULT 行透传）
// ---------------------------------------------------------------------------

/** 溯源区域紧凑元组：[页码(0起), x0, y0, x1, y1]（0-1 归一化、左上原点、相对页面宽高） */
export type KnowledgeSourceBbox = [
  number,
  number,
  number,
  number,
  number,
];

/** 检索单来源：LLM 引用（[index] 角标）与前端溯源卡片共用 */
export interface KnowledgeSource {
  index: number;
  kb_id: string;
  doc_id: string;
  doc_name: string;
  position: number;
  score: number;
  content: string;
  page_start: number | null;
  page_end: number | null;
  heading_path: string[];
  /** 原文位置框（存量文档缺失时省略，溯源降级为页码跳转） */
  bboxes?: KnowledgeSourceBbox[];
}

export interface KnowledgeSearchResult {
  sources: KnowledgeSource[];
  notes: string[];
}
