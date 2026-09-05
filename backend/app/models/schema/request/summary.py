from pydantic import BaseModel, Field


class SummaryRequest(BaseModel):
    text: str = Field(..., description="待分析的原文", min_length=1, max_length=50000)
    runId: str | None = Field(default=None, description="轮次标识（前端关联用），缺省由服务端生成", max_length=36)
