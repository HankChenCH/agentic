"""MinerU 云端 Open API（v4）解析供应商。

四步协议（https://mineru.net/apiManage/docs）：
1. ``POST /api/v4/file-urls/batch``（Bearer token）申请预签名上传 URL + batch_id
2. ``PUT`` 文件字节到预签名 URL（无鉴权头）
3. 轮询 ``GET /api/v4/extract-results/batch/{batch_id}`` 直到 state=done/failed
4. ``GET full_zip_url`` 下载结果 zip，取 ``*_content_list.json`` / ``*.md`` / ``images/*``

归一化策略：content_list 平铺块翻译成 ParsedBlock；vlm 后端的丢弃块
（header/footer/page_number/aside_text/page_footnote）在此过滤——它们是
版面噪音，不应进入分块与索引。``content_list_v2.json`` 官方仍在开发中，不采用。

bbox 保留策略：content_list 的 bbox 为 0-1000 归一化坐标（左上原点、
相对页面宽高），统一换算成 0-1 浮点后进入 ParsedBlock——下游（分块 meta
→ 向量 metadata → 工具结果 → 前端高亮）不需要页面尺寸即可定位。
"""

import fnmatch
import io
import json
import math
import time
import zipfile
from typing import Any

import httpx

from app.core.config import MineruCloudEntry
from app.core.exceptions import InfrastructureError
from app.infrastructures.document_parser.document_parser_provider import (
    DocumentParser,
    DocumentParserBuilder,
    DocumentParserProvider,
    register,
)
from app.infrastructures.document_parser.models import ParsedBlock, ParsedBlockType, ParsedDocument

# vlm 后端输出的版面噪音块：不进入归一化结果
_DISCARDED_TYPES = {"header", "footer", "page_number", "aside_text", "page_footnote"}

# 云端解析任务终态：其余状态（waiting-file/pending/running）继续轮询
_DONE_STATE = "done"
_FAILED_STATE = "failed"


