"""记忆管理端点：图快照（P1 只读）——HTTP wiring，业务全在领域服务。

管理侧受众按「服务层两层制」直接消费领域服务（MemoryGraphService）；
时间参数不可解析由服务抛 MemoryInvalidTimeParamError（3001/400），
全局异常处理器映射为信封响应，端点不做格式判断。
"""

from typing import Annotated

from fastapi import APIRouter, Query
from wireup import Injected

from app.models.schema.response.biz_response import Response
from app.services import MemoryGraphService

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.get("/graph")
def memory_graph(
    graph_service: Injected[MemoryGraphService],
    at: Annotated[str | None, Query(
        description="时点回放锚（ISO 日期/日期时间）；缺省返回当前态快照")] = None,
    limit: Annotated[int, Query(ge=1, le=2000, description="各集合的条目上限")] = 300,
):
    """记忆图谱快照：实体/情节为节点、陈述为带谓词边，供力导向图渲染。"""
    return Response.success(graph_service.graph_snapshot(at_iso=at, limit=limit)).to_dict()
