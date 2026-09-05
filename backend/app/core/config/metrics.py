"""运维监控指标配置：Prometheus 采集端点与 worker 指标暴露。

与 ``app/configs/metrics.yaml`` 一一对应；数值可经环境变量（``.env`` / 进程环境）
覆盖。HTTP 应用侧 ``/metrics`` 端点默认始终挂载（无独立开关，跟随 enabled）；
Celery worker 侧另起一个独立 HTTP 端口暴露任务指标（prometheus-client
multiprocess 模式聚合 prefork 子进程）。
"""

from pydantic import BaseModel, Field, field_validator


class MetricsConfig(BaseModel):
    """Prometheus 指标配置（``metrics.yaml`` 分节）。"""

    enabled: bool = Field(
        default=True,
        description="是否启用指标采集（/metrics 端点、HTTP 中间件与 worker 指标暴露）。",
    )
    worker_metrics_port: int = Field(
        default=9091,
        description="Celery worker 指标 HTTP 服务端口（multiprocess 模式聚合子进程指标）。",
    )

    @field_validator("worker_metrics_port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError(f"worker_metrics_port 必须在 1-65535 之间: {value}")
        return value
