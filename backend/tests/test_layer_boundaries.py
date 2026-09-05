"""分层边界守卫：AST 扫描 app/** 的 import，断言跨层禁边不被违反。

规则与 backend v2 六边形布局的依赖箭头表一致（v2 布局见 backend/README.md）：

    cmd ──► 全部（组装根 core/container 保留原位）
    api ──► {application | domain}        tasks ──► {domain, adapters.tasking}
    commands ──► domain
    application ──► {domain, components, agents}
    domain ──► models/domain              （唯一向下依赖；零 adapters/框架机制）
    components ──► {domain 端口, adapters 契约}
    agents ──► components
    adapters ──► {domain 端口（实现）, models, core/config}
    packages ──► {core, adapters}         （能力库层，禁向上）

另含供应商红线：domain / application 禁 import 供应商 SDK
（weaviate / redis / obstore / sqlalchemy / sqlmodel）——持久化与基础设施
机制只住在 adapters。规则改动须两处同步（本文件 + backend/README.md）。
"""

import ast
import pathlib


APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

# 文件所在包（origin）→ 禁止 import 的目标包前缀（dotted）
FORBIDDEN_EDGES = {
    "app.packages": [
        # 能力库层领域无关，禁向上依赖
        "app.domain",
        "app.application",
        "app.components",
        "app.agents",
        "app.api",
        "app.tasks",
        "app.models",
    ],
    "app.domain": [
        # 端口反转的题眼：领域零 adapters 依赖——消费的一律是本层 ports 声明的协议
        "app.application",
        "app.components",
        "app.agents",
        "app.api",
        "app.tasks",
        "app.adapters",
    ],
    "app.application": [
        "app.api",
        "app.tasks",
        "app.adapters.persistence",  # 编排禁止直触持久化，一律经领域服务/端口
    ],
    "app.components": [
        "app.application",
        "app.agents",
        "app.api",
        "app.tasks",
    ],
    "app.agents": ["app.api", "app.tasks"],
    # 驱动适配器：HTTP 只触碰 application/domain 两个门面（+ tasks 派发）
    "app.api": ["app.adapters", "app.components", "app.agents"],
    # 后台任务只驱动领域服务；任务机制（celery app）经 adapters.tasking 获取
    "app.tasks": [
        "app.adapters.persistence",
        "app.adapters.llm",
        "app.adapters.db",
        "app.adapters.redis",
        "app.adapters.vector",
        "app.adapters.filesystem",
        "app.adapters.document_parser",
        "app.components",
        "app.agents",
        "app.api",
        "app.application",
    ],
}

# 供应商 SDK 红线：这些顶层包只允许出现在 adapters / cmd / commands（及
# tests 白盒）——domain/application 的机制敏感面由端口协议覆盖
VENDOR_MODULES = {"weaviate", "redis", "obstore", "sqlalchemy", "sqlmodel"}
VENDOR_FORBIDDEN_ORIGINS = ("app.domain", "app.application")

# origin 前缀按最长匹配；命中即取其禁边表，未命中（core/models/adapters/
# exceptions/app.cmd 等下层）不做限制。
SKIP_FILES = {
    # 各聚合 __init__ 的再导出面豁免（同层内部导入天然不会命中禁边）
}


def _origin_of(module: pathlib.Path) -> str | None:
    dotted = ".".join(module.relative_to(APP_DIR.parent).with_suffix("").parts)
    candidates = [p for p in FORBIDDEN_EDGES if dotted == p or dotted.startswith(p + ".")]
    if not candidates:
        return None
    return max(candidates, key=len)


def _resolve(module: pathlib.Path, node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not node.level:
        return [node.module] if node.module else []
    # 相对导入：按当前包逐级上溯解析（level=1 即当前包）
    parts = list(module.relative_to(APP_DIR).with_suffix("").parts)
    if module.name != "__init__.py":
        parts = parts[:-1]
    for _ in range(node.level - 1):
        parts = parts[:-1]
    base = ".".join(parts)
    if not node.module:
        return [base] if base else []
    return [f"{base}.{node.module}" if base else node.module]


def _app_targets(dotted_names: list[str]) -> list[str]:
    return [name for name in dotted_names if name and (name == "app" or name.startswith("app."))]


def _violations() -> list[str]:
    problems: list[str] = []
    for module in sorted(APP_DIR.rglob("*.py")):
        origin = _origin_of(module)
        tree = ast.parse(module.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for dotted in _app_targets(_resolve(module, node)):
                if origin is not None:
                    for forbidden in FORBIDDEN_EDGES[origin]:
                        if dotted == forbidden or dotted.startswith(forbidden + "."):
                            problems.append(
                                f"{module.relative_to(APP_DIR.parent)}: {origin} "
                                f"不得 import {dotted}（禁边 {origin} ──► {forbidden}）"
                            )
                # 供应商红线（不限 origin 表内层级）
                if dotted.startswith(VENDOR_FORBIDDEN_ORIGINS):
                    continue
            if origin is not None and origin.startswith(VENDOR_FORBIDDEN_ORIGINS):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root in VENDOR_MODULES:
                            problems.append(
                                f"{module.relative_to(APP_DIR.parent)}: {origin} "
                                f"不得 import 供应商 SDK {root}（机制只住 adapters）"
                            )
                elif node.module:
                    root = node.module.split(".")[0]
                    if root in VENDOR_MODULES:
                        problems.append(
                            f"{module.relative_to(APP_DIR.parent)}: {origin} "
                            f"不得 import 供应商 SDK {root}（机制只住 adapters）"
                        )
    return problems


def test_layer_boundaries_hold():
    problems = _violations()
    assert not problems, "分层边界被违反：\n" + "\n".join(problems)


def test_boundary_table_covers_core_origins():
    """守卫表自身不得腐化：核心 origin 必须在禁边表内。"""
    for origin in (
        "app.domain",
        "app.application",
        "app.components",
        "app.agents",
        "app.api",
        "app.tasks",
        "app.packages",
    ):
        assert origin in FORBIDDEN_EDGES
