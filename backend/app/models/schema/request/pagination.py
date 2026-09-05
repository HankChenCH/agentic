from pydantic import BaseModel, Field

class PaginationRequest(BaseModel):
    page: int = Field(default=1, ge=1, description="页数，从 1 开始")
    pageSize: int = Field(default=20, ge=1, le=100, description="每页数量")