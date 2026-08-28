import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
  useParams,
} from "react-router";

import { Toaster } from "@/components/ui/sonner";
import { PdfPreviewProvider } from "@/components/shared/pdf-preview-provider";
import { AdminHomePage } from "@/pages/admin/admin-home-page";
import { KnowledgeDetailPage } from "@/pages/admin/knowledge-detail-page";
import { KnowledgeListPage } from "@/pages/admin/knowledge-list-page";
import { MemoryGraphPage } from "@/pages/admin/memory-graph-page";
import { ChatPage } from "@/pages/chat-page";

import { AgenticRuntimeProvider } from "./agentic-runtime";

// 旧知识库详情路径重定向：Navigate 不解析 :params，需先取出再拼目标
const KnowledgeDetailRedirect = () => {
  const { kbId } = useParams<{ kbId: string }>();
  return <Navigate to={`/admin/knowledge/${kbId}`} replace />;
};

// 路由表：/ 纯对话聊天；/admin 管理侧（模块启动页），知识库等管理模块
// 挂在 /admin/<module> 下；旧 /knowledge* 路径重定向兜底。
// AgenticRuntimeProvider 放在路由外层：聊天 runtime（当前会话、composer
// 草稿）不随页面切换重建 —— 去管理侧再返回对话时状态原样保留。
const router = createBrowserRouter([
  { path: "/", element: <ChatPage /> },
  { path: "/admin", element: <AdminHomePage /> },
  { path: "/admin/knowledge", element: <KnowledgeListPage /> },
  { path: "/admin/knowledge/:kbId", element: <KnowledgeDetailPage /> },
  { path: "/admin/memory-graph", element: <MemoryGraphPage /> },
  { path: "/knowledge", element: <Navigate to="/admin/knowledge" replace /> },
  {
    path: "/knowledge/:kbId",
    element: <KnowledgeDetailRedirect />,
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
