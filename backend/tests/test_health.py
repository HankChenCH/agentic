"""/health 就绪探针：依赖探测收敛为 up/down 与 503 口径（不走完整应用装配）。

probe() 接受真实依赖对象，这里用替身驱动三种组合：全绿、redis 掉、
db 掉；探测函数本身抛异常即视为 down（真实连接错误路径由 _check_* 覆盖）。
"""

import contextlib

from sqlalchemy.exc import SQLAlchemyError

from app.api.health import probe


class _StubEngine:
    @contextlib.contextmanager
    def connect(self):
        yield self

    def execute(self, _stmt):
        return None


class _StubRedis:
    def __init__(self, *, up: bool = True):
        self.up = up

    def ping(self):
        if not self.up:
            raise ConnectionError("redis down")


def test_all_dependencies_up():
    status, components = probe(_StubEngine(), _StubRedis())
    assert status == "ok"
    assert components == {"db": "up", "redis": "up"}


def test_redis_down_marks_unavailable():
    status, components = probe(_StubEngine(), _StubRedis(up=False))
    assert status == "unavailable"
    assert components == {"db": "up", "redis": "down"}


def test_db_down_marks_unavailable(monkeypatch):
    from app.api import health as health_module

    def _boom(_engine):
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(health_module, "_check_db", _boom)
    status, components = probe(_StubEngine(), _StubRedis())
    assert status == "unavailable"
    assert components == {"db": "down", "redis": "up"}
