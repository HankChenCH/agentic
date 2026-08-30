"""memory 组件的能力模块：按能力命名，各持一个 @injectable 门面服务。

- recall：召回能力——build_fast_context 会话前快注 + timeline/expand/
  state_at 深度三件套（零 LLM，纯 SQL/向量检索 + 渲染）；
- consolidation：巩固能力——轮次收尾后的 remember 巩固管线（抽取 →
  消歧 → 裁决 → 落库 + 向量同步）。
"""
