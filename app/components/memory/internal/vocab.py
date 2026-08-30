"""谓词受控词表：基数标注是裁决降级规则的唯一依据。

抽取与裁决的 LLM 输出若使用词表外谓词，按 MULTI 兜底（可并存，不覆盖
已有事实）——宁可多并存也不误删。单值谓词的新值到来时旧行置 SUPERSEDED。
"""

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
    """返回 'single' | 'multi'：未知谓词按 multi 兜底（见模块 docstring）。"""
    if predicate in SINGLE_VALUE_PREDICATES:
        return "single"
    return "multi"


def fact_summary(subject_name: str, predicate: str, object_label: str) -> str:
    """陈述 summary 的唯一规范句式（向量嵌入源）。

    单值谓词「A的X是B」、其余「AXB」。抽取落库（service._fact_summary）与
    人工编辑改挂（editor）共用此实现——客体实体被合并/拆分改挂时 summary
    必须按新归属重组，否则留下语义错位的向量。
    """
    if cardinality(predicate) == "single":
        return f"{subject_name}的{predicate}是{object_label}"
    return f"{subject_name}{predicate}{object_label}"
