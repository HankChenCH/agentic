"""共享 fixture：临时 SQLite 引擎（文件库，多 Session 连接可见）。"""

import logging

import pytest
from sqlmodel import SQLModel, create_engine

import app.models.domain.agentic  # noqa: F401 收集全部表模型
import app.models.domain.knowledge  # noqa: F401
import app.models.domain.memory  # noqa: F401 记忆 v2 四表


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


class StubLoggerFactory:
    """ConversationService 构造所需的日志工厂替身（退回 stdlib logger）。"""

    def get_logger(self, name):
        return logging.getLogger(name)


class FakeCancelSignalStore:
    """``CancelSignalStore`` 端口的内存实现（第二实现，同时是
    app.infrastructures.redis.RedisCancelSignalStore 的测试替身）。"""

    def __init__(self):
        self.canceled: set[str] = set()
        self.fail_writes = False

    def cancel(self, thread_id):
        if self.fail_writes:
            raise RuntimeError("store down")
        self.canceled.add(str(thread_id))

    def is_canceled(self, thread_id):
        return str(thread_id) in self.canceled

    def clear(self, thread_id):
        self.canceled.discard(str(thread_id))
