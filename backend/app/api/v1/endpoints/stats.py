"""用量统计端点（个人用量视角）：汇总 / 按天序列 / 流水分页。

身份经 require_user 注入（router 级挂载，见 cmd/http/main.py），查询一律
以 principal.user_id 圈定——个人用量，无跨用户视图。时间参数为 ISO 8601
（FastAPI 原生解析，格式非法 422），半开区间 [start, end)，领域服务校验
start < end（违规 6001/400）。
"""

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
from app.domain.usage import UsageService

from app.models.schema.request.pagination import PaginationRequest
from app.models.schema.response.biz_response import Response

router = APIRouter(prefix="/stats", tags=["Stats"])


@router.get("/usage/summary")
def usage_summary(
    usage: Injected[UsageService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    start: Optional[datetime] = Query(default=None, description="起始时间（含），ISO 8601"),
    end: Optional[datetime] = Query(default=None, description="结束时间（不含），ISO 8601"),
):
    """区间总量 + 按场景/按模型分布。"""
    return Response.success(
        usage.summary(user_id=principal.user_id, start=start, end=end)
    ).to_dict()


@router.get("/usage/daily")
def usage_daily(
    usage: Injected[UsageService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    start: Optional[datetime] = Query(default=None, description="起始时间（含），ISO 8601"),
    end: Optional[datetime] = Query(default=None, description="结束时间（不含），ISO 8601"),
    scene: Optional[str] = Query(default=None, description="场景过滤：chat/title/memory"),
):
    """按天时间序列（UTC 日分桶，日期升序）。"""
    return Response.success(
        usage.daily(user_id=principal.user_id, start=start, end=end, scene=scene)
    ).to_dict()


@router.get("/usage/records")
def usage_records(
    usage: Injected[UsageService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    pagination: Annotated[PaginationRequest, Depends()],
    start: Optional[datetime] = Query(default=None, description="起始时间（含），ISO 8601"),
    end: Optional[datetime] = Query(default=None, description="结束时间（不含），ISO 8601"),
    scene: Optional[str] = Query(default=None, description="场景过滤：chat/title/memory"),
):
    """用量流水分页（时间倒序，一行 = 一次 LLM 调用）。"""
    return Response.success(
        usage.records(
            user_id=principal.user_id, start=start, end=end, scene=scene,
            page=pagination.page, page_size=pagination.pageSize,
        )
    ).to_dict()
