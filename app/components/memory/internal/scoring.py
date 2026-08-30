"""召回评分：遗忘曲线 × 提取练习效应的合成打分（设计定稿 §6.4）。

    score = α·相关度 + β·时近性(exp(-Δt/τ)) + γ·(重要度 + 访问加成)

- τ 由配置的半衰期天数推导（exp 在 Δt=半衰期时恰降一半）；
- Δt 以「最近活跃时刻」计——取 last_accessed_at 与发生时间中较新者
  （解析见调用方），由本模块只收最终入参，保持纯函数可测；
- 访问加成随 access_count 递增但封顶：模拟提取练习的边际递减，
  防止历史热点凭访问次数永久压住新事实；
- 快速回忆路径没有相关度输入（零 embedding 调用约束），relevance=None
  时按 β:γ 归一加权退化为「时近 × 重要」排序。
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

_LN2 = math.log(2)

# 访问加成：每被召回一次 +0.05 重要度，累计封顶 +0.2
ACCESS_BONUS_STEP = 0.05
ACCESS_BONUS_CAP = 0.2


@dataclass(frozen=True)
class ScoreWeights:
    relevance: float = 0.5
    recency: float = 0.2
    importance: float = 0.3


@dataclass(frozen=True)
class ScorableItem:
    """参与打分的单一记忆条目投影。"""

    relevance: Optional[float] = None
    last_active: Optional[datetime] = None
    importance: float = 0.5
    access_count: int = 0


def _aware(dt: datetime) -> datetime:
    # SQLite 方言不保留 tzinfo（存储恒为 UTC 墙钟）：读回的 naive 统一按 UTC 补齐，
    # 避免 aware 的 now 与库值相减时抛 offset-naive/aware 混算
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def recency_factor(last_active: Optional[datetime], now: datetime, half_life_days: float) -> float:
    """指数衰减遗忘曲线；无时间锚视为刚活跃（不打压）。"""
    if last_active is None:
        return 1.0
    if half_life_days <= 0:
        return 1.0
    delta_seconds = (_aware(now) - _aware(last_active)).total_seconds()
    if delta_seconds <= 0:
        return 1.0
    tau_days = half_life_days / _LN2
    return math.exp(-(delta_seconds / 86400.0) / tau_days)


def access_bonus(access_count: int) -> float:
    return min(max(access_count, 0) * ACCESS_BONUS_STEP, ACCESS_BONUS_CAP)


def score_item(item: ScorableItem, weights: ScoreWeights, half_life_days: float, now: datetime) -> float:
    rec = recency_factor(item.last_active, now, half_life_days)
    # 重要度含访问加成，钳制到 0-1：加成只能逼近满分不能突破
    imp = min(max(item.importance, 0.0), 1.0) + access_bonus(item.access_count)
    imp = min(imp, 1.0)

    if item.relevance is None:
        # 快路径：无检索相关度，剩余两轴归一化加权
        total = weights.recency + weights.importance
        if total <= 0:
            return 0.0
        return (weights.recency * rec + weights.importance * imp) / total
    return weights.relevance * item.relevance + weights.recency * rec + weights.importance * imp
