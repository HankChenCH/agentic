"""记忆域测试共享替身：鸭子类型的假模型 / 假向量索引。

刻意不用 BaseChatModel 子类——服务层只调 ``invoke``，鸭子类型即可，
免去 pydantic 字段声明的样板。
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.config.memory import MemoryConfig
from app.services.domain.memory import MemoryVectorHit


class CannedLLM:
    """按调用次序回放预设文本；不足时复用最后一个。"""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list = []

    def invoke(self, messages):
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return SimpleNamespace(content=self.responses[index])


class FakeModelFactory:
    """ModelFactory 的最小替身：create(N) 返回绑定的 CannedLLM。"""

    def __init__(self, llm: CannedLLM):
        self.llm = llm

    def create(self, provider: str):
        return self.llm


class FakeMemoryVectorIndex:
    """MemoryVectorIndex 替身：记录写入、回放预置命中与预置余弦。

    ``preset_hits`` 的 score 是 search 的融合分（仅排序用）；实体消歧的
    合并判定走 ``text_cosine``，其返回值由 ``preset_cosines`` FIFO 回放
    （耗尽后复用最后一个值；未预置时全 0 = 不合并）。
    """

    def __init__(
        self,
        preset_hits: list[MemoryVectorHit] | None = None,
        preset_cosines: list[float] | None = None,
    ):
        self.upserts: list = []
        self.deleted: list = []
        self.searches: list[dict] = []
        self.cosine_calls: list[dict] = []
        self.rebuilds: list = []
        self._preset = preset_hits or []
        self._cosines = list(preset_cosines or [])

    def upsert_many(self, entries):
        self.upserts.extend(entries)

    def delete(self, items):
        self.deleted.extend(items)

    def search(self, query, *, kinds=("statement", "episode"), top_k=8, alpha=0.7):
        self.searches.append({"query": query, "kinds": kinds, "alpha": alpha})
        kept = [h for h in self._preset if h.kind in kinds]
        return kept[:top_k]

    def text_cosine(self, query, contents):
        self.cosine_calls.append({"query": query, "contents": list(contents)})
        if not self._cosines:
            return [0.0 for _ in contents]
        return [
            self._cosines.pop(0) if len(self._cosines) > 1 else self._cosines[0]
            for _ in contents
        ]

    def rebuild(self, entries):
        """记录全量重建入参；常规写路径不应触发（由各用例隐式覆盖）。"""
        self.rebuilds.append(list(entries))


def make_service_config(**overrides):
    """纯内存配置：按 AppConfig 的访问形状包装（service 只读 .memory 分节）。"""
    return SimpleNamespace(memory=MemoryConfig(**overrides))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
