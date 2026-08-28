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
    """MemoryVectorIndex 替身：记录写入、回放预置命中。"""

    def __init__(self, preset_hits: list[MemoryVectorHit] | None = None):
        self.upserts: list = []
        self.deleted: list = []
        self.searches: list[dict] = []
        self._preset = preset_hits or []

    def upsert_many(self, entries):
        self.upserts.extend(entries)

    def delete(self, items):
        self.deleted.extend(items)

    def search(self, query, *, kinds=("statement", "episode"), top_k=8, alpha=0.7):
        self.searches.append({"query": query, "kinds": kinds, "alpha": alpha})
        kept = [h for h in self._preset if h.kind in kinds]
        return kept[:top_k]

    def rebuild(self, entries):
        raise AssertionError("rebuild 不应被常规路径触发")


def make_service_config(**overrides):
    """纯内存配置：按 AppConfig 的访问形状包装（service 只读 .memory 分节）。"""
    return SimpleNamespace(memory=MemoryConfig(**overrides))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
