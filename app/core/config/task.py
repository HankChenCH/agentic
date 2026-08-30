from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .redis import RedisConfig


class TaskConnectionRef(BaseModel):
    """任务队列连接引用：「驱动 + key 引用」。

    ``driver`` 声明 Celery 的传输驱动，名字对应一个连接配置分节
    （如 ``redis`` → ``redis.yaml``）；``provider`` 引用该分节
    ``providers`` 里的 entry key，连接事实统一归属分节本身，
    task.yaml 只持有引用（同 vector_db 顶层 ``embedding`` 指向 llm 分节的形式）。
    """

    driver: Literal["redis"] = Field(default="redis", description="传输驱动标识（对应连接配置分节名）")
    provider: str = Field(default="redis", description="被引用分节 providers 里的 entry key")


class TaskConfig(BaseModel):
    """任务队列（Celery）配置：broker/backend 各持一个连接引用，worker 与任务发送方共用。

    连接 URL 在消费期经 :func:`resolve_url` 从被引用分节解析，未来换/混用
    其它传输（如 rabbitmq）只扩 driver 取值并接入对应分节，schema 不变。
    """

    broker: TaskConnectionRef = Field(default_factory=TaskConnectionRef, description="消息 broker 连接引用")
    backend: TaskConnectionRef = Field(default_factory=TaskConnectionRef, description="任务结果 backend 连接引用")
    time_limit: int = Field(
        default=600,
        description="任务硬超时（秒），超时 worker 强杀任务进程（对齐 ``task_time_limit``）",
    )
    soft_time_limit: int = Field(
        default=540,
        description="任务软超时（秒），超时向任务内抛 SoftTimeLimitExceeded，任务可自行收尾；须小于 time_limit",
    )

    @model_validator(mode="after")
    def _soft_before_hard(self):
        if self.soft_time_limit >= self.time_limit:
            raise ValueError(f"soft_time_limit ({self.soft_time_limit}) must be < time_limit ({self.time_limit})")
        return self


def resolve_url(ref: TaskConnectionRef, *, redis: RedisConfig) -> str:
    """把连接引用解析为连接 URL，引用失效时立即抛 ValueError（启动期 fail-fast）。

    当前仅支持 ``driver: redis``（查 ``redis.yaml`` providers）；未来新增
    驱动在此扩展取值分支并接入对应分节。
    """
    if ref.driver != "redis":
        raise ValueError(f"unsupported task driver: {ref.driver}, supported: ['redis']")
    entry = redis.providers.get(ref.provider)
    if entry is None:
        raise ValueError(
            f"unknown redis provider '{ref.provider}' referenced by task config, configured: {list(redis.providers)}"
        )
    return entry.url
