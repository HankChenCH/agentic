"""账号信息 ↔ 记忆「用户」节点联动：UserNodeSyncService 行为契约。

纯单测：真实 SqliteGraphMemoryRepository + 临时 SQLite（conftest 的 engine
fixture），覆盖注册面（首次创建/幂等刷新）与 attributes 回落口径。
"""

from uuid import uuid4

from conftest import TEST_USER_ID
from app.components.memory.ability.consolidation import USER_ENTITY_NAME
from app.components.memory.ability.user_node import ATTR_NICKNAME, ATTR_USERNAME, UserNodeSyncService
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository


def make_svc(engine) -> UserNodeSyncService:
    return UserNodeSyncService(memory_repo=SqliteGraphMemoryRepository(engine=engine))


def test_creates_user_node_with_account_attributes(engine):
    svc = make_svc(engine)
    node = svc.sync_user_node(TEST_USER_ID, "alice", "小爱")

    assert node.name == USER_ENTITY_NAME and node.is_user is True
    assert node.attributes == {ATTR_USERNAME: "alice", ATTR_NICKNAME: "小爱"}


def test_sync_is_scoped_per_user(engine):
    svc = make_svc(engine)
    other = uuid4()
    svc.sync_user_node(TEST_USER_ID, "alice", "")
    node = svc.sync_user_node(other, "bob", "阿宝")

    assert node.attributes[ATTR_USERNAME] == "bob"
    # 作用域隔离：alice 的节点对 bob 不可见
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(other)
    assert repo.find_entity_by_name(USER_ENTITY_NAME).attributes[ATTR_USERNAME] == "bob"


def test_existing_node_attributes_updated_only_on_change(engine):
    svc = make_svc(engine)
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)

    first = svc.sync_user_node(TEST_USER_ID, "alice", "")
    assert first.attributes[ATTR_NICKNAME] == "alice"        # 空昵称回落 username
    assert repo.find_entity_by_name(USER_ENTITY_NAME).id == first.id

    # 无变化：不产生新行
    again = svc.sync_user_node(TEST_USER_ID, "alice", "")
    assert again.id == first.id

    # 资料变更：原行 attributes 原位刷新（非重建）
    renamed = svc.sync_user_node(TEST_USER_ID, "alice", "小爱")
    assert renamed.id == first.id
    assert renamed.attributes == {ATTR_USERNAME: "alice", ATTR_NICKNAME: "小爱"}


def test_existing_attributes_beyond_account_are_preserved(engine):
    svc = make_svc(engine)
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    node = svc.sync_user_node(TEST_USER_ID, "alice", "小爱")
    node.attributes = {**node.attributes, "merge_blocklist": ["张三"]}
    repo.upsert_entity(node)

    refreshed = svc.sync_user_node(TEST_USER_ID, "alice", "小爱")

    assert refreshed.attributes["merge_blocklist"] == ["张三"]
