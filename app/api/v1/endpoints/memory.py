"""记忆管理端点：图快照（只读）+ 编辑（L1 事实纠错闭环）——HTTP wiring。

管理侧受众按「服务层两层制」直接消费领域服务（MemoryGraphService /
MemoryAdminService）；业务校验由服务抛 3xxx 业务异常（时间参数 3001、
对象不存在 3002、名称冲突 3005、无变化 3006、参数非法 3008），全局异常
处理器映射为信封响应，端点不做格式判断。

编辑对象的路径引用接受图快照 id 形态（``s:13`` / ``e:5``）或裸数字——
前端详情面板拿到的就是快照 id，按号直达。
"""

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response as RawResponse
from wireup import Injected

from app.models.schema.request.memory import (
    EntityMergeRequest,
    EntitySplitRequest,
    EntityUpdateRequest,
    EpisodeLinkUpdateRequest,
    EpisodeUpdateRequest,
    MaintenancePurgeRequest,
    MaintenanceResetRequest,
    StatementCorrectRequest,
    StatementCreateRequest,
)
from app.models.schema.response.biz_response import Response
from app.services import MemoryAdminService, MemoryGraphService

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


@router.post("/statements")
def create_statement(
    admin_service: Injected[MemoryAdminService],
    request: StatementCreateRequest,
):
    """手工补充事实（origin=MANUAL，抽取裁决恒不取代）。"""
    return Response.success(admin_service.add_statement(request)).to_dict()


@router.patch("/statements/{statement_ref}")
def correct_statement(
    admin_service: Injected[MemoryAdminService],
    statement_ref: str,
    request: StatementCorrectRequest,
):
    """取代式纠正：旧行置 SUPERSEDED 补全时效，新行 ACTIVE 接续（非原地改写）。"""
    return Response.success(admin_service.correct_statement(statement_ref, request)).to_dict()


@router.delete("/statements/{statement_ref}")
def archive_statement(
    admin_service: Injected[MemoryAdminService],
    statement_ref: str,
):
    """归档（软删）：ARCHIVED 后退出当前图谱，时点回放仍可追溯。"""
    return Response.success(admin_service.archive_statement(statement_ref)).to_dict()


@router.patch("/entities/{entity_ref}")
def update_entity(
    admin_service: Injected[MemoryAdminService],
    entity_ref: str,
    request: EntityUpdateRequest,
):
    """实体档案直改：名称/别名/类型（别名全量替换；改名撞他实体名/别名 → 3005）。"""
    return Response.success(admin_service.update_entity(entity_ref, request)).to_dict()


@router.post("/entities/{entity_ref}/merge")
def merge_entity(
    admin_service: Injected[MemoryAdminService],
    entity_ref: str,
    request: EntityMergeRequest,
):
    """错分离合并：source（含历史行）全量并入 target 后删除 source。"""
    return Response.success(admin_service.merge_entity(entity_ref, request)).to_dict()


@router.post("/entities/{entity_ref}/split")
def split_entity(
    admin_service: Injected[MemoryAdminService],
    entity_ref: str,
    request: EntitySplitRequest,
):
    """错合并拆分：所选事实/参与/别名迁往新实体，双方互写拆分禁令。"""
    return Response.success(admin_service.split_entity(entity_ref, request)).to_dict()


@router.delete("/entities/{entity_ref}")
def delete_entity(
    admin_service: Injected[MemoryAdminService],
    entity_ref: str,
):
    """孤立实体清理：无任何事实引用与事件参与才可删除（用户节点受保护）。"""
    return Response.success(admin_service.delete_entity(entity_ref)).to_dict()


@router.patch("/episodes/{episode_ref}")
def update_episode(
    admin_service: Injected[MemoryAdminService],
    episode_ref: str,
    request: EpisodeUpdateRequest,
):
    """事件档案直改：摘要/场景/发生时间（部分更新；scene 空串=清除）。"""
    return Response.success(admin_service.update_episode(episode_ref, request)).to_dict()


@router.delete("/episodes/{episode_ref}")
def delete_episode(
    admin_service: Injected[MemoryAdminService],
    episode_ref: str,
):
    """物理删除事件及其参与边（不可恢复，事件无状态机不走软删）。"""
    return Response.success(admin_service.delete_episode(episode_ref)).to_dict()


@router.get("/maintenance/purge-preview")
def purge_preview(
    admin_service: Injected[MemoryAdminService],
    scope: Annotated[str, Query(pattern="^(day|thread)$")],
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query()] = None,
    threadId: Annotated[str | None, Query()] = None,
):
    """清除影响面预览：statements/activeStatements/episodes/entities 计数。"""
    request = MaintenancePurgeRequest(scope=scope, from_dt=from_, to=to, threadId=threadId)
    return Response.success(admin_service.purge_preview(request)).to_dict()


@router.post("/maintenance/purge")
def purge_memory(
    admin_service: Injected[MemoryAdminService],
    request: MaintenancePurgeRequest,
):
    """范围清除：在效事实归档（软删可回放）、事件物理删除、范围内孤立实体清理。"""
    return Response.success(admin_service.purge_memory(request)).to_dict()


@router.get("/maintenance/export")
def export_memory(admin_service: Injected[MemoryAdminService]):
    """四表全量导出为 JSON 备份文件（含已取代/已归档历史行）。"""
    import json

    payload = json.dumps(admin_service.export_memory(), ensure_ascii=False)
    return RawResponse(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="memory-export.json"'},
    )


@router.post("/maintenance/reset")
def reset_memory(
    admin_service: Injected[MemoryAdminService],
    request: MaintenanceResetRequest,
):
    """整体重置：清空记忆四表并 drop 记忆向量 collection（confirmation 须为「重置」）。"""
    return Response.success(admin_service.reset_all_memory(request)).to_dict()


@router.patch("/episode-links/{link_ref}")
def update_episode_link(
    admin_service: Injected[MemoryAdminService],
    link_ref: str,
    request: EpisodeLinkUpdateRequest,
):
    """参与改挂：换实体/改角色（role 空串=清除；撞唯一组合 → 3007）。"""
    return Response.success(admin_service.update_episode_link(link_ref, request)).to_dict()
