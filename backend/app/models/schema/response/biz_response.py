from typing import Any
from dataclasses import dataclass

@dataclass
class Response:
    error_code: int = 0
    error_message: str = ""
    response: Any = None
    # 仅错误响应使用：dev/test 环境由全局异常处理器填充（异常原文与堆栈），生产环境保持 None
    detail: str | None = None
    trace: str | None = None

    def to_dict(self):
        payload = dict(self.__dict__)
        # 成功路径不含调试字段，保持响应体与历史版本一致
        if self.detail is None:
            payload.pop("detail")
        if self.trace is None:
            payload.pop("trace")
        return payload

    @classmethod
    def success(cls, response: Any = None):
        return Response(response=response)

    @classmethod
    def fail(cls, error_code: int, error_message: str, response: Any = None, detail: str | None = None, trace: str | None = None):
        return Response(error_code=error_code, error_message=error_message, response=response, detail=detail, trace=trace)
