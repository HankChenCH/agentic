"""多模态消息内容助手：存储形态与 LangChain 内容块之间的唯一翻译点。

存储形态（ag-ui InputContent JSON，camelCase）：用户消息 ``content`` 为
内容数组，如 ``[{"type": "text", "text": ...}, {"type": "image", "source":
{"type": "data" | "url", "value": ..., "mimeType": ...}}]``；经
``RunMessage``（pydantic + ag-ui 模型）校验后由端点归一落库。

LangChain 形态（langchain_core 1.x 标准多模态块）：``{"type": "image",
"base64": ..., "mime_type": ...}`` / ``{"type": "image", "url": ...}``，
BaseChatOpenAI 系模型类（含 ChatDeepSeek）自动翻译为厂商请求格式。

图片解析策略（vision 模型）：``data`` source 直接转 base64 块；``url``
source 若为本域附件引用（``/agentic/attachments/...``）经 ``image_resolver``
读对象存储字节转 base64 块（模型云端拉不到内网 rustfs，必须在服务端取回），
外部 http(s) URL 透传 url 块。任何解析失败一律降级丢弃，不让单张坏图
打断整轮流。

降级策略（模型未声明 vision）：图片丢弃；当前轮消息文本尾追加注明
（用户能从回答察觉图片没被看到），历史回放静默（注明只需服务一次，
避免污染后续轮的 prompt）。
"""

from typing import Callable

import base64

from langchain.messages import HumanMessage

# 存储形态的 part type 常量（ag-ui InputContent 判别键）
_TEXT_PART = "text"
_IMAGE_PART = "image"

# 附件引用 URL 前缀（与 api/v1/endpoints/attachments.py 的路由约定一致）
_ATTACHMENT_URL_PREFIX = "/agentic/attachments/"

# 降级注明的尾缀模板
_DEGRADE_NOTE = "\n\n（已忽略 {count} 张图片：当前模型不支持图片输入）"

# url source 取不到 mimeType 时的兜底（与上传端点允许集一致，全部为常见图型）
_DEFAULT_IMAGE_MIME = "image/png"


def text_of(content: str | list) -> str:
    """提取内容中的纯文本（text part 按序拼接）。标题生成/记忆抽取等
    纯文本消费方的统一入口；字符串形态原样返回。"""
    if isinstance(content, str):
        return content.strip()
    return "\n".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == _TEXT_PART and part.get("text")
    ).strip()


def image_parts_of(content: str | list) -> list[dict]:
    """提取内容中的 image part（存储形态，camelCase key）。"""
    if isinstance(content, str):
        return []
    return [part for part in content if isinstance(part, dict) and part.get("type") == _IMAGE_PART]


def user_message_from_content(
    content: str | list,
    *,
    supports_vision: bool,
    image_resolver: Callable[[str], bytes | None] | None = None,
    note_on_degrade: bool = True,
) -> HumanMessage:
    """把存储形态的 user content 转成 LangChain HumanMessage。

    ``image_resolver(url_value) -> bytes | None``：本域附件引用读字节
    （命中返回字节，非本域/读取失败返回 None），由编排层用附件 store +
    消息属主身份构造；纯文本或 data source 时不需要。

    纯文本消息（无图片）无论能力声明如何都走字符串 content——与既有
    链路（BaseAgent._input 的时间前缀、RAG 的文本折叠）完全兼容。
    """
    if isinstance(content, str):
        return HumanMessage(content=content)

    if not supports_vision:
        text = text_of(content)
        dropped = len(image_parts_of(content))
        if note_on_degrade and dropped > 0:
            text = f"{text}{_DEGRADE_NOTE.format(count=dropped)}" if text else _DEGRADE_NOTE.format(count=dropped).lstrip("\n")
        return HumanMessage(content=text)

    blocks: list[dict] = []
    text_fragments: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == _TEXT_PART and part.get("text"):
            text_fragments.append(part["text"])
        elif part_type == _IMAGE_PART:
            block = _image_block(part.get("source") or {}, image_resolver)
            if block is not None:
                blocks.append(block)

    text = "\n".join(text_fragments).strip()
    if text:
        # text 块保持在前：厂商对文本+图混合内容的指令理解以开头文本为锚
        blocks.insert(0, {"type": _TEXT_PART, "text": text})
    return HumanMessage(content=blocks or "")


def _image_block(source: dict, image_resolver: Callable[[str], bytes | None] | None) -> dict | None:
    """单个 image source → LangChain 图片块；不可解析返回 None（调用方丢弃）。"""
    source_type = source.get("type")
    mime = source.get("mimeType") or _DEFAULT_IMAGE_MIME

    if source_type == "data" and source.get("value"):
        return {"type": _IMAGE_PART, "base64": source["value"], "mime_type": mime}

    if source_type == "url" and source.get("value"):
        value = source["value"]
        if value.startswith(_ATTACHMENT_URL_PREFIX):
            # 本域附件引用：服务端读对象存储转 base64（解析失败降级丢弃）
            if image_resolver is None:
                return None
            data = image_resolver(value)
            if not data:
                return None
            return {"type": _IMAGE_PART, "base64": _to_base64(data), "mime_type": mime}
        if value.startswith(("http://", "https://")):
            # 外部 URL 透传（能否拉取归厂商）；本域前缀之外的地址不作服务端代拉
            return {"type": _IMAGE_PART, "url": value}
    return None


def _to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