class MineruCloudParser(DocumentParser):
    def __init__(self, entry: MineruCloudEntry):
        self._entry = entry
        self._base = entry.api_url.rstrip("/")
        self._token = entry.api_key.get_secret_value()

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        if not self._token:
            raise InfrastructureError(
                "mineru cloud api key is empty: set MINERU_API_KEY (document_parser.yaml entry 'mineru-cloud')"
            )
        batch_id, upload_url = self._request_upload_url(filename)
        self._upload(upload_url, data, filename)
        result = self._poll_result(batch_id)
        zip_bytes = self._download_zip(result["full_zip_url"])
        return self._normalize(zip_bytes)

    # ---------- 协议四步 ----------

    def _request_upload_url(self, filename: str) -> tuple[str, str]:
        body = {
            "enable_formula": self._entry.enable_formula,
            "enable_table": self._entry.enable_table,
            "language": self._entry.language,
            "files": [{"name": filename, "is_ocr": self._entry.is_ocr}],
        }
        payload = self._request_json("POST", "/api/v4/file-urls/batch", json_body=body)
        urls = payload.get("file_urls") or []
        if not payload.get("batch_id") or not urls:
            raise InfrastructureError(f"mineru cloud returned no batch_id/file_urls: {payload}")
        return payload["batch_id"], urls[0]

    def _upload(self, upload_url: str, data: bytes, filename: str) -> None:
        # 预签名 URL 不带鉴权头；Content-Type 与存储侧约定无关，不参与签名
        try:
            resp = httpx.put(upload_url, content=data, timeout=self._entry.timeout)
        except httpx.HTTPError as exc:
            raise InfrastructureError(f"mineru cloud upload failed for {filename}") from exc
        if resp.status_code // 100 != 2:
            raise InfrastructureError(
                f"mineru cloud upload failed for {filename}: HTTP {resp.status_code} {resp.text[:200]}"
            )

    def _poll_result(self, batch_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self._entry.poll_timeout
        while True:
            payload = self._request_json("GET", f"/api/v4/extract-results/batch/{batch_id}")
            results = payload.get("extract_result") or []
            if not results:
                raise InfrastructureError(f"mineru cloud batch {batch_id} returned empty extract_result")
            state = results[0].get("state")
            if state == _DONE_STATE:
                return results[0]
            if state == _FAILED_STATE:
                err = results[0].get("err_msg") or "unknown error"
                raise InfrastructureError(f"mineru cloud parse failed: {err}")
            if time.monotonic() >= deadline:
                raise InfrastructureError(
                    f"mineru cloud parse timed out after {self._entry.poll_timeout}s (state: {state})"
                )
            time.sleep(self._entry.poll_interval)

    def _download_zip(self, zip_url: str) -> bytes:
        try:
            resp = httpx.get(zip_url, timeout=self._entry.timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise InfrastructureError("mineru cloud result zip download failed") from exc
        if resp.status_code // 100 != 2:
            raise InfrastructureError(
                f"mineru cloud result zip download failed: HTTP {resp.status_code}"
            )
        return resp.content

    # ---------- 结果归一化 ----------

    def _normalize(self, zip_bytes: bytes) -> ParsedDocument:
        try:
            archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise InfrastructureError("mineru cloud result is not a valid zip archive") from exc

        names = archive.namelist()
        content_list_name = next(
            (n for n in names if n.endswith("_content_list.json")), None
        )
        if content_list_name is None:
            raise InfrastructureError(f"mineru cloud result zip has no *_content_list.json: {names}")
        md_name = next((n for n in names if n.endswith(".md") and not n.endswith("_origin.md")), None)

        raw_blocks = json.loads(archive.read(content_list_name).decode("utf-8"))
        if not isinstance(raw_blocks, list):
            raise InfrastructureError("mineru cloud content_list is not a json array")

        # 图片资产以 zip 相对路径为 key，与块内 img_path 一致；调用方转存后即弃
        assets = {
            name: archive.read(name)
            for name in fnmatch.filter(names, "images/*")
            if not name.endswith("/")
        }
        md_content = archive.read(md_name).decode("utf-8") if md_name else None
        return ParsedDocument(
            blocks=_normalize_blocks(raw_blocks, assets),
            assets=assets,
            md_content=md_content,
        )

    # ---------- 内部 ----------

    def _request_json(self, method: str, path: str, json_body: dict | None = None) -> dict[str, Any]:
        try:
            resp = httpx.request(
                method,
                f"{self._base}{path}",
                json=json_body,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._entry.timeout,
            )
        except httpx.HTTPError as exc:
            raise InfrastructureError(f"mineru cloud request failed: {method} {path}") from exc
        if resp.status_code // 100 != 2:
            raise InfrastructureError(
                f"mineru cloud request failed: {method} {path} HTTP {resp.status_code} {resp.text[:200]}"
            )
        payload = resp.json()
        if payload.get("code") != 0:
            raise InfrastructureError(
                f"mineru cloud api error: {method} {path} code={payload.get('code')} msg={payload.get('msg')}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise InfrastructureError(f"mineru cloud api returned no data object: {method} {path}")
        return data


def _normalize_blocks(raw_blocks: list[dict[str, Any]], assets: dict[str, bytes]) -> list[ParsedBlock]:
    """content_list 原始块 → ParsedBlock；字段缺失一律宽容降级（默认值）。"""
    blocks: list[ParsedBlock] = []
    for raw in raw_blocks:
        block_type = raw.get("type")
        if block_type in _DISCARDED_TYPES:
            continue
        page_idx = _to_int(raw.get("page_idx"), 0)
        bbox = _to_bbox(raw.get("bbox"))
        asset_name = raw.get("img_path")
        if asset_name and asset_name not in assets:
            asset_name = None
        if block_type == "text":
            text = str(raw.get("text") or "")
            if not text.strip():
                continue
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.TEXT,
                    text=text,
                    text_level=_to_int(raw.get("text_level"), 0),
                    page_idx=page_idx,
                    bbox=bbox,
                    sub_type=raw.get("sub_type"),
                )
            )
        elif block_type == "table":
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.TABLE,
                    html=str(raw.get("table_body") or ""),
                    captions=_to_str_list(raw.get("table_caption")),
                    footnotes=_to_str_list(raw.get("table_footnote")),
                    page_idx=page_idx,
                    bbox=bbox,
                    asset_name=asset_name,
                )
            )
        elif block_type in ("image", "chart"):
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.IMAGE if block_type == "image" else ParsedBlockType.CHART,
                    captions=_to_str_list(
                        raw.get("image_caption") or raw.get("chart_caption")
                    ),
                    footnotes=_to_str_list(
                        raw.get("image_footnote") or raw.get("chart_footnote")
                    ),
                    page_idx=page_idx,
                    bbox=bbox,
                    asset_name=asset_name,
                )
            )
        elif block_type == "equation":
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.EQUATION,
                    text=str(raw.get("text") or ""),
                    sub_type=raw.get("text_format"),
                    page_idx=page_idx,
                    bbox=bbox,
                    asset_name=asset_name,
                )
            )
        elif block_type == "code":
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.CODE,
                    text=str(raw.get("code_body") or ""),
                    sub_type=raw.get("sub_type"),
                    captions=_to_str_list(raw.get("code_caption")),
                    footnotes=_to_str_list(raw.get("code_footnote")),
                    page_idx=page_idx,
                    bbox=bbox,
                )
            )
        elif block_type == "list":
            # list_items 元素可能是字符串或 {type, text} 结构（2.5 版本差异），统一取文本
            items = [
                str(item.get("text") if isinstance(item, dict) else item or "")
                for item in (raw.get("list_items") or [])
            ]
            text = "\n".join(i for i in items if i.strip())
            if not text:
                continue
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.LIST,
                    text=text,
                    sub_type=raw.get("sub_type"),
                    page_idx=page_idx,
                    bbox=bbox,
                )
            )
        # 未知类型（未来版本新增）静默跳过：归一化层保证未知块不破坏入库
    return blocks


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bbox(value: Any) -> list[float] | None:
    """content_list 的 bbox → 0-1 归一化 [x0, y0, x1, y1]（左上原点，相对页面宽高）。

    MinerU 各版本/后端的坐标基准不一（官方 content_list 为 0-1000，部分
    新版输出 0-1 百分比）：max>1.5 判为 0-1000 制并归一到 0-1。脏数据
    （长度≠4 / 非数值 / 非有限数 / 非法几何）返回 None，溯源降级不阻断入库。
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        coords = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(c) for c in coords):
        return None
    x0, y0, x1, y1 = coords
    if x1 <= x0 or y1 <= y0:
        return None
    if max(coords) > 1.5:
        coords = [c / 1000 for c in coords]
    return [round(min(c, 1.0), 3) for c in coords]


def _to_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v]


@register
class MineruCloudBuilder(DocumentParserBuilder):
    provider = DocumentParserProvider.MINERU_CLOUD

    def build(self, entry: MineruCloudEntry) -> DocumentParser:
        return MineruCloudParser(entry)
