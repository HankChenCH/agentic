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


# 按 type 判别的 Union；未来新增供应商（如自部署 mineru-api）在此扩展
DocumentParserProviderEntry = Annotated[
    Union[MineruCloudEntry],
    Field(discriminator="type"),
]


class DocumentParserConfig(BaseModel):
    """文档解析配置：default 引用 providers 里的一个 entry key。"""

    default: str = Field(default="mineru-cloud", description="默认 entry key")
    providers: dict[str, DocumentParserProviderEntry] = Field(description="具名文档解析实例表")

    @model_validator(mode="after")
    def _default_must_exist(self):
        if self.default not in self.providers:
            raise ValueError(f"default provider '{self.default}' not in providers: {list(self.providers)}")
        return self
