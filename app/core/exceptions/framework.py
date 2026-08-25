"""框架异常：应用启动与运行期的框架级错误。

与业务异常的区别：框架异常意味着程序或部署层面的问题（配置错误、基建不可用），
终端用户无需也不应看到细节——生产环境对外统一为「服务内部错误」，
开发/测试环境如实返回异常信息与堆栈（见 :mod:`app.api.exception_handlers`）。
"""

from app.core.exceptions.base import AgenticError


class FrameworkError(AgenticError):
    """框架异常基类（启动/运行时）。"""

    default_code = 500


class ConfigError(FrameworkError):
    """配置加载失败：文件缺失/解析失败/校验失败/插值变量缺失。

    原 defining 于 ``core/config/loader.py``，迁入统一异常体系后由 loader
    导入使用，``core.config`` 对外的 ``ConfigError`` 导出保持不变。
    """


class InfrastructureError(FrameworkError):
    """基建错误：LLM 供应商构建、数据库引擎等基础设施不可用。"""
