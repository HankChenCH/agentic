from app.components.memory.service import MemoryService


def build_memory_tools(memory_service: MemoryService) -> list:
    """构造记忆工具集（供智能体 build_tools 装配），闭包绑定 MemoryService。"""

    def memory_recall() -> str:
        """
        查询系统的全部长期记忆（用户背景、偏好、历史对话中沉淀的事实）。
        当问题涉及用户个人情况、以往提过的信息且当前上下文中没有时，先调用本工具再回答。
        """
        memories = memory_service.recall()
        if len(memories) == 0:
            return "暂无记忆"

        lines = [
            f"{index}. {memory.content}（{memory.created_at.strftime('%Y-%m-%d %H:%M')} 记录）"
            for index, memory in enumerate(memories, start=1)
        ]
        return "\n".join(lines)

    return [memory_recall]
