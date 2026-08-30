"""分层边界守卫：AST 扫描 app/** 的 import，断言跨层禁边不被违反。

规则与 server/AGENTS.md「服务层两层制」的依赖箭头表一致（domain 单向
向下、组件不向上、消费方只触碰编排/领域两个门面）。规则改动须两处同步。
"""

import ast
import pathlib

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

# 文件所在包（origin）→ 禁止 import 的目标包前缀（dotted）
FORBIDDEN_EDGES = {
    "app.packages": [
        "app.services",  # 能力库层领域无关，禁向上依赖（含 orchestration/domain）
        "app.components",
        "app.agents",
        "app.api",
        "app.tasks",
        "app.repositories",
        "app.models",
    ],
    "app.services.domain": [
        "app.services.orchestration",  # 领域不得向上依赖编排
        "app.components",
        "app.agents",
        "app.api",
        "app.tasks",
    ],
    "app.services.orchestration": [
        "app.api",
        "app.tasks",
        "app.repositories",  # 编排禁止直触持久化，一律经领域服务
    ],
    "app.components": [
        "app.services.orchestration",
        "app.agents",
        "app.api",
        "app.tasks",
    ],
    "app.agents": ["app.api", "app.tasks"],
    # 消费方：只允许触碰 orchestration / domain 两个门面（或 app.services 根再导出）
    "app.api": ["app.repositories", "app.infrastructures", "app.components", "app.agents"],
    "app.tasks": [
        "app.repositories",
        "app.infrastructures",
        "app.components",
        "app.agents",
        "app.api",
        "app.services.orchestration",  # 后台任务只驱动领域服务
    ],
}

# origin 前缀按最长匹配；命中即取其禁边表，未命中（core/models/repositories/
# infrastructures/app.cmd 等下层）不做限制——packages 已单列禁边（库层禁向上）。
# services 根 __init__ 是聚合导出面，豁免检查（orchestration/domain 各自内部
# 导入属同层，天然不会命中禁边）。
SKIP_FILES = {APP_DIR / "services" / "__init__.py"}


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
    base = ".".join(["app", *parts])
    return [f"{base}.{node.module}" if node.module else base]


@pytest.mark.parametrize("module", sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts))
def test_no_forbidden_cross_layer_imports(module):
    if module in SKIP_FILES:
        pytest.skip("services 根是再导出聚合面")
    origin = _origin_of(module)
    if origin is None:
        pytest.skip("下层模块不设禁边")

    tree = ast.parse(module.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target in _resolve(module, node):
            if any(target == f or target.startswith(f + ".") for f in FORBIDDEN_EDGES[origin]):
                offenders.append(f"{origin} -> {target} ({module.relative_to(APP_DIR.parent)})")
    assert offenders == [], "违反分层禁边：\n" + "\n".join(offenders)
