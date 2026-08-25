from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, SecretStr, model_validator


class LocalFilesystemEntry(BaseModel):
    """本地磁盘存储 entry。

    ``root`` 为相对工作目录的根路径（默认 ``data/files``，已在 .gitignore），
    key 映射为 root 下的相对路径；根目录由构建器提前创建。
    """

    type: Literal["local"] = Field(default="local", description="文件存储类型标识")
    root: str = Field(
        default="data/files",
        description="本地存储根目录（相对工作目录）",
    )


class S3FilesystemEntry(BaseModel):
    """S3 / 兼容对象存储 entry（rustfs、MinIO、R2 等，底层经 obstore 接入）。

    ``endpoint`` 为 S3 兼容部署的直连地址（rustfs 本地开发为
    ``http://127.0.0.1:9000``）；AWS 官方部署留空走默认寻址。
    本地 HTTP endpoint 需 ``allow_http: true`` 且
    ``virtual_hosted_style_request: false``（path-style 寻址）。
    """

    type: Literal["s3"] = Field(default="s3", description="文件存储类型标识")
    bucket: str = Field(description="bucket 名（需预先存在，本地栈由 compose 的 rustfs-init 幂等创建）")
    endpoint: str | None = Field(
        default=None,
        description="S3 兼容服务地址，eg: http://127.0.0.1:9000；AWS 官方部署留空",
    )
    access_key_id: str | None = Field(default=None, description="访问密钥 ID；环境凭证部署可留空")
    secret_access_key: SecretStr | None = Field(default=None, description="访问密钥 Secret；环境凭证部署可留空")
    region: str | None = Field(default=None, description="region；S3 兼容服务通常可不填")
    virtual_hosted_style_request: bool | None = Field(
        default=None,
        description="是否 virtual-hosted-style 寻址；本地 endpoint 需显式 false",
    )
    allow_http: bool = Field(
        default=False,
        description="允许明文 HTTP（本地未加 TLS 的 S3 兼容服务需要）",
    )


# 按 type 判别的 Union；未来新增存储供应商（如 oss/gcs）在此扩展
FilesystemProviderEntry = Annotated[
    Union[LocalFilesystemEntry, S3FilesystemEntry],
    Field(discriminator="type"),
]


class FilesystemConfig(BaseModel):
    """文件存储配置：default 引用 providers 里的一个 entry key。"""

    default: str = Field(default="rustfs", description="默认 entry key")
    providers: dict[str, FilesystemProviderEntry] = Field(description="具名文件存储实例表")

    @model_validator(mode="after")
    def _default_must_exist(self):
        if self.default not in self.providers:
            raise ValueError(f"default provider '{self.default}' not in providers: {list(self.providers)}")
        return self
