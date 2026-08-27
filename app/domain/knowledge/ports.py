"""领域侧端口（依赖倒置）：协议定义在领域层，实现由外层以
``@injectable(as_type=...)`` 回填，保证依赖箭头仍指向领域。

当前端口：
- ``AgentCatalog``：智能体标识目录。绑定校验需要「agent 是否已注册」，
  但领域层不得 import ``app.agents``——实现在 ``app/agents/catalog.py``
  （读 AGENT_REGISTRY），经 wireup 按本协议类型注入。
"""

from typing import Protocol


class AgentCatalog(Protocol):
    def exists(self, agent_id: str) -> bool: ...

    def ids(self) -> list[str]: ...
