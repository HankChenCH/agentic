// @vitest-environment jsdom
// jest-dom 匹配器（运行时经 vitest.setup.ts 全局注入；这里显式引入以获得类型增强）
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentModeSwitch } from "@/components/assistant-ui/agent-selector";
import { UploadDocumentDialog } from "@/components/knowledge/upload-document-dialog";
import { toast } from "sonner";
import type { AgentInfo } from "@/services/agent-service";
import { useAgentStore } from "@/stores/agent-store";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/services/agent-service", () => ({ agentService: { listAgents: vi.fn() } }));

const toastMock = vi.mocked(toast);

const makeAgent = (id: string, name: string): AgentInfo =>
  ({ id, name, description: `描述 ${name}` }) as AgentInfo;

const seedAgents = (agents: AgentInfo[]) =>
  useAgentStore.setState({
    agents,
    defaultAgentId: agents[0]?.id ?? null,
    selectedAgentId: null,
    loaded: true,
    knownThreads: {},
  });

const resetStore = () =>
  useAgentStore.setState({
    agents: [],
    defaultAgentId: null,
    selectedAgentId: null,
    loaded: false,
    knownThreads: {},
  });

beforeEach(resetStore);
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentModeSwitch（智能体选择冒烟）", () => {
  it("目录未就绪：渲染 null 不闪烁占位", () => {
    const { container } = render(<AgentModeSwitch />);
    expect(container).toBeEmptyDOMElement();
  });

  it("≤3 个智能体：pills 直显，点击回写 agent-store", async () => {
    const user = userEvent.setup();
    seedAgents([
      makeAgent("builtin:demo", "演示"),
      makeAgent("builtin:rag", "知识库"),
    ]);
    render(<AgentModeSwitch />);

    expect(screen.getByRole("button", { name: /演示/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /知识库/ })).toBeInTheDocument();
    // 未显式选择时按服务端默认高亮（aria 无法直接断言样式，验证点击行为）
    await user.click(screen.getByRole("button", { name: /知识库/ }));
    expect(useAgentStore.getState().selectedAgentId).toBe("builtin:rag");
  });

  it(">3 个智能体：前 3 个直显，其余收进「更多」触发器", () => {
    seedAgents([
      makeAgent("a1", "甲"),
      makeAgent("a2", "乙"),
      makeAgent("a3", "丙"),
      makeAgent("a4", "丁"),
    ]);
    render(<AgentModeSwitch />);

    expect(screen.getByRole("button", { name: /甲/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /乙/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /丙/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /丁/ })).not.toBeInTheDocument();
    expect(screen.getByTitle("更多智能体")).toBeInTheDocument();
  });
});

describe("UploadDocumentDialog（上传弹窗冒烟）", () => {
  type UploadFn = (file: File, options?: { name?: string; description?: string }) => Promise<boolean>;
  const renderDialog = (onUpload: UploadFn) =>
    render(
      <UploadDocumentDialog open onOpenChange={() => {}} onUpload={onUpload} />,
    );

  it("dropzone accept 与后端白名单同步（pdf/xlsx/docx/md）", () => {
    renderDialog(async () => true);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeInTheDocument();
    // react-dropzone 把 accept 对象序列化为逗号分隔的 "mime,.ext" 串
    const accept = input.getAttribute("accept") ?? "";
    for (const mime of [
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "text/markdown",
    ]) {
      expect(accept).toContain(mime);
    }
    for (const ext of [".pdf", ".xlsx", ".docx", ".md"]) {
      expect(accept).toContain(ext);
    }
  });

  it("选文件 → 上传：串行逐个调用 onUpload（带描述），全成后 toast + 关闭", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn(async () => true);
    renderDialog(onUpload);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, [
      new File(["a"], "one.md", { type: "text/markdown" }),
      new File(["b"], "two.md", { type: "text/markdown" }),
    ]);
    expect(screen.getByText("one.md")).toBeInTheDocument();
    expect(screen.getByText("two.md")).toBeInTheDocument();

    await user.type(screen.getByLabelText("描述"), "测试描述");
    await user.click(screen.getByRole("button", { name: /上传/ }));

    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(2));
    // 串行：第二发的描述参数带上输入
    expect(onUpload).toHaveBeenNthCalledWith(1, expect.any(File), { description: "测试描述" });
    expect(onUpload).toHaveBeenNthCalledWith(2, expect.any(File), { description: "测试描述" });
    expect(toastMock.success).toHaveBeenCalledWith("已提交 2/2 个文档，解析入库后生效");
  });

  it("有失败项：toast 报成功数、列表清空便于重试、弹窗保持打开", async () => {
    const user = userEvent.setup();
    const onUpload = vi
      .fn<(file: File, options?: object) => Promise<boolean>>()
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false);
    renderDialog(onUpload);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, [
      new File(["a"], "one.md", { type: "text/markdown" }),
      new File(["b"], "two.md", { type: "text/markdown" }),
    ]);
    await user.click(screen.getByRole("button", { name: /上传/ }));

    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("已提交 1/2 个文档，解析入库后生效"));
    // 非全成：不清空之外的分支——列表清空重选，弹窗不关闭
    expect(screen.queryByText("one.md")).not.toBeInTheDocument();
    expect(screen.queryByText("two.md")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /上传/ })).toBeInTheDocument();
  });

  it("未选文件时上传按钮禁用", () => {
    renderDialog(async () => true);
    expect(screen.getByRole("button", { name: /上传/ })).toBeDisabled();
  });
});
