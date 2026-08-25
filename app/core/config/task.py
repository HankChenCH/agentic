from pydantic import BaseModel, Field


class TaskConfig(BaseModel):
    """任务队列（Celery）配置：worker 与任务发送方共用。"""

    broker: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="消息 broker 地址，本地开发默认指向 docker compose 的 redis",
    )
    backend: str = Field(
        default="redis://127.0.0.1:6379/1",
        description="任务结果 backend 地址",
    )
