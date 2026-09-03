import {
  deleteJson,
  getBinary,
  getJson,
  patchJson,
  postForm,
  postJson,
} from "@/lib/http";
import type {
  BackendDocumentSegment,
  BackendKnowledgeBase,
  BackendKnowledgeDocument,
  KnowledgeDocumentListResult,
  KnowledgeListResult,
  KnowledgeSegmentListResult,
} from "@/services/types";

/**
 * 知识库相关 REST 服务（对应后端 app/api/v1/endpoints/knowledge.py）。
 *
 * 所有方法都经 @/lib/http 的 axios 实例（拦截器已自动把
 * error_code !== 0 转成 BizError），这里拿到的是拆过信封的干净 payload。
 * 文档是知识库的子资源，路径统一带 kbId 前缀。
 */

/** POST /knowledge 的请求体（embedding_model 由后端从 llm.yaml 取默认值） */
export interface KnowledgeBaseCreateInput {
  name: string;
  description?: string;
  weight?: number;
  /** 公开或私有标识，缺省私有（仅属主可见） */
  isPublic?: boolean;
}

/** PATCH /knowledge/{kbId} 的请求体（部分更新：未传字段保持原值） */
export interface KnowledgeBaseUpdateInput {
  name?: string;
  description?: string;
  weight?: number;
  isPublic?: boolean;
}

/** PATCH /knowledge/{kbId}/document/{docId} 的请求体（仅元数据） */
export interface KnowledgeDocumentUpdateInput {
  name?: string;
  description?: string;
  weight?: number;
}

