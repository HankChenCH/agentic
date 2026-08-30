"""共享 fixture：临时 SQLite 引擎（文件库，多 Session 连接可见）。"""

import logging
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
