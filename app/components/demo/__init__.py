"""演示工具组件（builtin:demo 的演示能力）。

组件解剖学（范式见 app.components.base 与包 __init__ docstring）：
manifest.py 声明能力并装配工具，ability/ 持能力门面服务。数据源当前
为 mock（收敛在 ability 门面内部，未来替换实现工具层不动）；无跨能力
共享机件、自有存储与管理面实现，故无 internal/、repositories/ 与
admin.py。
"""

from .ability.weather import DemoWeatherService
from .manifest import DemoComponent, WeatherArgs

__all__ = [
    "DemoWeatherService",
    "DemoComponent",
    "WeatherArgs",
]
