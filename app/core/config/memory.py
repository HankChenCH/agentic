"""记忆系统配置（设计定稿 §8 配置面）。"""

from pydantic import BaseModel, Field


class RecallConfig(BaseModel):
    """召回检索参数。"""

    top_k: int = Field(default=8, description="向量混合检索条数")
    alpha: float = Field(default=0.7, description="Weaviate hybrid 权重：<1 混 BM25，1 纯向量")
    expansion_limit: int = Field(default=12, description="图扩散（1-hop）/事件关联的单次限额")
    fast_limit: int = Field(default=10, description="快速回忆块的事实行数上限")


class ScoreConfig(BaseModel):
    """评分权重与遗忘曲线形状。"""

    relevance_weight: float = Field(default=0.5, description="相关度权重 α")
    recency_weight: float = Field(default=0.2, description="时近性权重 β")
    importance_weight: float = Field(default=0.3, description="重要度(含访问加成)权重 γ")
    recency_half_life_days: float = Field(
        default=14.0, description="遗忘半衰期天数：距上次活跃隔这么久，时近性衰减到一半"
    )


class ResolutionConfig(BaseModel):
    similarity_threshold: float = Field(
        default=0.85, description="实体消歧的向量相似合并阈值"
    )


class DeepConfig(BaseModel):
    max_rounds: int = Field(default=3, description="单次运行深度回忆工具调用轮数上限（提示性约束，靠工具描述落实）")


class RenderConfig(BaseModel):
    """渲染模板裁剪面。"""

    max_fragments: int = Field(default=3, description="单次输出的记忆片段数上限")
    topology_max_edges: int = Field(default=8, description="单个片段拓扑行数上限")
    evidence_max_quotes: int = Field(default=3, description="单个片段证据条目上限")


class MemoryConfig(BaseModel):
    """记忆系统配置。

    ``extraction_provider`` 引用 ``LLMConfig.providers`` 的一个 entry key——
    记忆抽取复用对话模型供应商，不单独建供应商表；key 不存在时由
    ModelFactory.create 抛出明确 ValueError。
    """

    enabled: bool = Field(
        default=True,
        description="是否启用记忆写入；关闭后 remember 跳过，召回自然为空",
    )
    extraction_provider: str = Field(
        default="deepseek-flash",
        description="记忆抽取/裁决使用的裸聊天模型 entry key",
    )
    recall: RecallConfig = Field(default_factory=RecallConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    resolution: ResolutionConfig = Field(default_factory=ResolutionConfig)
    deep: DeepConfig = Field(default_factory=DeepConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
