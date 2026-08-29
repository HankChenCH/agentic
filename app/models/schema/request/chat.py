from typing import List

from uuid import UUID
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    # ag-ui 客户端会回传完整本地消息历史：工具消息 id 形如
    # ``{toolCallId}:tool``（可超 36 字符）、工具结果 content 为工具输出
    # 原文（knowledge_search 的 JSON 含溯源信息，可达数十 KB）。服务端
    # 只消费 messages[-1].content（见 agentic endpoint），历史消息仅透传，
    # 上限放宽到只挡异常 payload，不约束正常工具结果。
    id: str = Field(..., description="会话消息标识", min_length=1, max_length=128)
    role: str = Field(..., description="会话角色", min_length=1, max_length=30)
    content: str = Field(..., description="消息内容", min_length=1, max_length=200_000)

class ChatRequest(BaseModel):
    threadId: UUID = Field(..., description="会话标识")
    runId: str = Field(..., description="会话轮次标识", min_length=1, max_length=36)
    messages: List[ChatMessage] = Field(..., description="消息列表")

class CancelRequest(BaseModel):
    # 显式取消按 thread 作用域：一个会话同一时刻只有一个活跃轮次，前端
    # 停止按钮无需跟踪 runId
    threadId: UUID = Field(..., description="要取消当前轮次的会话标识")
