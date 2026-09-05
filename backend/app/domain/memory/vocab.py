"""记忆域规范文本与受控词表：谓词基数、事实规范句式、实体档案文本、
内部溯源引用 token 规则的唯一源。

本模块是纯领域词汇（无机制依赖）：components（抽取/消歧/编辑）与
adapters（图谱仓储的 summary 重组）与 application（维护用例的向量条目
组装）共用同一实现——summary/档案文本是向量嵌入源，多处漂移会留下
语义错位的向量。
"""

import re

from app.models.domain.memory import MemoryEntity

# ---------------- 谓词受控词表 ----------------
# 基数标注是裁决降级规则的唯一依据：抽取与裁决的 LLM 输出若使用词表外
# 谓词，按 MULTI 兜底（可并存，不覆盖已有事实）——宁可多并存也不误删。
# 单值谓词的新值到来时旧行置 SUPERSEDED。

SINGLE_VALUE_PREDICATES = frozenset({
    "姓名", "职业", "居住地", "所属公司", "所属组织", "职位",
    "学历", "毕业院校", "目标", "约束", "称呼",
})

MULTI_VALUE_PREDICATES = frozenset({
    "偏好", "技能", "认识", "参与", "担任", "擅长", "兴趣",
    "健康状况", "习惯",
})

EVENT_RELATION_PREDICATES = frozenset({
    "引发", "冲突", "延期", "促成", "阻碍", "取消", "变更",
})


def cardinality(predicate: str) -> str:
    """返回 'single' | 'multi'：未知谓词按 multi 兜底（见上方词表说明）。"""
    if predicate in SINGLE_VALUE_PREDICATES:
        return "single"
    return "multi"


def fact_summary(subject_name: str, predicate: str, object_label: str) -> str:
    """陈述 summary 的唯一规范句式（向量嵌入源）。

    单值谓词「A的X是B」、其余「AXB」。抽取落库与人工编辑改挂共用此实现——
    客体实体被合并/拆分改挂时 summary 必须按新归属重组，否则留下语义错位
    的向量。
    """
    if cardinality(predicate) == "single":
        return f"{subject_name}的{predicate}是{object_label}"
    return f"{subject_name}{predicate}{object_label}"


# ---------------- 实体档案文本 ----------------

def entity_content(row: MemoryEntity) -> str:
    """实体档案文本：向量写入与余弦判定共用同一内容（name+aliases）。"""
    alias_bit = f"（{'、'.join(row.aliases)}）" if row.aliases else ""
    return f"{row.name}{alias_bit}"


# ---------------- 内部溯源引用 token ----------------
# 渲染层给深入工具结果编的内部溯源键（#S13/§E3/#T1a2b/#实体7 形态）是给人
# 看的键，不是实体名——曾被助手复读后经抽取管线吸回实体 aliases（"#S13"
# 污染），入库前必须以本规则拦截。

INTERNAL_REF_TOKEN_RE = re.compile(r"^(?:#S\d+|§E\d+|#T[0-9a-f]+|#实体\d+)$")
INTERNAL_REF_IN_TEXT_RE = re.compile(r"#S\d+|§E\d+|#T[0-9a-f]+|#实体\d+")


def is_internal_ref(text: str) -> bool:
    """该 token 是否为渲染层内部溯源引用（不可作为实体名/别名入库）。"""
    return bool(INTERNAL_REF_TOKEN_RE.match(text.strip()))
