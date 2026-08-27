"""领域层：按聚合组织的领域服务，管理侧端点与后台任务的直接消费面。

依赖方向：只允许向下依赖 repositories / infrastructures；严禁 import
orchestration / components / agents / api。消费方规则见 AGENTS.md「服务层两层制」。
"""
