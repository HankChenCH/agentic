"""摄取任务派发实现：``IngestionDispatcher`` 端口的 Celery 回填。

任务对象经方法内懒 import——本包被 tasks 层与 HTTP 进程共用，模块期导入
``app.tasks`` 会形成 tasks ↔ adapters 导入环；运行期（容器就绪后）导入无环。
"""

from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.domain.knowledge.ports import IngestionDispatcher


@injectable(as_type=IngestionDispatcher)
@dataclass
class CeleryIngestionDispatcher(IngestionDispatcher):
    """经 Celery 补发 ``agentic.knowledge.process_document`` 任务。"""

    def dispatch_processing(self, kb_id: UUID, doc_id: UUID) -> None:
        from app.tasks.knowledge import process_document  # noqa: PLC0415 懒 import 防 tasks↔adapters 环

        process_document.delay(str(kb_id), str(doc_id))
