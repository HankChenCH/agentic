
from pydantic import BaseModel, Field


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=25, description="知识库名称")
    description: str = Field(default="", max_length=500, description="知识库详情描述")
    weight: int = Field(default=0, description="管理侧排序权重，越大越靠前")
    isPublic: bool = Field(
        default=False,
        description="公开或私有标识：true 公开（全员可见），false 私有（仅属主可见），缺省私有",
    )


class KnowledgeBaseUpdateRequest(BaseModel):
    """部分更新语义：未提供的字段（None）保持原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=25, description="知识库名称")
    description: str | None = Field(default=None, min_length=0, max_length=500, description="知识库详情描述")
    weight: int | None = Field(default=None, description="管理侧排序权重")
    isPublic: bool | None = Field(default=None, description="公开或私有标识")


class KnowledgeDocumentUpdateRequest(BaseModel):
    """仅元数据更新；doc_path 创建后不可变，更换文件需删除后重新上传。"""

    name: str | None = Field(default=None, min_length=1, max_length=250, description="文档名称")
    description: str | None = Field(default=None, min_length=0, max_length=500, description="文档详情描述")
    weight: int | None = Field(default=None, description="管理侧排序权重")


class KnowledgeSegmentCreateRequest(BaseModel):
    """手动新增分段：追加到文档末尾（position = 现有最大值 +1），不走解析流水线。"""

    content: str = Field(min_length=1, max_length=4000, description="分段内容")


class KnowledgeSegmentUpdateRequest(BaseModel):
    """分段内容整体替换：替换后自动重新嵌入（向量同 UUID 原地重写）。"""

    content: str = Field(min_length=1, max_length=4000, description="分段内容")

