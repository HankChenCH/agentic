"""检索工具：溯源结构化 JSON、空结果与非法 kb_ids 容错、config 身份透传。"""

import json
from types import SimpleNamespace
from uuid import uuid4

from app.components.knowledge import KnowledgeComponent
from app.components.knowledge.ability.retrieval import RetrievalHit


class StubRetrieval:
    def __init__(self, kbs=None, result=([], [])):
        self.kbs = kbs or []
        self.result = result
        self.captured = None

    def list_visible_knowledge(self, user_id):
        self.captured = user_id
        return self.kbs

    def search_for_user(self, user_id, query, kb_ids=None, top_k=4):
        self.captured = (user_id, query, kb_ids, top_k)
        return self.result


def tools_from(stub):
    tools = KnowledgeComponent(retrieval=stub).tools("builtin:demo")
    return {tool.name: tool for tool in tools}


def test_knowledge_search_returns_structured_sources():
    hit = RetrievalHit(
        content="安装前请确认系统版本。",
        score=0.834,
        kb_id="k",
        doc_id="d",
        doc_name="产品手册",
        position=1,
        meta={
            "page_start": 3,
            "page_end": 5,
            "heading_path": ["安装", "Linux"],
            "types": ["text"],
            "bboxes": [{"page": 3, "bbox": [0.1, 0.2, 0.8, 0.3]}, {"page": 4, "bbox": [0.05, 0.4, 0.9, 0.5]}],
        },
    )
    stub = StubRetrieval(result=([hit], ["知识库「旧库」嵌入模型与当前配置不一致，已跳过"]))
    payload = json.loads(tools_from(stub)["knowledge_search"].invoke({"query": "如何安装"}))

    source = payload["sources"][0]
    assert source["index"] == 1
    assert source["doc_name"] == "产品手册"
    assert source["page_start"] == 3 and source["page_end"] == 5
    assert source["heading_path"] == ["安装", "Linux"]
    assert source["score"] == 0.83  # 相关度保留两位
    assert source["content"] == "安装前请确认系统版本。"
    # bbox 紧凑元组：[page, x0, y0, x1, y1]
    assert source["bboxes"] == [[3, 0.1, 0.2, 0.8, 0.3], [4, 0.05, 0.4, 0.9, 0.5]]
    assert payload["notes"] == ["知识库「旧库」嵌入模型与当前配置不一致，已跳过"]


def test_knowledge_search_source_without_bboxes_omits_field():
    # 存量文档 meta 无 bboxes：来源省略该字段（前端降级为页码跳转）
    hit = RetrievalHit(
        content="片段",
        score=0.5,
        kb_id="k",
        doc_id="d",
        doc_name="旧文档",
        position=0,
        meta={"page_start": 1, "page_end": 1},
    )
    payload = json.loads(tools_from(StubRetrieval(result=([hit], [])))["knowledge_search"].invoke({"query": "问题"}))
    assert "bboxes" not in payload["sources"][0]


def test_knowledge_search_empty_result_and_notes():
    stub = StubRetrieval(result=([], ["知识库「A」未启用（ready），已跳过"]))
    output = tools_from(stub)["knowledge_search"].invoke({"query": "问题"})
    assert "未检索到相关内容" in output
    assert "未启用" in output


def test_knowledge_list_output():
    kb = SimpleNamespace(
        name="产品手册", id=uuid4(), doc_num=3, status=SimpleNamespace(value="enabled"),
        is_public=True, description="安装与配置",
    )
    output = tools_from(StubRetrieval(kbs=[kb]))["knowledge_list"].invoke({})
    assert "产品手册" in output and "3 篇文档" in output and "enabled" in output
    assert "公开" in output
    assert "安装与配置" in output


def test_invalid_kb_ids_dropped_not_fatal():
    stub = StubRetrieval()
    valid = uuid4()
    user_id = uuid4()
    tools_from(stub)["knowledge_search"].invoke(
        {"query": "问题", "kb_ids": [str(valid), "not-a-uuid"]},
        config={"configurable": {"user_id": str(user_id)}},
    )
    captured_user_id, _query, kb_ids, _top_k = stub.captured
    assert captured_user_id == user_id  # 可运行 config 注入的 user_id（字符串 → UUID）
    assert kb_ids == [valid]  # 非法项剔除，合法项保留


def test_none_kb_ids_passes_through():
    stub = StubRetrieval()
    tools_from(stub)["knowledge_search"].invoke({"query": "问题", "kb_ids": None})
    assert stub.captured[0] is None  # 直调无 config → 无身份
    assert stub.captured[2] is None  # 缺省 = 全部可用库
