"""API 限流：fastapi-limiter（pyrate-limiter 引擎）依赖 + Redis 共享计数。

替代原边缘中间件的进程内限流（``api/middleware.py`` 的 RateLimitMiddleware
已随本模块移除）：

- 以 FastAPI 依赖（``Depends(rate_limiter)``）按端点挂载（run / run/cancel /
  文档上传），不再走边缘中间件；命中上限由回调抛 ``HTTPException(429)``，
  经全局异常处理器统一出信封 + ``Retry-After``；
- 计数存 Redis（redis.yaml 默认 entry，asyncio 客户端；客户端惰性连接，
  首个请求才发生），多实例部署共享同一窗口；
- 身份取 JWT 用户（``require_user`` 把 principal 落在 ``request.state``，
  router 级依赖先于端点级执行，限流运行时必然已就位），未认证回退客户端 IP；
- 额度为代码常量（原 http.yaml 的 ``rate_limit`` 配置节随实现一并移除）。

pyrate-limiter 的桶在整桶窗口内计数、与 item name 无关（Redis 侧即一个
ZSET 的 ZCOUNT），按 key 限额必须一 key 一桶——:class:`PerKeyBucketFactory`
惰性建桶（``agentic:ratelimit:<scope>:<identity>``）并登记后台泄漏清理
过期成员；一个 key 首次访问多创建的桶共享同一 ZSET，属良性冗余。

与旧中间件的语义差异：计数身份由客户端 IP 改为登录用户；run 与 run/cancel
由共享前缀窗口改为按端点独立窗口（fastapi-limiter 以路由索引参与 key）；
``Retry-After`` 为窗口长度的上界而非精确等待秒数。
"""

from inspect import isawaitable
from time import time_ns
from typing import Awaitable, Callable

import redis.asyncio as async_redis
from fastapi import HTTPException
from fastapi_limiter.depends import RateLimiter
from pyrate_limiter import (
    AbstractBucket,
    BucketFactory,
    Duration,
    Limiter,
    Rate,
    RateItem,
    RedisBucket,
)
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import RedisConfig, StandaloneRedisProviderEntry, load_section

# Redis 键空间前缀（与取消信号的 agentic:signal: 同理，与其他用途隔离）
_BUCKET_KEY_PREFIX = "agentic:ratelimit:"
_REJECT_MESSAGE = "请求过于频繁，请稍后重试"

# 限流额度（代码常量；多实例共享窗口。沿用原 http.yaml 缺省值）
_RUN_RATE = Rate(30, Duration.MINUTE)
_UPLOAD_RATE = Rate(10, Duration.MINUTE)


def _wall_now_ms() -> int:
    # 与 RedisBucket.now() 同钟（wall ms）：item 时间戳必须与桶的计时钟一致
    return time_ns() // 1_000_000


class PerKeyBucketFactory(BucketFactory):
    """一 key 一桶的 :class:`BucketFactory`。

    pyrate-limiter 的默认单桶工厂把所有 key 计进同一窗口（桶内计数不区分
    item.name），按 key 限额必须由工厂按 key 分桶：``make_bucket(name)``
    决定后端（生产为 Redis ZSET，测试为内存桶），首个 key 访问时惰性创建
    并缓存，``schedule_leak`` 登记后台泄漏。``get`` 返回协程是官方支持的
    异步建桶路径（``try_acquire_async`` 会 await）。
    """

    def __init__(
        self,
        *,
        make_bucket: Callable[[str], "AbstractBucket | Awaitable[AbstractBucket]"],
        now_ms: Callable[[], int],
    ) -> None:
        self._make_bucket = make_bucket
        self._now_ms = now_ms
        self._buckets: dict[str, AbstractBucket] = {}

    def wrap_item(self, name: str, weight: int = 1) -> RateItem:
        return RateItem(name, self._now_ms(), weight=weight)

    def get(self, item: RateItem) -> "AbstractBucket | Awaitable[AbstractBucket]":
        bucket = self._buckets.get(item.name)
        if bucket is None:
            return self._create(item.name)
        return bucket

    async def _create(self, name: str) -> AbstractBucket:
        bucket = self._make_bucket(name)
        if isawaitable(bucket):
            bucket = await bucket
        self._buckets[name] = bucket
        self.schedule_leak(bucket)
        return bucket


