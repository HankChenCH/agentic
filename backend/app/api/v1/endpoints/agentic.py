from typing import Annotated, AsyncIterator, Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool
from starlette.types import Receive, Scope, Send
from wireup import Injected

from app.api.deps import UserPrincipal, require_user
from app.application import (
    AgentCatalogService,
    AgenticService,
    ConversationAppService,
    ToolCatalogService,
)

from app.models.schema.request.pagination import PaginationRequest
from app.models.schema.request.conversation import ActivateTurnRequest
from app.models.schema.request.run import CancelRequest, RunRequest
from app.models.schema.response.biz_response import Response

# router 级鉴权在 cmd/http/main.py 的 include_router 处统一挂载（无路径白名单）；
# 各端点再声明 Depends(require_user) 取身份——依赖结果每请求缓存，验签只跑一次。
router = APIRouter(prefix="/agentic", tags=["Agentic"])


async def _stream_and_close(events: Iterator[str]) -> AsyncIterator[str]:
    """同步 SSE 生成器 → async 流，并在任何收尾路径确定性 close 底层生成器。

    starlette 的 iterate_in_threadpool 没有 finally：断连取消时 CancelledError
    从 __anext__ 抛出直接终结 async 生成器帧，底层同步生成器无人 close，只能
    等循环 GC 才收到 GeneratorExit（实测分钟级），编排层的断连收口（轮次置
    CANCELED）随之延迟、进程先退出则丢失。这里包一层 finally close——close
    会吞掉生成器重抛的 GeneratorExit，收口逻辑在帧退出前同步执行完成。
    """
    try:
        async for frame in iterate_in_threadpool(events):
            yield frame
    finally:
        events.close()


class ClosingStreamingResponse(StreamingResponse):
    """响应周期结束（正常完成或客户端断连）时确定性关闭 body 迭代器的 SSE 响应。

    Starlette 从不收尾 body 迭代器：正常结束靠迭代器自然耗尽；断连时（uvicorn
    spec_version=2.3 走 listen_for_disconnect 取消流任务；spec_version>=2.4 靠
    send() 抛 OSError）直接把迭代器抛弃。配合 _stream_and_close 使用：断连瞬间
    在 finally 里 aclose 包装器 → 触发底层同步生成器的确定性 close；迭代器已
    自然耗尽时 aclose 是 no-op。
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                await aclose()

# 受众分流：全部业务经 application 用例层——run 是用户侧行程（AgenticService），
# conversation 增删查是管理侧用例（ConversationAppService）。身份经
# require_user 注入（JWT 无状态验签）；归属校验在领域层（仓储查询条件 /
# open_turn 比对），非本人会话统一 404。

@router.get("/conversation")
def list_conversation(
    conversations: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    pagination: Annotated[PaginationRequest, Depends()],
):
    envelope = conversations.list_conversations(user_id=principal.user_id, page=pagination.page, page_size=pagination.pageSize)
    # to_dict() 剔除 None 调试字段，保证成功响应体稳定（直接返回 dataclass 会带 null 字段）
    return Response.success(envelope).to_dict()

@router.get("/conversation/{thread_id}")
def get_conversation(
    conversations: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    thread_id: UUID,
):
    return Response.success(conversations.describe_conversation(user_id=principal.user_id, thread_id=thread_id)).to_dict()

@router.get("/conversation/{thread_id}/history")
def list_conversation_history_messages(
    conversations: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    thread_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=0, le=100),
):
    messages = conversations.list_history_messages(user_id=principal.user_id, thread_id=thread_id, offset=offset, limit=limit)
    return Response.success(messages).to_dict()

@router.delete("/conversation/{thread_id}")
def delete_conversation(
    conversations: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    thread_id: UUID,
):
    return Response.success(conversations.delete_conversation(user_id=principal.user_id, thread_id=thread_id)).to_dict()

@router.post("/conversation/{thread_id}/activate-turn")
def activate_conversation_turn(
    conversations: Injected[ConversationAppService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    thread_id: UUID,
    request: ActivateTurnRequest,
):
    # 末梢扇形内的变体切换：把活跃叶子移动到所选轮次（持久化用户在分支
    # 对比中的选择），后续 run 的 parent 与多轮回放均从新叶子派生。
    # 校验在领域层（归属 + 已完成 + 末梢），违规按业务错误如实透出。
    conversation = conversations.activate_turn(
        user_id=principal.user_id, thread_id=thread_id, turn_id=request.turnId,
    )
    return Response.success(conversation).to_dict()

@router.get("/agents")
def list_agents(catalog: Injected[AgentCatalogService]):
    # 智能体目录（id/展示名/描述/图片能力）：前端选择 UI 消费，选中项经 run
    # 请求的 forwardedProps.agentId 上送绑定。目录与用户无关，认证由 router
    # 级依赖统一覆盖，端点无需 principal。
    return Response.success(catalog.describe()).to_dict()

@router.get("/tool-catalog")
def get_tool_catalog(catalog: Injected[ToolCatalogService]):
    # 工具能力目录（组件 → 工具的名/展示标题/描述/参数 schema）：前端 UI 标识化
    # 消费。目录与用户无关（纯静态注册表读），认证由 router 级依赖统一覆盖，
    # 端点无需 principal。
    return Response.success(catalog.describe()).to_dict()

@router.post("/run")
def run(
    service: Injected[AgenticService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    request: RunRequest,
):
    # service.run() 已输出 SSE 帧（"data: {...}\n\n"），endpoint 纯透传。
    # 多轮历史以服务端（库）为准：请求只取末条用户消息作为当前提问，payload 历史不回放
    # （但整体投影给 open_turn 做"重试最新一轮"自动检测）。
    # content 经 RunMessage 归一为存储形态内容数组（纯文本 = 单 text part 的退化形态）。
    # forwardedProps.agentId：前端选择的智能体（缺省/未知由编排层回退会话绑定）。
    # forwardedProps.branch：重试/编辑的显式分支信号（baseMessageId），服务端
    # 据此精确定位兄弟基点；缺省走自动检测（纯文本会话同样可命中）。
    forwarded = request.forwardedProps or {}
    agent_id = forwarded.get("agentId")
    branch = forwarded.get("branch")
    payload = [(m.role, m.storage_content()) for m in request.messages]
    events = service.run(
        principal.user_id, request.threadId, request.runId,
        request.messages[-1].storage_content(),
        agent_id=agent_id if isinstance(agent_id, str) and agent_id else None,
        payload=payload,
        branch=branch if isinstance(branch, dict) else None,
    )
    return ClosingStreamingResponse(_stream_and_close(events), media_type="text/event-stream")

@router.post("/run/cancel")
def cancel_run(
    service: Injected[AgenticService],
    principal: Annotated[UserPrincipal, Depends(require_user)],
    request: CancelRequest,
):
    # 显式取消通道：先过归属校验再置 Redis 取消标志即返回（幂等），运行中的流式
    # 循环在节流边界感知后收口。不等流结束——同步生成器无法从外部打断在执行的
    # next()，等待时长不可控；前端配合本地 abort 获得即时取消态。
    service.cancel_run(principal.user_id, request.threadId)
    return Response.success({"canceled": True}).to_dict()