export const knowledgeService = {
  /**
   * 分页拉取知识库列表。
   * GET /knowledge?page=&pageSize=
   */
  async listKnowledgeBases(
    page = 1,
    pageSize = 12,
  ): Promise<KnowledgeListResult> {
    return getJson<KnowledgeListResult>("/knowledge", {
      params: { page, pageSize },
    });
  },

  /**
   * 查询单个知识库。
   * GET /knowledge/{kbId}
   */
  async getKnowledgeBase(kbId: string): Promise<BackendKnowledgeBase> {
    return getJson<BackendKnowledgeBase>(`/knowledge/${kbId}`);
  },

  /**
   * 创建知识库。
   * POST /knowledge
   */
  async createKnowledgeBase(
    input: KnowledgeBaseCreateInput,
  ): Promise<BackendKnowledgeBase> {
    return postJson<BackendKnowledgeBase>("/knowledge", input);
  },

  /**
   * 更新知识库元数据（部分更新语义）。
   * PATCH /knowledge/{kbId}
   */
  async updateKnowledgeBase(
    kbId: string,
    input: KnowledgeBaseUpdateInput,
  ): Promise<BackendKnowledgeBase> {
    return patchJson<BackendKnowledgeBase>(`/knowledge/${kbId}`, input);
  },

  /**
   * 删除知识库。后端异步删除：返回的是标记为 deleting 的实体快照。
   * DELETE /knowledge/{kbId}
   */
  async deleteKnowledgeBase(kbId: string): Promise<BackendKnowledgeBase> {
    return deleteJson<BackendKnowledgeBase>(`/knowledge/${kbId}`);
  },

  /**
   * 启用/停用知识库。
   * POST /knowledge/{kbId}/enable | /disable
   */
  async setKnowledgeEnabled(
    kbId: string,
    enabled: boolean,
  ): Promise<BackendKnowledgeBase> {
    return postJson<BackendKnowledgeBase>(
      `/knowledge/${kbId}/${enabled ? "enable" : "disable"}`,
    );
  },

  /**
   * 上传文档（multipart：file + name + description，单文件单请求）。
   * POST /knowledge/{kbId}/document
   */
  async uploadDocument(
    kbId: string,
    file: File,
    options: { name?: string; description?: string } = {},
  ): Promise<BackendKnowledgeDocument> {
    const form = new FormData();
    form.append("file", file);
    if (options.name) form.append("name", options.name);
    if (options.description) form.append("description", options.description);
    return postForm<BackendKnowledgeDocument>(
      `/knowledge/${kbId}/document`,
      form,
    );
  },

  /**
   * 拉取文档原始文件字节（预览用，二进制响应不走信封）。
   * GET /knowledge/{kbId}/document/{docId}/file
   * 上传上限 50MB，超时放宽到 60s（实例默认 30s 偏紧）。
   * ``signal``：调用方（PdfViewer）在文档切换/重挂载时中断过期请求，
   * 避免大文件重复下载。
   */
  async getDocumentFile(
    kbId: string,
    docId: string,
    signal?: AbortSignal,
  ): Promise<ArrayBuffer> {
    return getBinary(`/knowledge/${kbId}/document/${docId}/file`, {
      timeout: 60_000,
      signal,
    });
  },

  /**
   * 查询单个文档。
   * GET /knowledge/{kbId}/document/{docId}
   */
  async getDocument(
    kbId: string,
    docId: string,
  ): Promise<BackendKnowledgeDocument> {
    return getJson<BackendKnowledgeDocument>(
      `/knowledge/${kbId}/document/${docId}`,
    );
  },

  /**
   * 分页拉取知识库下的文档列表。
   * GET /knowledge/{kbId}/document?page=&pageSize=
   */
  async listDocuments(
    kbId: string,
    page = 1,
    pageSize = 20,
  ): Promise<KnowledgeDocumentListResult> {
    return getJson<KnowledgeDocumentListResult>(
      `/knowledge/${kbId}/document`,
      { params: { page, pageSize } },
    );
  },

  /**
   * 更新文档元数据（doc_path 不可变，换文件需删除后重传）。
   * PATCH /knowledge/{kbId}/document/{docId}
   */
  async updateDocument(
    kbId: string,
    docId: string,
    input: KnowledgeDocumentUpdateInput,
  ): Promise<BackendKnowledgeDocument> {
    return patchJson<BackendKnowledgeDocument>(
      `/knowledge/${kbId}/document/${docId}`,
      input,
    );
  },

  /**
   * 删除文档。后端异步清理向量，返回标记为 deleting 的快照。
   * DELETE /knowledge/{kbId}/document/{docId}
   */
  async deleteDocument(
    kbId: string,
    docId: string,
  ): Promise<BackendKnowledgeDocument> {
    return deleteJson<BackendKnowledgeDocument>(
      `/knowledge/${kbId}/document/${docId}`,
    );
  },

  /**
   * 补发文档处理任务（worker 掉线导致任务丢失、或 failed 后重试）。
   * POST /knowledge/{kbId}/document/{docId}/retry
   */
  async retryDocument(
    kbId: string,
    docId: string,
  ): Promise<BackendKnowledgeDocument> {
    return postJson<BackendKnowledgeDocument>(
      `/knowledge/${kbId}/document/${docId}/retry`,
    );
  },

  /**
   * 启用/停用文档（停用后不参与检索）。
   * POST /knowledge/{kbId}/document/{docId}/enable | /disable
   */
  async setDocumentEnabled(
    kbId: string,
    docId: string,
    enabled: boolean,
  ): Promise<BackendKnowledgeDocument> {
    return postJson<BackendKnowledgeDocument>(
      `/knowledge/${kbId}/document/${docId}/${enabled ? "enable" : "disable"}`,
    );
  },

  // -----------------------------------------------------------------------
  // 文档分段管理（分段是文档的子资源；写操作要求文档处于稳定态）
  // -----------------------------------------------------------------------

  /**
   * 分页拉取文档分段（按 position 有序，keyword 非空时按内容模糊过滤）。
   * GET /knowledge/{kbId}/document/{docId}/segment?page=&pageSize=&keyword=
   */
  async listSegments(
    kbId: string,
    docId: string,
    page = 1,
    pageSize = 20,
    keyword?: string,
  ): Promise<KnowledgeSegmentListResult> {
    return getJson<KnowledgeSegmentListResult>(
      `/knowledge/${kbId}/document/${docId}/segment`,
      { params: { page, pageSize, ...(keyword ? { keyword } : {}) } },
    );
  },

  /**
   * 手动新增分段：追加到文档末尾，即时就绪并写入向量库。
   * POST /knowledge/{kbId}/document/{docId}/segment
   */
  async createSegment(
    kbId: string,
    docId: string,
    input: { content: string },
  ): Promise<BackendDocumentSegment> {
    return postJson<BackendDocumentSegment>(
      `/knowledge/${kbId}/document/${docId}/segment`,
      input,
    );
  },

  /**
   * 编辑分段内容（整体替换，后端自动重新嵌入）。
   * PATCH /knowledge/{kbId}/document/{docId}/segment/{segmentId}
   */
  async updateSegment(
    kbId: string,
    docId: string,
    segmentId: string,
    input: { content: string },
  ): Promise<BackendDocumentSegment> {
    return patchJson<BackendDocumentSegment>(
      `/knowledge/${kbId}/document/${docId}/segment/${segmentId}`,
      input,
    );
  },

  /**
   * 删除分段（后端同步删行并清理对应向量）。
   * DELETE /knowledge/{kbId}/document/{docId}/segment/{segmentId}
   */
  async deleteSegment(
    kbId: string,
    docId: string,
    segmentId: string,
  ): Promise<BackendDocumentSegment> {
    return deleteJson<BackendDocumentSegment>(
      `/knowledge/${kbId}/document/${docId}/segment/${segmentId}`,
    );
  },

  /**
   * 启用/停用分段（禁用后不参与检索召回）。
   * POST /knowledge/{kbId}/document/{docId}/segment/{segmentId}/enable | /disable
   */
  async setSegmentEnabled(
    kbId: string,
    docId: string,
    segmentId: string,
    enabled: boolean,
  ): Promise<BackendDocumentSegment> {
    return postJson<BackendDocumentSegment>(
      `/knowledge/${kbId}/document/${docId}/segment/${segmentId}/${enabled ? "enable" : "disable"}`,
    );
  },

  /**
   * 整篇重分段：重新走解析→分块→向量全量重写（手动分段/禁用一并被替换）。
   * POST /knowledge/{kbId}/document/{docId}/rechunk
   */
  async rechunkDocument(
    kbId: string,
    docId: string,
  ): Promise<BackendKnowledgeDocument> {
    return postJson<BackendKnowledgeDocument>(
      `/knowledge/${kbId}/document/${docId}/rechunk`,
    );
  },
};
