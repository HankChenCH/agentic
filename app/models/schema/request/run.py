from typing import List, Union

from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator

from ag_ui.core.types import InputContent

# 文本内容总量上限（含字符串形态与数组内全部 text part），延续历史口径
_MAX_TEXT_CHARS = 200_000
# 单条消息图片 part 上限：防单条消息塞满上下文，与前端多选场景对齐
_MAX_IMAGE_PARTS = 4
# url source 引用长度上限（本域附件引用与外部图片 URL 都远短于该值）
_MAX_URL_CHARS = 2048

class RunMessage(BaseModel):
    # ag-ui 客户端会回传完整本地消息历史：工具消息 id 形如
    # ``{toolCallId}:tool``（可超 36 字符）、工具结果 content 为工具输出
    # 原文（knowledge_search 的 JSON 含溯源信息，可达数十 KB）。服务端
    # 只消费 messages[-1].content（见 agentic endpoint），历史消息仅透传，
    # 上限放宽到只挡异常 payload，不约束正常工具结果。
    id: str = Field(..., description="会话消息标识", min_length=1, max_length=128)
    role: str = Field(..., description="会话角色", min_length=1, max_length=30)
    # 多模态口径：字符串（纯文本，历史兼容）或 ag-ui InputContent 数组
    # （text/image/audio/video/document，本期图片输入只发 text+image）。
    # 图片以稳定 URL 引用（本域附件）或 data/外部 URL 形态出现；请求体
    # 上限仍由 http.yaml 的 run 作用域把守（1 MiB），data 形态天然受限。
    content: Union[str, List[InputContent]] = Field(
        ...,
        description="消息内容：纯文本字符串或多模态内容数组（ag-ui InputContent）",
    )

    @field_validator("content")
    @classmethod
    def _validate_multimodal(cls, value: Union[str, List[InputContent]]):
        if isinstance(value, str):
            if not (1 <= len(value) <= _MAX_TEXT_CHARS):
                raise ValueError(f"content length must be within [{1}, {_MAX_TEXT_CHARS}]")
            return value
        if not value:
            raise ValueError("content array must not be empty")
        image_count = 0
        text_chars = 0
        for part in value:
            part_type = part.type
            if part_type == "binary":
                # ag-ui 已废弃形态：本项目不接受（客户端附件走 URL 引用）
                raise ValueError("binary content parts are not accepted; reference an uploaded attachment instead")
            if part_type == "image":
                image_count += 1
                if image_count > _MAX_IMAGE_PARTS:
                    raise ValueError(f"at most {_MAX_IMAGE_PARTS} image parts per message")
                source_value = getattr(part.source, "value", "") or ""
                if len(source_value) > _MAX_URL_CHARS and part.source.type == "url":
                    raise ValueError(f"url source must be within {_MAX_URL_CHARS} chars")
            elif part_type == "text":
                text_chars += len(part.text or "")
                if text_chars > _MAX_TEXT_CHARS:
                    raise ValueError(f"total text must be within {_MAX_TEXT_CHARS} chars")
        return value

    @model_validator(mode="after")
    def _must_contain_text_or_image(self):
        # 纯图片消息允许（图示提问），但数组里既无文本也无图片时拒绝
        # （audio/video/document 本期不产生，进来也无法被消费）
        if isinstance(self.content, list) and not any(
            p.type in ("text", "image") for p in self.content
        ):
            raise ValueError("content must contain at least one text or image part")
        return self

    def storage_content(self) -> list[dict]:
        """归一为存储形态：统一内容数组（camelCase key，ag-ui 协议原样），
        供 open_turn 落库与多模态消息构造消费；纯文本折叠为单个 text part。
        ``exclude_none`` 去掉可选字段的空值（如 image 的 metadata: None）。"""
        if isinstance(self.content, str):
            return [{"type": "text", "text": self.content}]
        return [part.model_dump(by_alias=True, exclude_none=True) for part in self.content]

class RunRequest(BaseModel):
    threadId: UUID = Field(..., description="会话标识")
    runId: str = Field(..., description="会话轮次标识", min_length=1, max_length=36)
    messages: List[RunMessage] = Field(..., description="消息列表")

class CancelRequest(BaseModel):
    # 显式取消按 thread 作用域：一个会话同一时刻只有一个活跃轮次，前端
    # 停止按钮无需跟踪 runId
    threadId: UUID = Field(..., description="要取消当前轮次的会话标识")
