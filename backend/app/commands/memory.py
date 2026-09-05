"""memory 域维护命令：实体错合并存量修复 + 记忆向量索引全量重建。

「广州市卫生职业技术学院」被错并入禹通公司档案的事故（2026-08-28）：实体
消歧曾把 Weaviate hybrid 融合分当余弦阈值用，statement / episode_link 随之
错挂，别名还吸入了「学校名」「#S13/#S14」等污染项；代码侧修复见
domain/memory/vocab.py（溯源 token 拦截规则）与
components/memory/internal/resolution.py（两段式消歧）。

命令体只做 CLI 接线与展示；事故定位常量与修复/重建编排归 application
维护用例（MemoryAppService，全局算子视角，向量按行归属分组回写）。
本模块零白盒 import、零直接 SQL。

用法：``python -m app.cmd.admin memory repair [--apply]`` /
``memory rebuild-index [--yes]``
"""

from typing import Annotated

import typer

from app.application import MemoryAppService
from app.core.container import build_sync_container

app = typer.Typer(no_args_is_help=True, help="记忆域维护")


def _service() -> MemoryAppService:
    """命令体内惰性建容器：顶层 --config-dir/--env-file 已先桥接环境变量。"""
    return build_sync_container().get(MemoryAppService)


def _detail(plan) -> str:
    return "、".join(f"{kind} {n}" for kind, n in plan.counts.items())


@app.command("repair")
def repair_cmd(
    apply: Annotated[
        bool, typer.Option("--apply", help="真实写入并重写触达对象向量；缺省 dry-run")
    ] = False,
) -> None:
    """修复实体错合并的存量数据（幂等，可安全重跑）。"""
    report = _service().repair(apply=apply)
    typer.echo(f"== 记忆实体修复（{'APPLY' if apply else 'DRY-RUN'}）==")
    for line in report.lines:
        typer.echo(line)


@app.command("rebuild-index")
def rebuild_index_cmd(
    yes: Annotated[
        bool, typer.Option("--yes", help="真执行 drop+全量重嵌；缺省只打印条数统计")
    ] = False,
) -> None:
    """全量重建记忆向量索引（换 embedding 模型后必跑；SQL 为事实源）。"""
    service = _service()
    if not yes:
        plan = service.collect_index_entries()
        typer.echo("== 记忆向量索引重建（DRY-RUN）==")
        typer.echo(f"将按用户 drop 并重嵌 {plan.total} 条、{len(plan.grouped)} 个用户 collection（{_detail(plan)}）")
        typer.echo("确认后加 --yes 执行")
        return
    plan = service.rebuild_index()
    typer.echo(f"== 记忆向量索引重建完成：{plan.total} 条、{len(plan.grouped)} 个用户 collection（{_detail(plan)}）==")
