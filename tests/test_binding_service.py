"""绑定管理服务：全量替换语义与校验（4007/4001/4003）。"""

from uuid import uuid4

import pytest
from sqlmodel import Session

import app.agents.factory  # noqa: F401 触发内置 agent 注册（builtin:demo）
from app.agents.catalog import RegistryAgentCatalog
from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeAgentInvalidError,
    KnowledgeNotFoundError,
    KnowledgeStatusError,
)
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_binding_repository import KnowledgeBindingRepository
from app.services.domain.knowledge.binding_service import KnowledgeBindingService


def make_kb(engine, name, status=KnowledgeStatus.ENABLED) -> uuid4:
    with Session(engine) as session:
        kb = KnowledgeBase(name=name, embedding_model="ollama-embedding", status=status)
        session.add(kb)
        session.commit()
        session.refresh(kb)
        return kb.id


@pytest.fixture()
def service(engine):
    return KnowledgeBindingService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        binding_repo=KnowledgeBindingRepository(engine=engine),
        agent_catalog=RegistryAgentCatalog(),
        logger_factory=LoggerFactory(),
    )


def test_replace_and_list_roundtrip(engine, service):
    kb1 = make_kb(engine, "kb1")
    kb2 = make_kb(engine, "kb2")
    # 重复 id 入参去重保序
    service.replace_bindings("builtin:demo", [kb1, kb2, kb1])
    assert [kb.name for kb in service.list_bindings("builtin:demo")] == ["kb1", "kb2"]

    # 全量替换：最终集合只剩 kb1
    service.replace_bindings("builtin:demo", [kb1])
    assert [kb.name for kb in service.list_bindings("builtin:demo")] == ["kb1"]

    # 空列表 = 清空绑定
    service.replace_bindings("builtin:demo", [])
    assert service.list_bindings("builtin:demo") == []


def test_unknown_agent_rejected(service):
    with pytest.raises(KnowledgeAgentInvalidError):
        service.replace_bindings("builtin:ghost", [])


def test_unknown_kb_rejected(service):
    with pytest.raises(KnowledgeNotFoundError):
        service.replace_bindings("builtin:demo", [uuid4()])


def test_deleting_kb_rejected(engine, service):
    kb = make_kb(engine, "dying", status=KnowledgeStatus.DELETING)
    with pytest.raises(KnowledgeStatusError):
        service.replace_bindings("builtin:demo", [kb])
