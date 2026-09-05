"""天气查询门面：demo 组件的能力实现，当前为演示用 mock 数据源。

返回结构化天气数据（dict），不触达任何外部服务；数据源的「mock」性质收敛
在本门面内部——未来接入真实天气数据源时仅替换此处实现，工具声明（manifest）
与装配路径不动。UI 呈现（A2UI 天气卡片）不在本门面职责内，由
``weather_surface`` 消费本门面的数据形态组装。
"""

from dataclasses import dataclass

from wireup import injectable


@injectable
@dataclass
class DemoWeatherService:
    """天气查询门面服务（mock 数据源，服务演示场景）。"""

    def get_weather(self, city: str, date: str) -> dict:
        """返回结构化天气数据；字段即天气卡片的展示契约（LLM 与前端共用）。"""
        return {
            "city": city,
            "date": date,
            "condition": "晴朗",
            "temperature": 33,
            "humidity": 60,
        }
