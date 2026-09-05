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
    acks_late: bool = Field(
        default=True,
        description="任务执行完才 ack（``task_acks_late``）：worker 崩溃/断连时未 ack 消息由 broker 重投，"
        "前提是任务体幂等（knowledge 摄取由 claim 门闸保证）",
    )
    reject_on_worker_lost: bool = Field(
        default=False,
        description="worker 子进程被强杀（硬超时等）时是否重投消息（``task_reject_on_worker_lost``）。"
        "刻意默认 False：确定性超时的文档会被无限「强杀→重投」循环（毒丸），进程内死亡一律落 DB 状态，"
        "由看门狗（reap）按计数上限做有界恢复",
    )
    prefetch_multiplier: int = Field(
        default=1,
        ge=1,
        description="worker 预取倍数（``worker_prefetch_multiplier``）：acks_late 下取 1，避免单 worker 囤积任务拖长尾延迟",
    )
    visibility_timeout: int = Field(
        default=1200,
        description="Redis 未 ack 消息的重投窗口（秒）：worker 整进程死亡（kill -9/断电）后消息在此窗口后被 broker 重投；"
        "须大于 time_limit（不能把仍在跑的任务重投出去），broker 与 result backend 两处统一套用",
    )
    stale_processing_seconds: int = Field(
        default=660,
        description="processing 判死阈值（秒）：文档 processing 持续超过该时长视为执行体已死，看门狗接管；"
        "须大于 time_limit（存活任务最长可跑 time_limit，不能误伤慢任务）",
    )
    stale_pending_seconds: int = Field(
        default=600,
        description="pending 判丢失阈值（秒）：pending 持续超过该时长视为消息已丢（如 Redis 无持久化重启），看门狗直接补发",
    )
    reap_interval_seconds: int = Field(
        default=120,
        ge=0,
        description="看门狗（reap）对账周期（秒）：beat 周期调度卡死文档对账；0 = 关闭看门狗",
    )
    max_reap_attempts: int = Field(
        default=3,
        ge=1,
        description="单文档看门狗重投上限：达到上限置 failed + 原因（毒丸保护），走人工 retry；成功收尾归零",
    )

    @model_validator(mode="after")
    def _soft_before_hard(self):
        if self.soft_time_limit >= self.time_limit:
            raise ValueError(f"soft_time_limit ({self.soft_time_limit}) must be < time_limit ({self.time_limit})")
        return self

    @model_validator(mode="after")
    def _recovery_windows_after_limits(self):
        # 可靠性窗口都建立在「任务最长跑 time_limit」之上：重投/判死阈值太短会把存活任务误重投
        if self.visibility_timeout <= self.time_limit:
            raise ValueError(
                f"visibility_timeout ({self.visibility_timeout}) must be > time_limit ({self.time_limit})"
            )
        if self.stale_processing_seconds <= self.time_limit:
            raise ValueError(
                f"stale_processing_seconds ({self.stale_processing_seconds}) must be > time_limit ({self.time_limit})"
            )
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
