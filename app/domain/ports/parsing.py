"""文档解析的归一化结果模型：隔离 MinerU 原始 content_list 的格式变化。

供应商 provider 负责把原始输出（如 content_list.json 的平铺块数组）翻译
成这里的 ParsedBlock/ParsedDocument；分块器与入库流水线只依赖本模型，
MinerU 升级或更换解析供应商时上层不动。
"""

from enum import Enum

from pydantic import BaseModel, Field


class ParsedBlockType(str, Enum):
    """归一化后的内容块类型（对齐 MinerU content_list 的常用类型）。"""

    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"
    CHART = "chart"
    EQUATION = "equation"
    CODE = "code"
    LIST = "list"


class ParsedBlock(BaseModel):
    """单个内容块：按阅读顺序平铺，字段按类型取用（text 类用 text/html）。

    ``page_idx`` 为 0 起页码；``text_level`` 仅 text 块有效（0/缺省为正文，
    1=h1、2=h2 …，作分块边界）；``asset_name`` 指向 ParsedDocument.assets
    里的图片字节（表格截图/插图等），由上层转存对象存储。
    """

    type: ParsedBlockType
    text: str = Field(default="", description="文本内容（正文 / LaTeX / 代码），按类型取用")
    text_level: int = Field(default=0, description="标题层级：0 正文，1=h1，2=h2 …")
    page_idx: int = Field(default=0, description="所在页码（0 起）")
    bbox: list[float] | None = Field(
        default=None,
        description="块区域坐标 [x0, y0, x1, y1]：0-1 归一化、左上原点、相对页面宽高（溯源用，前端按百分比定位高亮）",
    )
    html: str | None = Field(default=None, description="表体 HTML（table 块）")
    captions: list[str] = Field(default=[], description="题注（表格/图片/图表/代码的 caption）")
    footnotes: list[str] = Field(default=[], description="脚注（表格脚注等）")
    sub_type: str | None = Field(default=None, description="供应商子类型（code/algorithm、markdown 等），仅透传")
    asset_name: str | None = Field(default=None, description="关联图片资产名（assets 字典的 key）")


class ParsedDocument(BaseModel):
    """一份文档的完整解析结果。"""

    blocks: list[ParsedBlock] = Field(description="按阅读顺序平铺的内容块")
    assets: dict[str, bytes] = Field(default={}, description="图片资产（名 → 字节），含表格截图/插图")
    md_content: str | None = Field(default=None, description="全文 Markdown（供应商提供时用于预览）")
