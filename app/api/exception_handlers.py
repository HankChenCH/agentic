"""FastAPI 全局异常处理器（AOP）：统一 Response 信封 + 环境感知脱敏。

职责边界：本模块只做 HTTP wiring（注册 handler、组装响应）；异常分级定义在
:mod:`app.core.exceptions`，环境判定来自 :func:`app.core.config.get_environment`。

环境策略：

- 业务异常（``BusinessError``）：不区分环境，``message`` 如实返回；
- 框架/基础/未知异常：dev/test 返回异常信息 + ``detail`` + ``trace``（堆栈），
  prod 只返回「服务内部错误」；
- 日志与响应脱敏无关：所有非业务异常都以完整堆栈落日志（``exc_info=exc``），
  业务异常按 warning 记录（预期内错误，不打堆栈噪音）。

SSE 端点（POST /agentic/chat）不受 HTTP 异常处理器管辖：响应头一旦发出，
流内错误只能以 ag-ui RunErrorEvent 形式返回——脱敏策略与本文一致，实现见
:meth:`app.services.agentic_service.AgenticService._run_error_message`
（业务异常如实、其余 prod 统一「服务内部错误」）；两处需同步维护。
"""

import json
import logging
from traceback import format_exception

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_environment
from app.core.exceptions import BusinessError, FrameworkError
from app.models.schema.response.biz_response import Response

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "服务内部错误"


def _is_production() -> bool:
    return get_environment() == "prod"


def _internal_error_response(exc: Exception) -> JSONResponse:
    """框架/基础/未知异常的响应：生产环境对外只说「服务内部错误」。"""
    if _is_production():
        return JSONResponse(
            status_code=500,
            content=Response.fail(error_code=500, error_message=INTERNAL_ERROR_MESSAGE).to_dict(),
        )
    trace = "".join(format_exception(type(exc), exc, exc.__traceback__))
    return JSONResponse(
        status_code=500,
        content=Response.fail(
            error_code=500,
            error_message=str(exc) or type(exc).__name__,
            detail=repr(exc),
            trace=trace,
        ).to_dict(),
    )


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    logger.warning("business error %s on %s %s: [%s] %s", exc.code, request.method, request.url.path, type(exc).__name__, exc.message)
    return JSONResponse(
        status_code=exc.http_status,
        content=Response.fail(error_code=exc.code, error_message=exc.message).to_dict(),
    )


async def framework_error_handler(request: Request, exc: FrameworkError) -> JSONResponse:
    logger.error("framework error on %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
    return _internal_error_response(exc)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # 基础/未知异常兜底：starlette 发送响应后仍会重抛，uvicorn 会再记一次堆栈，属预期噪音
    logger.error("unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
    return _internal_error_response(exc)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 422 属客户端错误：校验明细不含服务端内部信息，任何环境都返回
    detail = json.dumps(jsonable_encoder(exc.errors()), ensure_ascii=False, default=str)
    logger.warning("request validation failed on %s %s: %s", request.method, request.url.path, detail)
    return JSONResponse(
        status_code=422,
        content=Response.fail(error_code=422, error_message="请求参数校验失败", detail=detail).to_dict(),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 兼容遗留 HTTPException 用法：保持原状态码，统一为 Response 信封格式
    logger.warning("http error %s on %s %s", exc.status_code, request.method, request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content=Response.fail(error_code=exc.status_code, error_message=str(exc.detail)).to_dict(),
        headers=getattr(exc, "headers", None),
    )


def register_exception_handlers(server: FastAPI) -> None:
    """在应用工厂（create_app）中注册全部全局异常处理器。"""
    server.add_exception_handler(BusinessError, business_error_handler)
    server.add_exception_handler(FrameworkError, framework_error_handler)
    server.add_exception_handler(RequestValidationError, validation_error_handler)
    server.add_exception_handler(StarletteHTTPException, http_exception_handler)
    server.add_exception_handler(Exception, unhandled_error_handler)
