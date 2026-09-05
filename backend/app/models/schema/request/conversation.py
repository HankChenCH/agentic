"""会话管理侧请求模型（camelCase，与 run.py 同口径）。"""

from uuid import UUID

from pydantic import BaseModel, Field


class ActivateTurnRequest(BaseModel):
    turnId: UUID = Field(..., description="要激活为当前分支的轮次id（须为已完成轮次且位于末梢扇形内）")
