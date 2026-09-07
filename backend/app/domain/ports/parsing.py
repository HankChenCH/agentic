"""文档解析契约与归一化结果模型：隔离供应商原始输出的格式变化。

契约（``DocumentParser``）与数据形状（``ParsedBlock``/``ParsedDocument``）
同住 domain——分块器与入库流水线只依赖本模块；供应商实现（MinerU 云端等）
住 ``app/adapters/document_parser/``，负责把原始输出（如 content_list.json
的平铺块数组）翻译成这里的模型，格式差异（content_list 版本、bbox 坐标系）
不出 provider 边界。MinerU 升级或更换解析供应商时上层不动。
"""

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field

from app.core.exceptions import BusinessError


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


class DocumentParseError(BusinessError):
    """文档解析的永久性失败：文件损坏、字节与后缀不符、非目标格式等。

    归入业务错误（而非 InfrastructureError）是刻意的：换一份文件重试也一样
    失败，Celery 的 autoretry 不该烧在确定性失败上（dont_autoretry_for=
    (BusinessError,)），文档落 failed + error_message 供人工修复后重试。
    机制性故障（网络/对象存储/云端 API）仍由供应商实现抛 InfrastructureError。
    """


class DocumentParser(ABC):
    """统一文档解析契约：原始文件字节 → 归一化结构（blocks + assets + md）。

    接口为同步阻塞——调用方是 Celery 后台任务（见 ``app/tasks/knowledge.py``），
    解析耗时（云端排队/轮询）天然属于任务时长。
    """

    @abstractmethod
    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        """解析文档字节，filename 用于供应商侧的格式识别与结果文件定位。"""
