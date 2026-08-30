import { useEffect, useRef, type FC } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { useAui } from "@assistant-ui/react";

import { useConversationActions } from "@/hooks/use-conversation-list";

/** 路由参数初值哨兵：与 undefined（"/" 路径）区分，保证首次挂载也走一次同步 */
const NO_ROUTE_SEEN = Symbol("no-route-seen");

/**
 * 聊天路由 ↔ runtime 会话双向同步（自身不渲染）。
 *
 * URL 是会话身份的路由映射：`/` = 新会话，`/chat/:threadId` = 指定会话。
 * 数据源是 use-conversation-list 维护的 currentThreadId 镜像（agent.threadId
 * 的 React 化影子；external-store 下 threads.mainThreadId 不随会话切换变化，
 * 不能用作路由数据源）。
 *
 *  - URL → runtime：路由参数**真实变化**时切会话（深链打开 / 刷新恢复 /
 *    浏览器前进后退 / 手改地址）。切换走 aui.threads().switchToThread，
 *    最终调 adapter.onSwitchToThread，与侧栏点击同一条路径（历史加载复用）。
 *  - runtime → URL：镜像变化（侧栏切换 / 新建 / 删除当前会话 / 新会话首条
 *    消息落库）时同步地址栏。切换会话 push（可后退穿越），回到新会话
 *    replace（避免后退落进已删除的会话）。
 *
 * 两个方向的竞争以「路由参数是否变化」为界：镜像自身变化（如新建会话置
 * null）不会被判成深链而反向切会话；镜像 === undefined（启动初态）不产生
 * 任何导航。须挂在路由元素内（依赖 router context）且在 runtime provider 内。
 */
export const ThreadRouteSync: FC = () => {
  const { threadId: routeThreadId } = useParams<{ threadId?: string }>();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const aui = useAui();
  const { currentThreadId } = useConversationActions();
  const lastRouteRef = useRef<string | undefined | typeof NO_ROUTE_SEEN>(
    NO_ROUTE_SEEN,
  );
  // 路由参数刚变化标记：参数变化的那轮 commit 里镜像还是旧值（setCurrentThreadId
  // 要到下一轮 render 才可见），runtime→URL effect 若此时读到旧镜像会把深链误判
  // 成"新建会话"而把 URL 打回 /。Effect A 先行打标，Effect B 看到即跳过一轮。
  const routeChangedRef = useRef(false);

  // URL → runtime：仅当路由参数真实变化时触发（深链 / 前进后退 / 手改地址）
  useEffect(() => {
    if (lastRouteRef.current === routeThreadId) return;
    lastRouteRef.current = routeThreadId;
    routeChangedRef.current = true;
    if (routeThreadId) {
      // 镜像已与路由一致（如 runtime→URL 刚推过来的），无需重复切换
      if (routeThreadId !== currentThreadId) {
        void aui.threads().switchToThread(routeThreadId);
      }
      return;
    }
    // 落在 "/" 且镜像仍是既有会话：手改地址回根路径 = 回到新会话
    if (currentThreadId) {
      void aui.threads().switchToNewThread();
    }
  }, [routeThreadId, currentThreadId, aui]);

  // runtime → URL：镜像变化驱动地址栏
  useEffect(() => {
    if (routeChangedRef.current) {
      // 本轮由路由参数变化主导：镜像可能是旧值，交由 URL→runtime 方向处理
      routeChangedRef.current = false;
      return;
    }
    if (currentThreadId === undefined) return; // 启动初态不动 URL
    if (currentThreadId === null) {
      // 新会话语义；replace 避免后退落进已删除/已离开的会话
      if (pathname !== "/") navigate("/", { replace: true });
      return;
    }
    if (pathname !== `/chat/${currentThreadId}`) {
      navigate(`/chat/${currentThreadId}`);
    }
  }, [currentThreadId, pathname, navigate]);

  return null;
};
