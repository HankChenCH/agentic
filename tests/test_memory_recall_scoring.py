"""评分纯函数：遗忘半衰期、访问加成封顶、快路径归一。"""

from datetime import datetime, timezone

from app.components.memory.scoring import (
    ACCESS_BONUS_CAP,
    ScoreWeights,
    ScorableItem,
    access_bonus,
    recency_factor,
    score_item,
)

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_recency_halves_at_half_life():
    from datetime import timedelta

    fresh = recency_factor(NOW - timedelta(days=14), NOW, half_life_days=14)
    assert abs(fresh - 0.5) < 1e-9

    none_anchor = recency_factor(None, NOW, 14)
    assert none_anchor == 1.0  # 无时间锚视为刚活跃，不打压
    future_guard = recency_factor(NOW + timedelta(days=3), NOW, 14)
    assert future_guard == 1.0


def test_access_bonus_steps_and_caps():
    assert access_bonus(0) == 0.0
    assert access_bonus(1) > 0
    assert access_bonus(100) == ACCESS_BONUS_CAP


def test_score_item_full_weighting():
    weights = ScoreWeights(relevance=0.5, recency=0.2, importance=0.3)
    item = ScorableItem(
        relevance=1.0,
        last_active=datetime(2026, 8, 13, tzinfo=timezone.utc),  # 半衰点 → 0.5
        importance=0.5,
        access_count=1,
    )
    value = score_item(item, weights, 14, NOW)
    # γ·(0.5+加成) 与 β·0.5 的组合值：精确断言关键量级即可
    expected = 0.5 * 1.0 + 0.2 * 0.5 + 0.3 * min(0.5 + access_bonus(1), 1.0)
    assert abs(value - expected) < 1e-9


def test_score_item_fast_path_normalizes_without_relevance():
    weights = ScoreWeights(relevance=0.5, recency=0.2, importance=0.3)
    old_cold = ScorableItem(last_active=datetime(2020, 1, 1, tzinfo=timezone.utc), importance=0.1)
    hot_new = ScorableItem(last_active=NOW, importance=0.9)
    assert score_item(hot_new, weights, 14, NOW) > score_item(old_cold, weights, 14, NOW)
