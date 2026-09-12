"""知识摄取全链路 e2e：上传 → Celery 解析/分块/嵌入 → 向量落库 → RAG 溯源回答。

此前 e2e 只覆盖 KB CRUD 与无命中兜底（明确不上传文档），解析→分块→嵌入→
检索的段间拼装从未在真实链路验证。本文件补上：

- 用 ``.md`` 文档走 local_markdown 解析器（进程内轻量解析），**不依赖
  MinerU**（PDF 才按后缀路由到云端解析）；
- 前置：celery worker 在跑（``uv run celery -A app.adapters.tasking.celery_app
  worker``）+ Weaviate/Ollama 在线——摄取是异步任务，轮询文档状态至 ready；
- 断言正反配对：摄取后 RAG 回答必须带 [n] 引用角标（test_09 的无命中
  兜底必须无角标），锚定「引用角标 = 检索命中」的端到端契约。
"""

import re
import uuid

from helpers import (
    _client,
    _register,
    _run_sse,
    _text_of,
    _types,
    _wait_for,
)

# 文档内容刻意用低频词造事实：检索命中的语义锚点不依赖常见词
DOC_MARKDOWN = """# Agentic X-9 液冷石墨烯硬盘白皮书

Agentic X-9 是一台虚构的实验性存储设备。

## 核心参数

- 容量：192 TB（QLC 颗粒 + 石墨烯均热板）
- 冷却原理：热量经石墨烯层传导至液态金属微通道，再由外部泵循环带出
- 满载噪音：19 分贝（比图书馆翻书声更轻）
- 质保：十年换新（仅限月球背面发货订单）

## 冷却要点

液态金属微通道的循环流速为每秒 0.4 米，这一流速由固件 X9Cool v3 控制。
"""


def _create_kb(c, auth) -> str:
    kb_name = f"e2e摄取库{uuid.uuid4().hex[:6]}"
    created = c.post(
        "/knowledge", headers=auth,
        json={"name": kb_name, "description": "e2e 摄取全链路", "isPublic": False},
    )
    assert created.status_code == 200, created.text
    kb = created.json()["response"]
    return kb["id"] if isinstance(kb, dict) and "id" in kb else kb.get("kbId") or kb.get("kb_id")


def test_11_knowledge_ingestion_and_citations():
    """上传 .md → 轮询 ready → 分段可读 → RAG 带 [1] 引用 → 预览入口可用 → 清理。"""
    with _client() as c:
        user = _register(c, "ing")
        auth = {"Authorization": f"Bearer {user['token']}"}
        kb_id = _create_kb(c, auth)

        # 上传（multipart，走 local_markdown 解析器，不依赖 MinerU）
        upload = c.post(
            f"/knowledge/{kb_id}/document", headers=auth,
            files={"file": ("x9白皮书.md", DOC_MARKDOWN.encode("utf-8"), "text/markdown")},
            data={"description": "e2e 摄取样本"},
        )
        assert upload.status_code == 200, upload.text
        doc = upload.json()["response"]
        doc_id = doc.get("id") or doc.get("docId") or doc.get("documentId")
        assert doc_id, doc

        # 摄取是 Celery 异步任务：pending → processing → ready，轮询收敛
        def _ready() -> bool:
            detail = c.get(f"/knowledge/{kb_id}/document/{doc_id}", headers=auth).json()
            assert detail["error_code"] == 0, detail
            return detail["response"].get("status") in ("ready", "enabled")

        def _failed() -> bool:
            detail = c.get(f"/knowledge/{kb_id}/document/{doc_id}", headers=auth).json()
            return detail["response"].get("status") in ("failed", "reaped")

        assert _wait_for(_ready, timeout=180.0, interval=3.0), (
            "文档未在时限内就绪（可能 celery worker 未启动或解析失败）"
        )

        # 启用闸门是两级的：文档摄取完成只到 ready，检索只认
        # enabled 文档 + enabled 库——须先启用文档再启用库
        doc_enable = c.post(f"/knowledge/{kb_id}/document/{doc_id}/enable", headers=auth)
        assert doc_enable.status_code == 200 and doc_enable.json()["error_code"] == 0
        kb_enable = c.post(f"/knowledge/{kb_id}/enable", headers=auth)
        assert kb_enable.status_code == 200 and kb_enable.json()["error_code"] == 0

        # 分段落库可读
        segments = c.get(f"/knowledge/{kb_id}/document/{doc_id}/segment", headers=auth).json()
        assert segments["error_code"] == 0 and len(segments["response"]["items"]) >= 1

        # RAG 溯源回答：命中知识库 → 回答必须带 [n] 角标（与 test_09 无命中兜底正反配对）
        thread_id = uuid.uuid4().hex
        events = _run_sse(
            c, user["token"], thread_id,
            "Agentic X-9 硬盘的冷却原理是什么？请依据知识库回答并标注引用。",
            agent_id="builtin:rag",
        )
        types = _types(events)
        assert "RUN_FINISHED" in types, events
        assert "RUN_ERROR" not in types, events
        answer = _text_of(events)
        assert re.search(r"\[\d+\]", answer), f"检索命中场景回答未带引用角标: {answer}"
        assert "石墨烯" in answer or "液态金属" in answer, answer

        # 原始文件读取入口：预签名 302 或本地降级回源，两种后端形态都算可用
        file_resp = c.get(f"/knowledge/{kb_id}/document/{doc_id}/file", headers=auth)
        assert file_resp.status_code in (200, 302), file_resp.text

        # 清理：删库（三段式：行 + 对象存储 + drop collection）
        deleted = c.delete(f"/knowledge/{kb_id}", headers=auth)
        assert deleted.status_code == 200 and deleted.json()["error_code"] == 0
