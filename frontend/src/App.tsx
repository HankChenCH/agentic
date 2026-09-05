import { lazy, Suspense } from "react";
import {
  createBrowserRouter,
  Navigate,
  Outlet,
  RouterProvider,
  useLocation,
  useParams,
} from "react-router";

import { Toaster } from "@/components/ui/sonner";
import { PdfPreviewProvider } from "@/components/shared/pdf-preview-provider";
import { Skeleton } from "@/components/ui/skeleton";
import { ChatPage } from "@/pages/chat-page";
import { LoginPage } from "@/pages/login-page";
import { ProfilePage } from "@/pages/profile-page";
import { useAuthStore } from "@/stores/auth-store";

import { AgenticRuntimeProvider } from "./agentic-runtime";

// 管理侧页面按路由分割 chunk（React.lazy）：图谱页拖 @xyflow/react、
// 知识库页拖 dropzone/预览等重依赖，聊天首屏不必加载。
const AdminHomePage = lazy(() =>
  import("@/pages/admin/admin-home-page").then((m) => ({ default: m.AdminHomePage })),
);
const KnowledgeListPage = lazy(() =>
  import("@/pages/admin/knowledge-list-page").then((m) => ({ default: m.KnowledgeListPage })),
);
const KnowledgeDetailPage = lazy(() =>
  import("@/pages/admin/knowledge-detail-page").then((m) => ({ default: m.KnowledgeDetailPage })),
);
const KnowledgeDocumentDetailPage = lazy(() =>
  import("@/pages/admin/knowledge-document-detail-page").then((m) => ({
    default: m.KnowledgeDocumentDetailPage,
  })),
);
const MemoryGraphPage = lazy(() =>
  import("@/pages/admin/memory-graph-page").then((m) => ({ default: m.MemoryGraphPage })),
);
const UsageStatsPage = lazy(() =>
  import("@/pages/admin/usage-stats-page").then((m) => ({ default: m.UsageStatsPage })),
);

/** 分包页面的加载占位（骨架屏，避免 Suspense 空白闪烁） */
const LazyFallback = () => (
  <div className="flex h-screen items-center justify-center bg-background">
    <div className="flex flex-col items-center gap-3">
      <Skeleton className="size-24 rounded-xl" />
      <Skeleton className="h-4 w-40" />
    </div>
  </div>
);

// 旧知识库详情路径重定向：Navigate 不解析 :params，需先取出再拼目标
const KnowledgeDetailRedirect = () => {
  const { kbId } = useParams<{ kbId: string }>();
  return <Navigate to={`/admin/knowledge/${kbId}`} replace />;
};

// 认证守卫：未登录跳 /login（带 next 回跳参数），已登录渲染子路由
const RequireAuth = () => {
  const token = useAuthStore((s) => s.token);
  const location = useLocation();
  if (!token) {
    return (
      <Navigate to="/login" replace state={{ from: location.pathname }} />
    );
  }
  return <Outlet />;
};

// 已登录访问 /login 时直接回聊天页（避免重复登录）
const LoginPageOrRedirect = () => {
  const token = useAuthStore((s) => s.token);
  if (token) return <Navigate to="/" replace />;
  return <LoginPage />;
};

// 路由表：/ 与 /chat/:threadId 都是聊天页（threadId 由 ThreadRouteSync 与
// runtime 双向同步）；/admin 管理侧（模块启动页），知识库等管理模块
// 挂在 /admin/<module> 下；旧 /knowledge* 路径重定向兜底。
// 除 /login 外全部经 RequireAuth 守卫（会话/记忆都是用户级数据）。
// AgenticRuntimeProvider 放在路由外层：聊天 runtime（当前会话、composer
// 草稿）不随页面切换重建 —— 去管理侧再返回对话时状态原样保留。
// 会话列表初始拉取在 use-conversation-list 内按登录态门控，未登录启动不发请求。
const router = createBrowserRouter([
  { path: "/login", element: <LoginPageOrRedirect /> },
  {
    element: <RequireAuth />,
    children: [
      { path: "/", element: <ChatPage /> },
      { path: "/chat/:threadId", element: <ChatPage /> },
      { path: "/profile", element: <ProfilePage /> },
      { path: "/admin", element: <Suspense fallback={<LazyFallback />}><AdminHomePage /></Suspense> },
      { path: "/admin/knowledge", element: <Suspense fallback={<LazyFallback />}><KnowledgeListPage /></Suspense> },
      { path: "/admin/knowledge/:kbId", element: <Suspense fallback={<LazyFallback />}><KnowledgeDetailPage /></Suspense> },
      {
        path: "/admin/knowledge/:kbId/document/:docId",
        element: (
          <Suspense fallback={<LazyFallback />}>
            <KnowledgeDocumentDetailPage />
          </Suspense>
        ),
      },
      { path: "/admin/memory-graph", element: <Suspense fallback={<LazyFallback />}><MemoryGraphPage /></Suspense> },
      { path: "/admin/usage", element: <Suspense fallback={<LazyFallback />}><UsageStatsPage /></Suspense> },
      { path: "/knowledge", element: <Navigate to="/admin/knowledge" replace /> },
      {
        path: "/knowledge/:kbId",
        element: <KnowledgeDetailRedirect />,
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);

const App = () => {
  return (
    <AgenticRuntimeProvider>
      {/* 全局 PDF 预览：聊天溯源卡片与管理侧文档表格共用一个弹窗 */}
      <PdfPreviewProvider>
        <RouterProvider router={router} />
      </PdfPreviewProvider>
      {/* 操作反馈（sonner）：成功提示 / 业务错误 error_message */}
      <Toaster position="top-center" />
    </AgenticRuntimeProvider>
  );
};

export default App;
