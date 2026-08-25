import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { Toaster } from "@/components/ui/sonner";
import { PdfPreviewProvider } from "@/components/knowledge/pdf-preview-provider";
import { ChatPage } from "@/pages/chat-page";
import { KnowledgeDetailPage } from "@/pages/knowledge-detail-page";
import { KnowledgeListPage } from "@/pages/knowledge-list-page";

import { AgenticRuntimeProvider } from "./agentic-runtime";

// 路由表：/ 聊天（原有单屏布局），/knowledge 知识库卡片列表，
// /knowledge/:kbId 知识库详情（文档管理）。AgenticRuntimeProvider 放在
// 路由外层：聊天 runtime（当前会话、composer 草稿）不随页面切换重建。
const router = createBrowserRouter([
  { path: "/", element: <ChatPage /> },
  { path: "/knowledge", element: <KnowledgeListPage /> },
  { path: "/knowledge/:kbId", element: <KnowledgeDetailPage /> },
  { path: "*", element: <Navigate to="/" replace /> },
]);

const App = () => {
  return (
    <AgenticRuntimeProvider>
      {/* 全局 PDF 预览：聊天溯源卡片与知识库文档表格共用一个弹窗 */}
      <PdfPreviewProvider>
        <RouterProvider router={router} />
      </PdfPreviewProvider>
      {/* 操作反馈（sonner）：成功提示 / 业务错误 error_message */}
      <Toaster position="top-center" />
    </AgenticRuntimeProvider>
  );
};

export default App;
