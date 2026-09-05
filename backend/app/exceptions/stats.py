"""用量统计域业务异常（错误码 6xxx）。"""

from app.core.exceptions.business import BusinessError


class StatsError(BusinessError):
    """用量统计域通用业务错误。"""

    default_code = 6000


class InvalidUsageRangeError(StatsError):
    """用量查询的时间范围非法（start 晚于等于 end 等）。"""

    default_code = 6001
