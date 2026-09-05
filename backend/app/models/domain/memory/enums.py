"""记忆领域枚举：实体类型、陈述状态、来源标记。

值即大写成员名——StrEnum 本就是 str，库中裸字符串与枚举成员可直接比较。
"""

import enum


class EntityType(str, enum.Enum):
    """实体类型。刻意没有 EVENT——事件属情节层（memory_episode），
    渲染期才投影为 §E 事件节点，存储端不为它建实体行。"""

    PERSON = "PERSON"
    OBJECT = "OBJECT"
    PLACE = "PLACE"
    ORG = "ORG"
    CONCEPT = "CONCEPT"
    OTHER = "OTHER"


class StatementState(str, enum.Enum):
    """陈述生命周期。SUPERSEDED 表示被新值取代的历史切片：
    补全 valid_to / invalidated_at 后永久保留（时点回放的数据基础）。"""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class MemoryOrigin(str, enum.Enum):
    """来源标记。EXTRACTED 条目受巩固管线裁决管辖；
    MANUAL（人工建改）在裁决中被恒 SKIP——LLM 无权篡改人工事实。"""

    EXTRACTED = "EXTRACTED"
    MANUAL = "MANUAL"


def _register_values():
    # 兜底防护：保证值与成员名一致，使库中裸字符串可与枚举互比
    for cls in (EntityType, StatementState, MemoryOrigin):
        for member in cls:
            assert member.value == member.name, f"{cls.__name__}.{member.name} 值漂移"


_register_values()
