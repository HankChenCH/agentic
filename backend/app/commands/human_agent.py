"""human_agent 域管理命令：人工客服坐席的增删改查（运营维护入口）。

坐席是客服智能体「转人工」场景的数据源（components/human_agent 查库）。
命令体只做 CLI 接线与展示，业务在 application 坐席管理用例
（HumanAgentAppService，经容器注入仓储端口依赖）。

用法：``python -m app.cmd.admin human-agent <command> [...]``
"""

from typing import Annotated, Optional
from uuid import UUID

import typer

from app.application import HumanAgentAppService
from app.core.container import build_sync_container
from app.models.domain.human_agent import HumanAgentStatus

app = typer.Typer(no_args_is_help=True, help="人工客服坐席管理")


def _service() -> HumanAgentAppService:
    return build_sync_container().get(HumanAgentAppService)


@app.command("add")
def add(
    name: Annotated[str, typer.Option("--name", help="坐席姓名（全局唯一）")],
    title: Annotated[str, typer.Option("--title", help="职务，如「高级客服」")],
    specialty: Annotated[str, typer.Option("--specialty", help="擅长问题类型")] = "",
    intro: Annotated[str, typer.Option("--intro", help="坐席简介")] = "",
    status: Annotated[HumanAgentStatus, typer.Option("--status", help="接待状态")] = HumanAgentStatus.OFFLINE,
) -> None:
    """新增坐席。"""
    agent = _service().create_agent(
        name=name, title=title, specialty=specialty, intro=intro, status=status,
    )
    typer.echo(f"已新增坐席 {agent.id}：{agent.name}（{agent.title}，{agent.status.value}）")


@app.command("list")
def list_agents() -> None:
    """列出全部坐席（在线优先展示）。"""
    agents = _service().list_agents()
    if not agents:
        typer.echo("暂无坐席，先用 human-agent add 新增")
        return
    typer.echo(f"共 {len(agents)} 名坐席：")
    for agent in agents:
        line = f"- {agent.id} {agent.name}（{agent.title}，{agent.status.value}）"
        if agent.specialty:
            line += f" 擅长：{agent.specialty}"
        typer.echo(line)


@app.command("update")
def update(
    agent_id: Annotated[UUID, typer.Argument(help="坐席 id（human-agent list 查看）")],
    name: Annotated[Optional[str], typer.Option("--name", help="改为新姓名")] = None,
    title: Annotated[Optional[str], typer.Option("--title", help="改为新职务")] = None,
    specialty: Annotated[Optional[str], typer.Option("--specialty", help="改为新擅长描述")] = None,
    intro: Annotated[Optional[str], typer.Option("--intro", help="改为新简介")] = None,
    status: Annotated[Optional[HumanAgentStatus], typer.Option("--status", help="改为新状态")] = None,
) -> None:
    """更新坐席属性（仅显式传入的字段会被修改）。"""
    agent = _service().update_agent(
        agent_id, name=name, title=title, specialty=specialty, intro=intro, status=status,
    )
    if agent is None:
        typer.echo(f"坐席不存在：{agent_id}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"已更新坐席 {agent.id}：{agent.name}（{agent.title}，{agent.status.value}）")


@app.command("remove")
def remove(
    agent_id: Annotated[UUID, typer.Argument(help="坐席 id（human-agent list 查看）")],
) -> None:
    """删除坐席。"""
    if _service().delete_agent(agent_id):
        typer.echo(f"已删除坐席 {agent_id}")
    else:
        typer.echo(f"坐席不存在：{agent_id}", err=True)
        raise typer.Exit(code=1)
