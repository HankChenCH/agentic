"""天气查询门面：demo 组件的能力实现，当前为演示用 mock 数据源。

返回固定文案，不触达任何外部服务；数据源的「mock」性质收敛在本门面
内部——未来接入真实天气数据源时仅替换此处实现，工具声明（manifest）
与装配路径不动。
"""

from dataclasses import dataclass

from wireup import injectable


@injectable
@dataclass
class DemoWeatherService:
    """天气查询门面服务（mock 数据源，服务演示场景）。"""

    def get_weather(self, city: str, date: str) -> str:
        return f"{city} {date} 天气晴朗，气温33度，湿度60%"
