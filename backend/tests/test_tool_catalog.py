"""工具能力目录服务：静态注册表 → 前端展示元数据契约。"""

from app.application.tool_catalog import ToolCatalogService


def test_describe_returns_full_catalog_with_titles():
    catalog = ToolCatalogService().describe()["components"]
    assert {c["component"] for c in catalog} == {"demo", "memory", "knowledge", "human_agent", "a2ui"}
    # 工具级 title 是前端 UI 标识化的关键产出：已声明的工具须原样透传
    # （title 为可选元数据，未声明的工具输出 null 由前端降级链兜底）
    by_name = {t["name"]: t for c in catalog for t in c["tools"]}
    assert by_name["knowledge_search"]["title"] == "知识库检索"
    assert by_name["get_weather"]["title"] == "查询天气"
    assert by_name["timeline"]["title"] == "回忆时间线"
    assert by_name["human_agent_list"]["title"] == "查看人工客服坐席"
    assert by_name["a2ui_compose"]["title"] == "生成 UI 卡片"
