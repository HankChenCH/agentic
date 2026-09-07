"""消息表示转换矩阵——消息转换边（codec）的总览与跨边共享规则。

agentic 域里同一条消息流有三种表示：
- **ag**（运行时面）：langgraph 流块——messages 投影的 ``ChatModelStream``
  与 tools 投影（ToolsTransformer 归一化契约），只在 run 流内存在；
- **store**（存储面）：``AgenticConversationMessage`` 行，content 为部件数组
  （部件词汇表见 ``ContentPartType``）——事实标准中间表示，落库即对账锚点；
- **ui**（协议/展示面）：ag-ui 事件流（实时）与前端 ThreadMessage（历史）。

转换矩阵（边名 = 源2目标；ag 侧出边在本包，store 出边在领域服务）：

| 边       | 实现                                            | 形态           | 受众差异（关键规则）                              |
| -------- | ----------------------------------------------- | -------------- | ------------------------------------------------- |
| ag2ui    | agui_translator.AgUiTranslator                  | 流式驱动       | 工具结果只发展示版 ``display``                    |
| ag2store | storage_translator.StorageTranslator            | 流式驱动(折叠) | content 恒真实结果，display_content 同行落库      |
| store2ui | ConversationService.list_history_messages       | 纯函数(批投影) | 出口把 display_content 替换为 content，键不外泄   |
| store2ag | ConversationService.replay_history              | 纯函数(批组装) | 真实 content；THOUGHT/CUSTOM 不回放；孤儿 tool_call 丢弃 |
| ui2store | RunMessage.storage_content                      | 纯函数         | 用户入参归一为存储形态（落库与回放同源）          |

统一表达：**每条边 = 模块级转换函数**（对同一套部件词汇表做 mapping）；流式
边（ag2\*）额外多一个薄驱动器（每 run 一实例），状态显式为对象——
AgUiTranslator 只持配置与 SSE 编码器，StorageTranslator 持 ``StorageState``。
批量边不设驱动器：输入是完整批次，单趟投影即可。形态差异来自数据到达方式
（帧流 vs 完整批次），不强行拉齐；拉齐的是转换函数与词汇表本身。

跨端共享的是协议词汇表（本模块 + ``ContentPartType`` + A2UI 事件名），不是
代码：前端历史翻译器 ``frontend/src/services/translators/thread-message-
translator.ts`` 是 store2ui 的前端半程，其拆条/合并规则与 StorageTranslator
对称。tool_call/tool_result 配对因此有三处实现（ag2store 流内配对、store2ag
批配对、前端按 id 归并）——同一领域规则在三种数据形态下的必然重复，改动时
三处同步。
"""

from typing import Any, Optional


def a2ui_messages(payload: dict[str, Any]) -> Optional[list]:
    """工具结果携带的 A2UI 消息数组（ag2ui/ag2store 共享的识别规则）。

    ToolsTransformer 契约里 UI 载荷在 ``ui`` 键、消息数组在 ``ui.a2ui``
    （工具 artifact 透传）。未携带或形态不符返回 None；两侧消费见
    AgUiTranslator 的 CUSTOM 事件与 StorageTranslator 的 CUSTOM 行。
    """
    ui_payload = payload.get("ui")
    if isinstance(ui_payload, dict) and ui_payload.get("a2ui"):
        return ui_payload["a2ui"]
    return None
