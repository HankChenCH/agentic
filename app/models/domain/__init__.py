"""领域模型聚合入口：side-effect 导入全部表模型包。

SQLModel 表模型靠类定义注册到 ``SQLModel.metadata``——需要收集完整
metadata 的场景（alembic env.py、测试 fixture）只需
``import app.models.domain``，无需逐包列举。
"""

import app.models.domain.agentic  # noqa: F401
import app.models.domain.knowledge  # noqa: F401
import app.models.domain.memory  # noqa: F401 记忆 v2 四表
import app.models.domain.user  # noqa: F401 用户表
