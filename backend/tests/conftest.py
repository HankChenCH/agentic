"""共享 fixture：临时 SQLite 引擎（文件库，多 Session 连接可见）。"""

import logging
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlmodel import SQLModel, create_engine

import app.models.domain  # noqa: F401 收集全部表模型

# 记忆/会话用户化后的测试身份：A 为主作用域（测试默认），B 用于跨用户隔离断言
TEST_USER_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
OTHER_USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


class StubLoggerFactory:
    """ConversationService 构造所需的日志工厂替身（退回 stdlib logger）。"""

    def get_logger(self, name):
        return logging.getLogger(name)


class StubUsageService:
    """UsageService 替身：用量记录进内存列表（可断言），不做任何 IO。

    与真实实现同面同语义：record_usage_safe（列表累加）+ usage_sink（闭包
    捕获 sink 上下文，键族归一后落进 records，空用量静默忽略——元素为
    SimpleNamespace）。
    """

    def __init__(self):
        self.records = []

    def record_usage_safe(self, records):
        self.records.extend(records)

    def usage_sink(self, *, user_id, scene, thread_id=None, turn_id=None):
        from app.domain.usage.extract import normalize_usage

        def sink(model_name, usage):
            # 键族归一口径同真实 sink（normalize_usage 对空/非法输入返回空 dict）
            normalized = normalize_usage(usage)
            if not normalized:
                return
            self.records.append(SimpleNamespace(
                user_id=user_id, scene=scene, model=model_name,
                usage=normalized, thread_id=thread_id, turn_id=turn_id,
            ))

        return sink
