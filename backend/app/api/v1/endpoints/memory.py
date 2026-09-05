"""记忆管理端点：图快照（只读）+ 编辑（L1 事实纠错闭环）——HTTP wiring。

管理侧受众经 application 记忆用例门面（MemoryAppService）；业务校验由
领域服务抛 3xxx 业务异常（时间参数 3001、对象不存在 3002、名称冲突 3005、
无变化 3006、参数非法 3008），全局异常处理器映射为信封响应，端点不做
格式判断。

记忆是用户级数据：全部端点经 require_user 取身份，领域服务在用户作用域
内读写（他人对象一律「不存在」，不泄露存在性）。

编辑对象的路径引用接受图快照 id 形态（``s:13`` / ``e:5``）或裸数字——
前端详情面板拿到的就是快照 id，按号直达。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response as RawResponse
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
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
from app.application import MemoryAppService

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.get("/graph")
def memory_graph(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    at: Annotated[str | None, Query(
        description="时点回放锚（ISO 日期/日期时间）；缺省返回当前态快照")] = None,
    limit: Annotated[int, Query(ge=1, le=2000, description="各集合的条目上限")] = 300,
):
    """记忆图谱快照：实体/情节为节点、陈述为带谓词边，供力导向图渲染。"""
    return Response.success(app_service.graph_snapshot(user_id=principal.user_id, at_iso=at, limit=limit)).to_dict()


@router.post("/statements")
def create_statement(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    request: StatementCreateRequest,
):
    """手工补充事实（origin=MANUAL，抽取裁决恒不取代）。"""
    return Response.success(app_service.add_statement(principal.user_id, request)).to_dict()


@router.patch("/statements/{statement_ref}")
def correct_statement(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    statement_ref: str,
    request: StatementCorrectRequest,
):
    """取代式纠正：旧行置 SUPERSEDED 补全时效，新行 ACTIVE 接续（非原地改写）。"""
    return Response.success(app_service.correct_statement(principal.user_id, statement_ref, request)).to_dict()


@router.delete("/statements/{statement_ref}")
def archive_statement(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    statement_ref: str,
):
    """归档（软删）：ARCHIVED 后退出当前图谱，时点回放仍可追溯。"""
    return Response.success(app_service.archive_statement(principal.user_id, statement_ref)).to_dict()


@router.patch("/entities/{entity_ref}")
def update_entity(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    entity_ref: str,
    request: EntityUpdateRequest,
):
    """实体档案直改：名称/别名/类型（别名全量替换；改名撞他实体名/别名 → 3005）。"""
    return Response.success(app_service.update_entity(principal.user_id, entity_ref, request)).to_dict()


@router.post("/entities/{entity_ref}/merge")
def merge_entity(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    entity_ref: str,
    request: EntityMergeRequest,
):
    """错分离合并：source（含历史行）全量并入 target 后删除 source。"""
    return Response.success(app_service.merge_entity(principal.user_id, entity_ref, request)).to_dict()


@router.post("/entities/{entity_ref}/split")
def split_entity(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    entity_ref: str,
    request: EntitySplitRequest,
):
    """错合并拆分：所选事实/参与/别名迁往新实体，双方互写拆分禁令。"""
    return Response.success(app_service.split_entity(principal.user_id, entity_ref, request)).to_dict()


@router.delete("/entities/{entity_ref}")
def delete_entity(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    entity_ref: str,
):
    """孤立实体清理：无任何事实引用与事件参与才可删除（用户节点受保护）。"""
    return Response.success(app_service.delete_entity(principal.user_id, entity_ref)).to_dict()


@router.patch("/episodes/{episode_ref}")
def update_episode(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    episode_ref: str,
    request: EpisodeUpdateRequest,
):
    """事件档案直改：摘要/场景/发生时间（部分更新；scene 空串=清除）。"""
    return Response.success(app_service.update_episode(principal.user_id, episode_ref, request)).to_dict()


@router.delete("/episodes/{episode_ref}")
def delete_episode(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    episode_ref: str,
):
    """物理删除事件及其参与边（不可恢复，事件无状态机不走软删）。"""
    return Response.success(app_service.delete_episode(principal.user_id, episode_ref)).to_dict()


@router.get("/maintenance/purge-preview")
def purge_preview(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    scope: Annotated[str, Query(pattern="^(day|thread)$")],
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query()] = None,
    threadId: Annotated[str | None, Query()] = None,
):
    """清除影响面预览：statements/activeStatements/episodes/entities 计数。"""
    request = MaintenancePurgeRequest(scope=scope, from_dt=from_, to=to, threadId=threadId)
    return Response.success(app_service.purge_preview(principal.user_id, request)).to_dict()


@router.post("/maintenance/purge")
def purge_memory(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    request: MaintenancePurgeRequest,
):
    """范围清除：在效事实归档（软删可回放）、事件物理删除、范围内孤立实体清理。"""
    return Response.success(app_service.purge_memory(principal.user_id, request)).to_dict()


@router.get("/maintenance/export")
def export_memory(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
):
    """四表全量导出为 JSON 备份文件（含已取代/已归档历史行）。"""
    import json

    payload = json.dumps(app_service.export_memory(principal.user_id), ensure_ascii=False)
    return RawResponse(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="memory-export.json"'},
    )


@router.post("/maintenance/reset")
def reset_memory(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    request: MaintenanceResetRequest,
):
    """整体重置：清空本人记忆四表并 drop 本人记忆向量 collection（confirmation 须为「重置」）。"""
    return Response.success(app_service.reset_all_memory(principal.user_id, request)).to_dict()


@router.patch("/episode-links/{link_ref}")
def update_episode_link(
    app_service: Injected[MemoryAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    link_ref: str,
    request: EpisodeLinkUpdateRequest,
):
    """参与改挂：换实体/改角色（role 空串=清除；撞唯一组合 → 3007）。"""
    return Response.success(app_service.update_episode_link(principal.user_id, link_ref, request)).to_dict()
