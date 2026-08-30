"""组件结构范式守卫：扫描 app/components 的解剖学合规性。

范式（总纲见 app/components/__init__.py docstring）：
- 每个组件包必有 manifest.py（能力声明）与 ability/（能力模块）；
- 禁止 service.py / utils.py 等泛化命名（组件根与 ability/ 下）；
- internal/ 是组件私有机件：app 层（orchestration/agents/api/tasks/domain/
  models/repositories/infrastructures）禁止 import，白盒消费方仅限
  tests/ 与维护 CLI（app/commands/）。

规则改动须与 server/AGENTS.md 的组件小节同步。
"""

import ast
import pathlib

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"
COMPONENTS_DIR = APP_DIR / "components"

# 结构范式明令禁止的泛化命名（能力/职责必须落在文件名上）
GENERIC_MODULE_NAMES = {"service.py", "utils.py", "helpers.py", "common.py", "misc.py", "core.py"}

# internal/ 的白盒豁免面：组件自身 + 维护 CLI；app 其余层一律禁入
INTERNAL_ALLOWED_ORIGINS = ("app.commands",)


def _component_packages() -> list[pathlib.Path]:
    return sorted(
        d for d in COMPONENTS_DIR.iterdir()
        if d.is_dir() and (d / "__init__.py").exists() and "__pycache__" not in d.parts
    )


@pytest.mark.parametrize("package", _component_packages(), ids=lambda p: p.name)
def test_component_anatomy(package):
    """每个组件包的固定槽位：manifest.py 与 ability/ 必有。"""
    assert (package / "manifest.py").exists(), f"{package.name}: 缺 manifest.py（能力声明槽位）"
    ability = package / "ability"
    assert ability.is_dir() and (ability / "__init__.py").exists(), f"{package.name}: 缺 ability/（能力模块槽位）"


@pytest.mark.parametrize("module", sorted(COMPONENTS_DIR.rglob("*.py")), ids=lambda p: str(p.relative_to(COMPONENTS_DIR)))
def test_no_generic_module_names(module):
    """组件根与 ability/ 下禁止泛化命名：能力/职责必须落在文件名上。"""
    if module.name in GENERIC_MODULE_NAMES:
        base = module.parent.name
        assert base not in ("components", "ability"), f"泛化命名禁止（{module.relative_to(APP_DIR)}）：按能力/职责命名"


def test_internal_not_imported_from_app_layers():
    """app 层禁入组件 internal/；白盒消费方仅限 tests/ 与 app/commands/。"""
    offenders = []
    for module in APP_DIR.rglob("*.py"):
        if "__pycache__" in module.parts:
            continue
        dotted = ".".join(module.relative_to(APP_DIR.parent).with_suffix("").parts)
        if dotted.startswith(INTERNAL_ALLOWED_ORIGINS) or dotted.startswith("app.components"):
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets = [node.module]
            for target in targets:
                parts = target.split(".")
                if len(parts) >= 4 and parts[:2] == ["app", "components"] and "internal" in parts:
                    offenders.append(f"{dotted} -> {target}")
    assert offenders == [], "app 层不得 import 组件 internal/（白盒仅限 tests 与 app/commands）：\n" + "\n".join(offenders)
