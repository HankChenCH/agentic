"""解析结果分块器：ParsedDocument → 入库分段草稿。

纯函数模块（无 DI）：规则为标题感知分块——
- ``text_level >= 1`` 的标题块封口当前段并更新标题面包屑，面包屑以
  ``h1 > h2`` 前置行进入段内容，提升无上下文嵌入的召回质量；
- 连续正文合并打包，软上限封口（``_MAX_SEGMENT_CHARS``），超长正文按
  段落边界二次切分到硬上限（``_HARD_SEGMENT_CHARS``）；
- 表格/公式/代码为原子块，绝不跨段切断；
- 图片/图表以题注文本入库（嵌入模型 bge-m3 仅文本），资产 key 由调用方
  （DocumentIngestionService）补全进 meta。

meta 结构：``{"page_start", "page_end", "heading_path": [str], "types": [str],
"assets": [str], "bboxes": [{"page": int, "bbox": [x0, y0, x1, y1]}]}``——
assets 此处为解析资产名，入库前由服务层改写为对象存储完整 key；bboxes 为
段内各块的 0-1 归一化溯源区域（有 bbox 的块才产出，供检索命中后定位原文）。
"""

from dataclasses import dataclass

from app.domain.ports import ParsedBlock, ParsedBlockType, ParsedDocument

# 软上限：加入新块后超过即封口当前段
_MAX_SEGMENT_CHARS = 800
# 硬上限：单段内容绝不超过；超长正文按段落边界切到该长度
_HARD_SEGMENT_CHARS = 1200

# 单段溯源区域条数上限：防御超长段的 meta 膨胀（定位展示取前若干块已够）
_MAX_SEGMENT_BBOXES = 40

# 原子块类型：整体成段或独立成块，不做字符级切分
_ATOMIC_TYPES = {ParsedBlockType.TABLE, ParsedBlockType.EQUATION, ParsedBlockType.CODE}


@dataclass
class SegmentDraft:
    """分段草稿：入库前的内容与元数据，由 DocumentIngestionService 补 id/资产 key 后落库。"""

    content: str
    word_count: int
    meta: dict


def chunk_document(parsed: ParsedDocument) -> list[SegmentDraft]:
    return _Chunker().run(parsed.blocks)


class _Chunker:
    def __init__(self) -> None:
        self._breadcrumb: list[str] = []
        self._buffer: list[tuple[str, ParsedBlock]] = []
        self._chunks: list[SegmentDraft] = []

    def run(self, blocks: list[ParsedBlock]) -> list[SegmentDraft]:
        for block in blocks:
            if block.type is ParsedBlockType.TEXT and block.text_level >= 1:
                # 标题即分块边界：封口当前段，更新面包屑（截掉更深层级）
                self._flush()
                title = block.text.strip()
                del self._breadcrumb[block.text_level - 1:]
                if title:
                    self._breadcrumb.append(title)
                continue
            for piece in _split_long(_render(block), block.type, self._body_limit()):
                self._push(piece, block)
        self._flush()
        return self._chunks

    def _push(self, text: str, block: ParsedBlock) -> None:
        # 标题在缓冲区存活期内不变（标题到达必先 flush），前缀长度可稳定计入
        current = self._prefix_len() + sum(len(t) for t, _ in self._buffer)
        if self._buffer and current + len(text) > _MAX_SEGMENT_CHARS:
            self._flush()
        self._buffer.append((text, block))
        # 超限的原子块（巨型表格等）立即封口，避免拖累后续块
        if block.type in _ATOMIC_TYPES and current + len(text) > _MAX_SEGMENT_CHARS:
            self._flush()

    def _prefix_len(self) -> int:
        # 面包屑前缀「h1 > h2\n\n」占用的字符数
        return len(" > ".join(self._breadcrumb)) + 2 if self._breadcrumb else 0

    def _body_limit(self) -> int:
        # 正文可用硬上限 = 总硬上限 - 前缀占用；兜底下限防超长面包屑挤爆正文
        return max(_HARD_SEGMENT_CHARS - self._prefix_len(), 200)

    def _flush(self) -> None:
        if not self._buffer:
            return
        body = "\n\n".join(text for text, _ in self._buffer)
        breadcrumb = " > ".join(self._breadcrumb)
        content = f"{breadcrumb}\n\n{body}" if breadcrumb else body
        pages = [block.page_idx for _, block in self._buffer]
        types = sorted({block.type.value for _, block in self._buffer})
        assets = [
            block.asset_name
            for _, block in self._buffer
            if block.asset_name and block.type
            in (ParsedBlockType.IMAGE, ParsedBlockType.CHART, ParsedBlockType.TABLE)
        ]
        meta = {
            "page_start": min(pages),
            "page_end": max(pages),
            "heading_path": list(self._breadcrumb),
            "types": types,
            "assets": assets,
        }
        bboxes = _collect_bboxes(self._buffer)
        if bboxes:
            meta["bboxes"] = bboxes
        self._chunks.append(
            SegmentDraft(
                content=content,
                word_count=len(content),
                meta=meta,
            )
        )
        self._buffer = []


def _collect_bboxes(buffer: list[tuple[str, ParsedBlock]]) -> list[dict]:
    """段内缓冲的溯源区域：按 (page, bbox) 去重——同块被 ``_split_long``
    切成多片时引用相同 block，重复框无展示价值；封顶防 meta 膨胀。"""
    seen: set[tuple[int, tuple[float, ...]]] = set()
    bboxes: list[dict] = []
    for _, block in buffer:
        if not block.bbox:
            continue
        key = (block.page_idx, tuple(block.bbox))
        if key in seen:
            continue
        seen.add(key)
        bboxes.append({"page": block.page_idx, "bbox": list(block.bbox)})
        if len(bboxes) >= _MAX_SEGMENT_BBOXES:
            break
    return bboxes


def _render(block: ParsedBlock) -> str:
    """块 → 可嵌入的 Markdown 文本；无可索引内容时返回空串。"""
    if block.type is ParsedBlockType.TEXT or block.type is ParsedBlockType.LIST:
        return block.text.strip()
    if block.type is ParsedBlockType.EQUATION:
        return f"$${block.text.strip()}$$" if block.text.strip() else ""
    if block.type is ParsedBlockType.CODE:
        body = block.text.rstrip()
        captions = _join_captions(block)
        return f"{captions}\n\n```\n{body}\n```".strip() if body else ""
    if block.type is ParsedBlockType.TABLE:
        parts = [_join_captions(block), block.html or "", _join_footnotes(block)]
        return "\n\n".join(p for p in parts if p)
    if block.type in (ParsedBlockType.IMAGE, ParsedBlockType.CHART):
        # 图片本体不可嵌入：仅题注/脚注成文；资产由服务层转存后记入 meta
        parts = [_join_captions(block), _join_footnotes(block)]
        return "\n\n".join(p for p in parts if p)
    return ""


def _join_captions(block: ParsedBlock) -> str:
    return "\n".join(c.strip() for c in block.captions if c.strip())


def _join_footnotes(block: ParsedBlock) -> str:
    footnotes = [f.strip() for f in block.footnotes if f.strip()]
    return "\n".join(f"注：{f}" for f in footnotes)


def _split_long(text: str, block_type: ParsedBlockType, limit: int = _HARD_SEGMENT_CHARS) -> list[str]:
    """超长正文按段落边界切分到 limit；原子块不切。"""
    if not text or block_type in _ATOMIC_TYPES or len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        while len(paragraph) > limit:
            # 单段落超限（无换行长文）：按长度硬切
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces
