"""记忆编辑请求模型（camelCase，与 /memory 域对外契约同形）。

编辑语义与 knowledge 域一致：部分更新——未提供的字段（None）保持原值。
"""

from pydantic import BaseModel, ConfigDict, Field


class StatementCreateRequest(BaseModel):
    """手工补充事实：主体二选一（entityId 直引 / name 引用不存在则建），
    客体二选一（entityId / 字面量 objectText）。"""

    subjectEntityId: int | None = Field(default=None, description="主体实体 id")
    subjectName: str | None = Field(default=None, min_length=1, max_length=100, description="主体名称（不存在则按此名新建实体）")
    subjectEntityType: str = Field(default="OTHER", description="仅新建主体时生效的实体类型")
    predicate: str = Field(min_length=1, max_length=50, description="谓词（受控词表，基数规则见 components/memory/vocab.py）")
    objectEntityId: int | None = Field(default=None, description="客体实体 id（与 objectText 二选一）")
    objectText: str | None = Field(default=None, min_length=1, max_length=200, description="字面量客体（与 objectEntityId 二选一）")
    summary: str | None = Field(default=None, min_length=1, max_length=300, description="整句表述；缺省按规范句式组装")
    validFrom: str | None = Field(default=None, max_length=30, description="生效起点 ISO 日期/日期时间；缺省为当前时刻")
    note: str | None = Field(default=None, max_length=300, description="人工备注，落 evidence")


class StatementCorrectRequest(BaseModel):
    """取代式纠正：未提供的字段保持原值；提交后旧行 SUPERSEDED、新行 ACTIVE。"""

    predicate: str | None = Field(default=None, min_length=1, max_length=50, description="新谓词")
    objectEntityId: int | None = Field(default=None, description="新客体实体 id（与 objectText 二选一提供）")
    objectText: str | None = Field(default=None, min_length=1, max_length=200, description="新字面量客体")
    summary: str | None = Field(default=None, min_length=1, max_length=300, description="新整句表述；缺省按新值重组")
    validFrom: str | None = Field(default=None, max_length=30, description="新生效起点 ISO 日期/日期时间")
    note: str | None = Field(default=None, max_length=300, description="纠正备注，落新行 evidence")


class EntityUpdateRequest(BaseModel):
    """实体档案直改：aliases 为全量替换（编辑语义，区别于合并的并集收敛）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100, description="规范名（纯专名）")
    aliases: list[str] | None = Field(default=None, description="别名集全量替换")
    entityType: str | None = Field(default=None, description="实体类型（PERSON/OBJECT/PLACE/ORG/CONCEPT/OTHER）")


class EntityMergeRequest(BaseModel):
    """错分离合并：source 全量并入 target（含历史行）后删除 source。"""

    targetRef: str = Field(min_length=1, max_length=30, description="存留方实体引用（图快照 id 形态 e:{id}）")


class EntitySplitRequest(BaseModel):
    """错合并拆分：所选内容迁往新实体；三类选择至少一项。"""

    name: str = Field(min_length=1, max_length=100, description="新实体规范名（撞其他实体名/别名 → 3005）")
    entityType: str = Field(default="OTHER", description="新实体类型")
    aliases: list[str] = Field(default_factory=list, description="随迁别名")
    statementIds: list[str] = Field(default_factory=list, description="迁移陈述引用（s:{id}，须属于 source）")
    episodeLinkIds: list[str] = Field(default_factory=list, description="迁移事件参与引用（l:{id}，须属于 source）")


class EpisodeUpdateRequest(BaseModel):
    """事件档案直改（部分更新）；scene 传空串表示清除场景。"""

    summary: str | None = Field(default=None, min_length=1, max_length=500, description="事件梗概（向量嵌入源）")
    scene: str | None = Field(default=None, max_length=50, description="场景标签；空串=清除")
    occurredAt: str | None = Field(default=None, max_length=30, description="发生时间 ISO 日期/日期时间")


class EpisodeLinkUpdateRequest(BaseModel):
    """参与改挂：换实体/改角色；role 传空串表示清除角色。"""

    entityId: int | None = Field(default=None, description="新参与实体 id")
    role: str | None = Field(default=None, max_length=50, description="参与角色；空串=清除")


class MaintenancePurgeRequest(BaseModel):
    """范围清除：scope=day 用 from/to（本地自然日起止 ISO 时刻，时区由前端决定）；
    scope=thread 用 threadId（会话遗忘不动实体）。"""

    model_config = ConfigDict(populate_by_name=True)

    scope: str = Field(pattern="^(day|thread)$", description="清除范围")
    from_dt: str | None = Field(default=None, alias="from", max_length=30, description="起始 ISO 时刻（含）")
    to: str | None = Field(default=None, max_length=30, description="结束 ISO 时刻（含）")
    threadId: str | None = Field(default=None, max_length=40, description="会话 thread id")


class MaintenanceResetRequest(BaseModel):
    """整体重置：confirmation 必须逐字为「重置」，防误触。"""

    confirmation: str = Field(min_length=1, max_length=10, description="确认文字，必须为「重置」")