def _build_async_client(config: RedisConfig) -> async_redis.Redis:
    """按 redis.yaml 默认 entry 构建 asyncio 客户端（惰性，不发起连接）。

    api 层禁入 infrastructures（分层 AST 禁边），无法复用 RedisClientFactory
    （其契约类型为同步 redis.Redis）——连接事实仍以 redis.yaml 为唯一来源，
    此处仅按同一 entry 组装 asyncio 形态；仅支持 standalone entry。
    """
    entry = config.providers[config.default]
    if not isinstance(entry, StandaloneRedisProviderEntry):
        raise ValueError(
            f"rate limiting supports standalone redis only, got: {entry.type} ({config.default})"
        )
    return async_redis.Redis.from_url(
        entry.url,
        socket_timeout=entry.socket_timeout,
        socket_connect_timeout=entry.socket_connect_timeout,
    )


def _redis_bucket_maker(
    client: async_redis.Redis, rates: list[Rate]
) -> Callable[[str], "Awaitable[RedisBucket]"]:
    """Redis 桶工厂：一 key 一 ZSET；asyncio 客户端下 init 返回建桶协程。"""

    def make(name: str):
        return RedisBucket.init(rates=rates, redis=client, bucket_key=_BUCKET_KEY_PREFIX + name)

    return make


async def _identity(request: Request) -> str:
    principal = getattr(request.state, "user_principal", None)
    if principal is not None:
        return f"user:{principal.user_id}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


def _scoped_identifier(scope: str) -> Callable[[Request], Awaitable[str]]:
    """key = <scope>:<identity>：作用域隔离，避免不同端点的路由索引尾缀碰撞。"""

    async def identify(request: Request) -> str:
        return f"{scope}:{await _identity(request)}"

    return identify


def _make_reject(window_seconds: int) -> Callable[[Request, Response], Awaitable[None]]:
    async def reject(request: Request, response: Response) -> None:
        raise HTTPException(
            status_code=429,
            detail=_REJECT_MESSAGE,
            # pyrate 非阻塞路径不回报精确等待，Retry-After 取窗口长度作上界
            headers={"Retry-After": str(window_seconds)},
        )

    return reject


def build_rate_limiter(
    *,
    rate: Rate,
    make_bucket: Callable[[str], "AbstractBucket | Awaitable[AbstractBucket]"],
    now_ms: Callable[[], int],
    scope: str,
) -> RateLimiter:
    """装配一个作用域的 :class:`RateLimiter` 依赖（生产与测试共用同一装配逻辑）。"""
    engine = Limiter(PerKeyBucketFactory(make_bucket=make_bucket, now_ms=now_ms))
    return RateLimiter(
        engine,
        identifier=_scoped_identifier(scope),
        callback=_make_reject(window_seconds=rate.interval // 1000),
    )


# redis.yaml 在导入期读取（与边缘策略配置装配期读取同口径，.env 首次加载在
# 此触发；配置错误进程起不来）。客户端惰性连接：建实例/建桶均不发起网络。
_async_redis_client = _build_async_client(load_section("redis.yaml", RedisConfig))

run_rate_limiter = build_rate_limiter(
    rate=_RUN_RATE,
    make_bucket=_redis_bucket_maker(_async_redis_client, [_RUN_RATE]),
    now_ms=_wall_now_ms,
    scope="run",
)
upload_rate_limiter = build_rate_limiter(
    rate=_UPLOAD_RATE,
    make_bucket=_redis_bucket_maker(_async_redis_client, [_UPLOAD_RATE]),
    now_ms=_wall_now_ms,
    scope="upload",
)
