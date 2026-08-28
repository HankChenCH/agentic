"""共享 fixture：临时 SQLite 引擎（文件库，多 Session 连接可见）。"""

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
