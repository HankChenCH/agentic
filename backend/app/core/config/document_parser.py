from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, SecretStr, model_validator


class MineruCloudEntry(BaseModel):
    """MinerU 云端 Open API entry（mineru.net，v4）。

    四步协议：申请预签名上传 URL → PUT 上传 → 轮询 batch 结果 → 下载
    结果 zip。``api_key`` 为控制台申请的 Bearer token；文档数据会出公网，
    建议生产环境改用自部署 ``mineru_api`` entry（协议见
    ``mineru_api_provider``，接入时新增 entry type 即可）。
    """

    type: Literal["mineru_cloud"] = Field(default="mineru_cloud", description="文档解析供应商类型标识")
    api_url: str = Field(
        default="https://mineru.net",
        description="云端 API 根地址",
    )
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="API Bearer token（控制台申请），经 ${MINERU_API_KEY} 注入",
    )
    language: str = Field(
        default="ch",
        description="文档语言提示（ch / en / mixed 等，云端 API 参数）",
    )
    enable_formula: bool = Field(default=True, description="是否解析公式（返回 LaTeX）")
    enable_table: bool = Field(default=True, description="是否解析表格（返回 HTML 表体）")
    is_ocr: bool = Field(default=True, description="扫描件 OCR 开关（云端 API files 参数）")
    timeout: float = Field(
        default=60.0,
        description="单次 HTTP 请求超时（秒），不含轮询等待",
    )
    poll_interval: float = Field(
        default=5.0,
        description="解析结果轮询间隔（秒）",
    )
    poll_timeout: float = Field(
        default=1800.0,
        description="解析结果轮询总超时（秒），超时视为失败",
    )


# 本地 Office 文档解析 entry：跑在进程内的轻量库解析（openpyxl / python-docx，
# 经 LangChain loader 范式组装），无需外部服务与密钥；路由表（routing）把
# 文件后缀指到具体 entry。
class LocalXlsxEntry(BaseModel):
    """xlsx 本地解析 entry：每个工作表一个标题块 + 表格按行窗口切块。

    表格行数超 ``rows_per_block`` 时按行窗口切成多个 TABLE 块（表头每块
    重复），保证单块体量可嵌入；超 ``max_rows_per_sheet`` / ``max_cols``
    截断并注记。合并单元格按锚点值渲染（read_only 流式读取不带合并信息）。
    """

    type: Literal["local_xlsx"] = Field(default="local_xlsx", description="文档解析供应商类型标识")
    rows_per_block: int = Field(default=20, ge=1, description="单个 TABLE 块携带的数据行窗口大小（不含表头）")
    max_rows_per_sheet: int = Field(default=1000, ge=1, description="单表最大读取数据行数，超出截断")
    max_cols: int = Field(default=64, ge=1, description="单表最大读取列数，超出截断")


class LocalDocxEntry(BaseModel):
    """docx 本地解析 entry：按阅读序产出标题/正文/列表/表格块。"""

    type: Literal["local_docx"] = Field(default="local_docx", description="文档解析供应商类型标识")


class LocalMarkdownEntry(BaseModel):
    """markdown 本地解析 entry：行级块解析（标题/列表/代码/管道表），零参数。"""

    type: Literal["local_markdown"] = Field(default="local_markdown", description="文档解析供应商类型标识")


# 按 type 判别的 Union；未来新增供应商（如自部署 mineru-api）在此扩展
DocumentParserProviderEntry = Annotated[
    Union[MineruCloudEntry, LocalXlsxEntry, LocalDocxEntry, LocalMarkdownEntry],
    Field(discriminator="type"),
]


class DocumentParserConfig(BaseModel):
    """文档解析配置：default 引用 providers 里的一个 entry key。

    ``routing`` 是文件后缀 → 解析器链的路由表（以文件类型为 key 选择解析
    器的机制面）：值为 entry key 或有序 entry key 列表（回退链——前序解析
    器永久性失败/产出为空时依次升级，见 FileTypeRoutingParser）；未命中
    回退 default entry。key 归一为小写带点后缀（``PDF``/``pdf`` 均入
    ``.pdf``），链内每个 key 必须引用 providers 里存在的 entry——装配期
    fail-fast。
    """

    default: str = Field(default="mineru-cloud", description="默认 entry key")
    providers: dict[str, DocumentParserProviderEntry] = Field(description="具名文档解析实例表")
    routing: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description="文件后缀 → entry key（或有序回退链）路由表，如 .xlsx: [local-xlsx, mineru-cloud]",
    )

    @model_validator(mode="after")
    def _validate(self):
        if self.default not in self.providers:
            raise ValueError(f"default provider '{self.default}' not in providers: {list(self.providers)}")
        normalized: dict[str, list[str]] = {}
        for raw_suffix, raw_chain in self.routing.items():
            suffix = raw_suffix.strip().lower()
            if not suffix.startswith("."):
                suffix = f".{suffix}"
            # str 视为单元素链；归一后统一为 list，消费方无需再分支
            chain = [raw_chain] if isinstance(raw_chain, str) else list(raw_chain)
            if not chain:
                raise ValueError(f"routing '{raw_suffix}' has an empty parser chain")
            for key in chain:
                if not self.providers.get(key):
                    raise ValueError(
                        f"routing '{raw_suffix}' references unknown provider '{key}': {list(self.providers)}"
                    )
            if suffix in normalized:
                raise ValueError(f"duplicate routing entry for suffix '{suffix}'")
            normalized[suffix] = chain
        self.routing = normalized
        return self
