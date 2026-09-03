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
  user_id: string;
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
  | { type: "tool_result"; tool_call_id: string; content: unknown }
  | {
      // 多模态 image part（ag-ui InputContent 存储形态，camelCase 特例）：
      // source 为稳定附件引用（url）或 base64 内联（data）
      type: "image";
      source:
        | { type: "url"; value: string; mimeType?: string }
        | { type: "data"; value: string; mimeType: string };
    };

/** POST /agentic/attachments 返回的附件元数据 */
export interface BackendAttachment {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  /** 稳定相对引用（API 根相对路径），入库进消息 content，永不过期 */
  url: string;
}

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
  /** 分支基点轮次（兄弟语义：同 parent 的轮次互为同一问答的重试/编辑变体） */
  parent_turn_id: string | null;
  /** 同一问答（兄弟间）第几次尝试，从 1 开始 */
  attempt_no: number;
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
  /** 活跃叶子轮次（末梢扇形的 head），前端据此确定分支树的当前路径 */
  active_turn_id?: string | null;
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
  /** 属主用户 id（公开库对全员可见，私有库仅属主可见） */
  user_id: string;
  name: string;
  description: string;
  /** 建库时的嵌入模型标识（创建后不可改，前端只读展示） */
  embedding_model: string;
  /** 公开或私有标识：true 公开（全员可见），false 私有（仅属主可见） */
  is_public: boolean;
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

/**
 * 分段元数据（镜像后端 DocumentSegment.meta：解析/分块产出，管理侧只读）。
 * 手动新增的分段无解析溯源，meta 为 null。
 */
export interface KnowledgeSegmentMeta {
  page_start?: number | null;
  page_end?: number | null;
  /** 标题面包屑（h1 > h2 …） */
  heading_path?: string[];
  /** 块类型（text/table/image…） */
  types?: string[];
  /** 解析资产（图片等）的对象存储 key */
  assets?: string[];
  /** 原文位置框：page 0 起；bbox [x0,y0,x1,y1] 0-1 归一化（与 PdfHighlight 同坐标系） */
  bboxes?: { page: number; bbox: number[] }[];
}

/** 文档分段实体（GET /knowledge/{kbId}/document/{docId}/segment 的 items 元素） */
export interface BackendDocumentSegment {
  id: string;
  kb_id: string;
  doc_id: string;
  /** 段落位置（0 起，文档内唯一） */
  position: number;
  content: string;
  meta: KnowledgeSegmentMeta | null;
  /** 内容字数（按字符计） */
  word_count: number;
  status: KnowledgeStatus;
  created_at: string;
  updated_at: string;
}

export type KnowledgeSegmentListResult =
  KnowledgePagedResult<BackendDocumentSegment>;

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

/** 检索单来源：LLM 引用（[index] 角标）与前端溯源卡片共用（检索与定位读取同形） */
export interface KnowledgeSource {
  index: number;
  kb_id: string;
  doc_id: string;
  doc_name: string;
  position: number;
  /** 混合检索相关度（0-1）；定位读取（knowledge_context）无检索分，字段省略 */
  score?: number;
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

/** 工具能力目录：后端 GET /agentic/tool-catalog 的展示元数据（title 仅用于 UI） */
export interface ToolCatalogTool {
  name: string;
  /** 中文展示标题；后端未声明时为 null（前端走注册表/机器名降级链） */
  title: string | null;
  description: string;
  parameters: Record<string, unknown>;
}

export interface ToolCatalogComponent {
  component: string;
  title: string;
  description: string;
  tools: ToolCatalogTool[];
}

export interface ToolCatalog {
  components: ToolCatalogComponent[];
}
