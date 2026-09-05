"""会话域业务信号：key 布局与语义常量。

信号机制由通用库 ``app.packages.signal`` 承担，本模块只声明会话域的
信号用途——每个用途 = 一个 key 构造 + 语义常量 + 消费方业务流程。
新增信号用途（如 keepalive 心跳：编排循环周期 fire 刷存活、看门狗
wait/is_fired 判僵死）照此扩展，不加类。

取消信号（第一用途）：thread 作用域取消标志——一个会话同一时刻只有
一个活跃轮次，标志按 thread 覆盖。生产端为 REST cancel 接口（经
AgenticService 归属校验后写入），消费端为流式循环（帧级节流
is_fired）与工具入口守卫；触发后保持到下一轮 open_turn 防御性 reset
（TTL 为兜底上限）。
"""

from uuid import UUID

# 取消标志 TTL（秒）：孤儿标志的最长存活上限；正常生命周期内 open_turn
# 防御性清理，TTL 只作兜底
CANCEL_FLAG_TTL_SECONDS = 3600


def cancel_flag_key(thread_id: UUID | str) -> str:
    """thread 作用域取消标志的相对 key（命名空间前缀由 SignalStore 实现负责）。"""
    return f"conversation:{thread_id}:cancel"
