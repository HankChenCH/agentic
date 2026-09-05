"""Celery worker 侧 Prometheus 指标：任务计数 / 耗时直方图 / 在途 Gauge。

prefork 池下任务在子进程执行，指标经 prometheus-client **multiprocess
模式**落盘（``PROMETHEUS_MULTIPROC_DIR`` 必须在 fork 前设置），父进程起
一个独立 HTTP 端口（metrics.yaml ``worker_metrics_port``，默认 9091），
每次抓取用 ``MultiProcessCollector`` 重新扫描目录聚合各子进程指标。

两个关键时机约束：
- **prometheus_client 必须在 env 设置之后再首次导入**——其 ``values.ValueClass``
  在模块导入期一次性决定单进程/multiproc 实现（ ``ValueClass =
  get_value_class()``），故 prometheus_client 的导入整体延迟到
  :func:`setup_metrics` 内部（env 设置之后）；
- **本模块导入必须无副作用**——``app.tasks`` 的任务模块会
  ``from app.cmd.task_executor.main import celery_app``，而该链条在
  HTTP 应用等导入方进程中同样会被触发。全部装配（env、埋点信号、
  指标端口）延迟到 ``worker_init`` 信号：仅真正运行 worker 时触发，
  且在 prefork 之前执行，子进程继承 env 与已导入的模块。
"""

import os
import tempfile
import threading
from time import monotonic

from celery import signals

# 指标对象在装配期（env 设置 + prometheus_client 导入之后）才创建，
# 经本模块级命名空间给信号处理器访问
_METRICS: dict = {}


def _prerun(task_id, task, **_kwargs):
    task.__agentic_metrics_started = monotonic()
    _METRICS["running"].inc()


def _postrun(task_id, task, state=None, runtime=None, **_kwargs):
    name = task.name
    _METRICS["total"].labels(task=name, state=str(state)).inc()
    elapsed = runtime
    if elapsed is None:
        started = getattr(task, "__agentic_metrics_started", None)
        elapsed = monotonic() - started if started is not None else None
    if elapsed is not None:
        _METRICS["duration"].labels(task=name).observe(elapsed)
    _METRICS["running"].dec()


def _child_shutdown(**kwargs):
    # prefork 子进程退出后清除其 multiproc 落盘文件（gauge 残留会虚报在途数）
    from prometheus_client.multiprocess import mark_process_dead

    pid = kwargs.get("pid")
    if pid:
        mark_process_dead(pid)


def setup_metrics(enabled: bool, port: int) -> None:
    """worker 装配入口（``worker_init`` 信号触发，prefork 前执行）。

    enabled=False 时完全不埋点、不起端口；重复调用幂等（信号连接与
    指标对象只建一次）。
    """
    if not enabled or _METRICS:
        return

    # 须在 prometheus_client 首次导入之前就位（决定 multiproc 实现，见模块 docstring）
    os.environ.setdefault(
        "PROMETHEUS_MULTIPROC_DIR", tempfile.mkdtemp(prefix="agentic-metrics-")
    )

    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
    from prometheus_client.exposition import make_wsgi_app
    from prometheus_client.multiprocess import MultiProcessCollector

    _METRICS["total"] = Counter(
        "agentic_celery_tasks_total",
        "Celery 任务执行总数（按任务名与结束状态）。",
        ["task", "state"],
    )
    _METRICS["duration"] = Histogram(
        "agentic_celery_task_duration_seconds",
        "Celery 任务执行耗时（秒，任务成功时由 Celery 提供 runtime，其余自计）。",
        ["task"],
    )
    _METRICS["running"] = Gauge(
        "agentic_celery_tasks_currently_running",
        "当前正在执行的 Celery 任务数。",
    )

    signals.task_prerun.connect(_prerun, weak=False)
    signals.task_postrun.connect(_postrun, weak=False)
    signals.worker_process_shutdown.connect(_child_shutdown, weak=False)

    registry = CollectorRegistry()
    MultiProcessCollector(registry)
    wsgi_app = make_wsgi_app(registry)

    def _serve() -> None:
        from wsgiref.simple_server import WSGIRequestHandler, make_server

        class _Handler(WSGIRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - wsgiref 签名
                pass

        make_server("0.0.0.0", port, wsgi_app, handler_class=_Handler).serve_forever()

    threading.Thread(target=_serve, name="prometheus-metrics", daemon=True).start()
