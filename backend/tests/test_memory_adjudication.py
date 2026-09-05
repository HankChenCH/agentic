"""裁决应用：ADD/REPLACE 落库语义、程序化降级规则、MANUAL 不可取代保护。"""

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from app.components.memory import MemoryConsolidationService
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.models.domain.memory import MemoryOrigin, MemoryStatement, StatementState

from fakes_memory import CannedLLM, FakeMemoryVectorIndex, FakeModelFactory, make_service_config
from conftest import TEST_USER_ID


def _service(engine, llm) -> MemoryConsolidationService:
    return MemoryConsolidationService(
        memory_repo=SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID),
        vector_index=FakeMemoryVectorIndex(),
        model_factory=FakeModelFactory(llm),
        app_config=make_service_config(),
    )


def _msg():
    return SimpleNamespace(message_type="MESSAGE", content=[{"type": "text", "text": "收到"}])


def _fact_response(object_text: str, evidence: str = "我是做后端的") -> str:
    fact = {"subject_key": "user", "predicate": "职业",
            "object_text": object_text, "confidence": 0.8, "evidence": evidence}
    return '{"entities": [], "episodes": [], "facts": [' + json.dumps(fact, ensure_ascii=False) + ']}'


def test_remember_adds_statement_with_evidence_and_vector_entry(engine):
    llm = CannedLLM(_fact_response("后端开发"), '{"decisions": [{"index": 0, "action": "ADD"}]}')
    svc = _service(engine, llm)
    turn_id = uuid4()

    created = svc.remember(query="我在上海做后端开发", turn_messages=[_msg()], user_id=TEST_USER_ID,
                           thread_id=uuid4(), turn_id=turn_id)

    statements = svc.memory_repo.list_active_statements()
    assert len(created) == 1 and len(statements) == 1
    row = statements[0]
    assert row.summary == "用户的职业是后端开发"
    assert row.evidence[0]["quote"] == "我是做后端的"
    assert row.evidence[0]["source_turn_id"] == str(turn_id)
    # 两次 LLM 调用（抽取+裁决）；向量端只收到 statement 嵌入（本轮无候选实体，
    # 恒存的“用户”节点仅在消歧触达时才重嵌）
    assert len(llm.calls) == 2
    assert {entry.kind for entry in svc.vector_index.upserts} == {"statement"}


def test_single_value_predicate_replaces_old_via_llm_decision(engine):
    seed_llm = CannedLLM(_fact_response("教师"), '{"decisions": [{"index":0,"action":"ADD"}]}')
    seed_svc = _service(engine, seed_llm)
    seed_svc.remember("我是教师", [_msg()], TEST_USER_ID, uuid4(), uuid4())
    old_row = seed_svc.memory_repo.list_active_statements()[0]

    llm = CannedLLM(_fact_response("后端开发"),
                    json.dumps({"decisions": [{"index": 0, "action": "REPLACE",
                                               "replace_id": old_row.id}]},
                               ensure_ascii=False))
    created = _service(engine, llm).remember("我改行做后端了", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    active = created[0].__class__ and [r for r in
        SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID).list_active_statements()]
    assert len(active) == 1 and active[0].object_text == "后端开发" and active[0].id != old_row.id

    reloaded_old = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID).statements_by_ids([old_row.id])[0]
    assert reloaded_old.state == StatementState.SUPERSEDED.value
    assert reloaded_old.invalidated_at is not None and reloaded_old.valid_to is not None


def test_manual_origin_cannot_be_superseded(engine):
    svc = _service(engine, CannedLLM('{"entities":[],"episodes":[],"facts":[]}', ""))
    manual = svc.memory_repo.insert_statement(MemoryStatement(
        subject_id=svc._ensure_user_entity(svc.memory_repo.for_user(TEST_USER_ID)).id,
        predicate="职业", object_text="管理层钦定", summary="人工修正的职业",
        state=StatementState.ACTIVE.value, origin=MemoryOrigin.MANUAL.value,
        valid_from=datetime(2026, 1, 1),
    ))

    conflict_llm = CannedLLM(_fact_response("程序员"),
                             json.dumps({"decisions": [{"index": 0, "action": "REPLACE",
                                                        "replace_id": manual.id}]}))
    result = _service(engine, conflict_llm).remember("我现在是程序员", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    # 冲突事实整体放弃：人工事实不可被 LLM 推翻（设计 §4 MANUAL 保护）
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    assert result == []
    reloaded = repo.statements_by_ids([manual.id])[0]
    assert reloaded.state == StatementState.ACTIVE.value and reloaded.invalidated_at is None
    assert repo.list_active_statements()[0].object_text == "管理层钦定"


def test_programmatic_fallback_when_adjudication_fails(engine):
    seed_llm = CannedLLM(_fact_response("教师"), '{"decisions": [{"index":0,"action":"ADD"}]}')
    seed_svc = _service(engine, seed_llm)
    seed_svc.remember("我是教师", [_msg()], TEST_USER_ID, uuid4(), uuid4())
    old_id = seed_svc.memory_repo.list_active_statements()[0].id

    # 裁决应答非 JSON → 程序化降级：单值谓词同主体自动 REPLACE 最旧行的新值
    fallback_llm = CannedLLM(_fact_response("测试工程师"), "裁决器今天不在状态")
    done = _service(engine, fallback_llm).remember("转岗成测试工程师了", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    states = {row.id: row.state for row in
              SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID).statements_by_ids([old_id])}
    assert states[old_id] == StatementState.SUPERSEDED.value
    assert any(row.object_text == "测试工程师" for row in done)
