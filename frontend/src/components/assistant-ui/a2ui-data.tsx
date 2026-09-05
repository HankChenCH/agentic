import { memo, useCallback, useState } from "react";
import {
  useAssistantDataUI,
  useAui,
  type DataMessagePartComponent,
} from "@assistant-ui/react";
import {
  MessageProcessor,
  type ActionListener,
} from "@a2ui/web_core/v0_9";
import { A2uiSurface, basicCatalog } from "@a2ui/react/v0_9";
import { injectStyles } from "@a2ui/react/styles";
import { ChevronDownIcon } from "lucide-react";

import {
  A2UI_DATA_PART_NAME,
  parseA2uiPayload,
  type A2uiMessage,
} from "@/services/a2ui";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import "./a2ui.css";

/**
 * A2UI data part 渲染器：ag-ui CUSTOM 事件（name="a2ui"）经 react-ag-ui
 * 聚合成 data part 后，在此把 A2UI v0.9 消息数组喂给官方渲染器
 * （MessageProcessor + A2uiSurface + basicCatalog）。
 *
 * 契约与降级：载荷解析/处理失败一律降级为折叠 JSON（不渲染半棵组件树，
 * 也不让坏载荷炸掉消息流）；工具调用过程本身仍走 get_weather 的
 * ToolFallback 折叠条，两者互不替代。
 *
 * 交互回传：Button.action（chat.send 事件）经 MessageProcessor 的
 * actionHandler 接住，把 context.text 作为用户新消息追加进线程——见
 * A2uiDataRender 内 CHAT_SEND_ACTION 注释。
 *
 * 样式接线：`injectStyles()` 注入 .a2ui-surface 结构样式（幂等）；主题
 * 调色板 CSS 变量由 basicCatalog 导入时自动注入 adoptedStyleSheets；
 * `a2ui.css`（a2ui-host 作用域）把调色板/排版变量接管到应用主题令牌并
 * 补齐包内未加载的 Text 排版规则——卡片观感与聊天页同风格，暗色自动跟随。
 */

// 模块级注入一次（内部按 styleId 幂等，重复调用无副作用）
if (typeof document !== "undefined") {
  injectStyles();
}

/**
 * 按钮点击回传的事件名（与后端 app/packages/a2ui/messages.py 的
 * CHAT_SEND_ACTION 对齐）：收到该事件时把 context.text 作为用户新消息
 * 追加进会话——按钮即「替用户说一句话」，不引入独立的动作执行通道。
 */
const CHAT_SEND_ACTION = "chat.send";

/** 单个 data part 的 surface 容器：消息一次性同步处理完，surface 清单由此固定 */
const A2uiPayload = memo(function A2uiPayload({
  messages,
  onAction,
}: {
  messages: A2uiMessage[];
  onAction?: ActionListener;
}) {
  const [state] = useState(() => {
    const processor = new MessageProcessor([basicCatalog], onAction);
    try {
      processor.processMessages(messages);
    } catch {
      // 坏载荷（结构合法但语义非法，如重复 surfaceId）：走折叠降级
      return { surfaces: [], failed: true };
    }
    return {
      surfaces: Array.from(processor.model.surfacesMap.values()),
      failed: false,
    };
  });

  if (state.failed) return <A2uiFallback payload={messages} />;
  return (
    // a2ui-host：宿主主题作用域（a2ui.css）——调色板/排版变量接管到应用
    // 主题，宽度贴合卡片内容（卡片自带内边距/圆角/描边）。
    <div className="a2ui-surface a2ui-host my-1 w-[19rem] max-w-full">
      {state.surfaces.map((surface) => (
        <A2uiSurface key={surface.id} surface={surface} />
      ))}
    </div>
  );
});

/** 载荷非法 / 处理失败时的折叠降级：保持可溯源，不静默吞掉 */
const A2uiFallback = ({ payload }: { payload: unknown }) => (
  <Collapsible className="my-1 w-fit max-w-full">
    <CollapsibleTrigger className="group/trigger text-muted-foreground hover:text-foreground flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
      <ChevronDownIcon className="size-3.5 shrink-0 transition-transform group-data-open/trigger:rotate-180" />
      A2UI 卡片渲染失败（查看原始载荷）
    </CollapsibleTrigger>
    <CollapsibleContent>
      <pre className="bg-muted text-muted-foreground mt-1 max-h-48 max-w-md overflow-auto rounded-md p-2 text-xs">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </CollapsibleContent>
  </Collapsible>
);

const A2uiDataRender: DataMessagePartComponent = (props) => {
  // 点击回传接线：Button.action 事件 → chat.send → 以用户新消息追加进线程
  //（触发一轮正常 run）。thread.append 异步生效（发送即返回，消息稍后出现）。
  // aui 客户端稳定，useCallback 保证 handler 身份不变，memo 化的 A2uiPayload
  // 不因重渲染重建 MessageProcessor。
  const aui = useAui();
  const onAction = useCallback<ActionListener>(
    (action) => {
      if (action.name !== CHAT_SEND_ACTION) return;
      const text = action.context?.text;
      if (typeof text !== "string" || !text) return;
      if (aui.thread.getState().isRunning) return; // 运行中忽略连点
      aui.thread.append(text);
    },
    [aui],
  );
  const messages = parseA2uiPayload(props.data);
  if (!messages) return <A2uiFallback payload={props.data} />;
  return <A2uiPayload messages={messages} onAction={onAction} />;
};

/** 挂载即注册 name="a2ui" 的 data part 渲染器（自身不渲染内容） */
export const A2uiDataUI = () => {
  useAssistantDataUI({
    name: A2UI_DATA_PART_NAME,
    render: A2uiDataRender,
  });
  return null;
};
