"""记忆组件业务异常（错误码 3xxx）。

命名注意：不能叫 ``MemoryError``，会遮蔽 Python 内建的同名异常。
"""

from app.core.exceptions.business import BusinessError


class MemoryComponentError(BusinessError):
    """记忆组件通用业务错误。"""

    default_code = 3000


class MemoryInvalidTimeParamError(MemoryComponentError):
    """图快照等接口的时间参数不可解析（ISO 格式约束）。"""

    default_code = 3001
    default_http_status = 400


class MemoryObjectNotFoundError(MemoryComponentError):
    """编辑目标对象（实体/陈述等）不存在。"""

    default_code = 3002
    default_http_status = 404


class MemoryProtectedObjectError(MemoryComponentError):
    """受保护对象不可执行该操作（如用户节点不可删除/不可作被并方）。"""

    default_code = 3003
    default_http_status = 409


class MemoryNameConflictError(MemoryComponentError):
    """名称与现存对象冲突（实体重名/别名撞车——拆成已存在的名字等于变相合并）。"""

    default_code = 3005
    default_http_status = 409


class MemoryNoChangeError(MemoryComponentError):
    """编辑未产生任何变化（纠正值与现值等价、未选择任何分离内容）。"""

    default_code = 3006
    default_http_status = 400


class MemoryConstraintConflictError(MemoryComponentError):
    """唯一约束冲突（如事件参与的 episode/entity/role 组合已存在）。"""

    default_code = 3007
    default_http_status = 409


class MemoryInvalidParamError(MemoryComponentError):
    """请求参数非法（客体引用二选一破坏、对非在效陈述操作等）。"""

    default_code = 3008
    default_http_status = 400
