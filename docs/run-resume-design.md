# SSE 断流改造设计：服务端保持 run + 断线 attach 续看

> 配套调研结论见会话记录。本文以对象交互图 + 时序图确认流程；实现顺序与范围见
> 会话中已确认的方案（进程内后台执行 + 后端前端全链路）。

## 一、对象交互图（组件与归属边界）

核心：**泵线程与订阅连接分属两个生命周期**——HTTP 请求侧只做「发起 run / 订阅
事件流」，泵在后台存活，事件日志是两者的唯一交汇点。

```mermaid
flowchart LR
    subgraph FE["前端（浏览器）"]
        RT["react-ag-ui runtime<br/>AgUiThreadRuntimeCore<br/>（isRunning / 重放聚合）"]
        UCL["use-conversation-list<br/>切换=不取消(reason=switch)<br/>停止=全量取消(reason=stop)"]
        AGT["HttpAgent<br/>POST /agentic/run"]
        RSS["run-stream-service（新）<br/>GET stream 信封解析<br/>产出 ag-ui 事件 + seq 游标"]
    end

    subgraph HTTP["API 进程 · 请求生命周期（随连接生灭）"]
        EP1["POST /agentic/run"]
        EP2["GET /conversation/{tid}/stream<br/>?after=seq（重放+尾随）"]
        EP3["GET /conversation/{tid}/active-run"]
        EP4["POST /run/cancel"]
    end

    subgraph BG["API 进程 · 后台（脱离连接存活）"]
        HUB["RunHub（@injectable 单例）<br/>注册表 tid→ActiveRun<br/>per-run 锁 / 订阅队列集 / 看门狗"]
        PUMP["泵线程 ×N（线程池）<br/>原 _stream_run：yield→publish"]
    end

    subgraph STATE["持久化与基础设施"]
        ELOG[("agentic_run_event 事件日志<br/>(thread,run,seq) 唯一")]
        CONV["ConversationService / Repository<br/>轮次状态机 / 消息落库"]
        SIG["SignalStore（Redis）<br/>取消标志"]
        FIN["TurnFinalizer 线程池<br/>标题 / 记忆"]
        LGC["BaseAgent / LangGraph<br/>调用方泵执行"]
    end

    UCL -->|"新 run"| AGT --> EP1
    UCL -->|"切换/刷新"| RSS --> EP2
    UCL -->|"发现活跃 run"| EP3
    UCL -->|"停止按钮"| EP4

    EP1 -->|"start_run()"| HUB
    EP1 -->|"subscribe(after=0)"| HUB
    EP2 -->|"subscribe(after=游标)"| HUB
    EP4 -->|"归属校验+置标志"| SIG

    HUB -->|"提交泵任务"| PUMP
    PUMP -->|"逐帧 publish"| HUB
    PUMP -->|"stream_events 驱动"| LGC
    PUMP -->|"open/complete/fail_turn<br/>store_assistant_messages"| CONV
    PUMP -->|"帧级取消检查(≤0.5s)"| SIG
    PUMP -->|"流闭后提交"| FIN

    HUB -->|"append（先落盘）"| ELOG
    HUB -->|"list_after 重放"| ELOG
    HUB -->|"看门狗：超时置取消标志"| SIG
```

职责要点：

- **RunHub** 是唯一的「run 存活」注册表：登记/注销 ActiveRun、per-run 锁保证
  「重放→尾随」无缺口、看门狗强制终止帧、同线程单活跃 run 守卫。
- **泵线程**持有原 `_stream_run` 全部业务语义（open_turn、双翻译器、取消检查、
  落库、finalizer）；断连不再出现在它的世界里。
- **事件日志**是请求侧与后台侧的唯一交汇：先落盘再广播；订阅端点读它重放。

## 二、时序 1：发起 run + 中途断连（run 存活跑完）

```mermaid
sequenceDiagram
    autonumber
    participant C as 客户端
    participant EP as POST /agentic/run
    participant Svc as AgenticService
    participant Hub as RunHub
    participant P as 泵线程 _stream_run
    participant Log as 事件日志(DB)

    C->>EP: POST run(threadId, runId, message)
    EP->>Svc: service.run(...)
    Svc->>Hub: start_run(threadId, runId, ...)
    Note over Hub: 登记ActiveRun<br/>同线程已有活跃run→业务报错
    Hub->>P: 提交线程池（异步）
    Svc->>Hub: subscribe(threadId, runId, after=0)
    EP-->>C: 200 SSE（纯 ag-ui 帧，契约不变）

    P->>P: open_turn（建轮次+存用户消息）
    loop interleave 逐帧
        P->>Hub: publish(seq=n, frame)
        Hub->>Log: append（先落盘再广播）
        Hub-->>EP: 推入订阅队列
        EP-->>C: data: {ag-ui 帧}
    end

    C->>C: 刷新页面 / 切走会话（SSE 断开）
    Note over EP,Hub: 仅摘除该订阅者队列；<br/>不再有 GeneratorExit 杀 run
    Note over P: 泵不受影响，继续拉取执行

    P->>Hub: publish(..., RUN_FINISHED)
    P->>P: complete_turn + 消息批量落库 + usage
    P->>P: finalizer（标题/记忆，后台线程池）
    Hub->>Hub: 注销 ActiveRun，泵线程归还
```

不变量：断连只影响「订阅者集合」；轮次终态、消息落库、finalizer 与客户端
是否在场完全解耦。

## 三、时序 2：刷新/切回后 attach 续看（存量重放 + 实时尾随）

```mermaid
sequenceDiagram
    autonumber
    participant C as 浏览器（新页面/切回）
    participant API as REST/SSE 端点
    participant Hub as RunHub
    participant Log as 事件日志
    participant RT as react-ag-ui core

    C->>API: GET history(threadId)
    API-->>C: COMPLETED 轮消息 + RUNNING 轮的用户消息
    C->>RT: applyExternalMessages(历史)
    C->>API: GET active-run(threadId)
    API-->>C: {active:true, runId, lastSeq}

    alt 活跃 run 存在
        C->>API: GET stream?after=0&run=runId
        API->>Hub: subscribe(threadId, runId, after=0)
        Hub->>Log: list_after(0)
        Log-->>Hub: 存量帧 seq 0..k
        Note over Hub: per-run 锁内：先登记队列<br/>再快照 last_seq，重放不漏不重
        loop 存量
            API-->>C: data:{seq, event}
            RT->>RT: 重放进 assistant 消息<br/>（unstable_resume 路径）
        end
        loop 尾随直至终止
            Hub-->>API: 实时新帧
            API-->>C: data:{seq, event}
        end
        Hub-->>API: RUN_FINISHED / RUN_ERROR（终止帧必有）
        API-->>C: SSE 正常结束
        C->>API: 刷新 history（终态+落库消息可见）
    else 无活跃 run
        Note over C: 仅展示历史；保留期内最近已结束<br/>run 仍可重放（迟到刷新场景）
    end
```

前端侧：`use-conversation-list` 切换时不再 cancel；attach 后 isRunning 恢复、
停止按钮可用（REST cancel 照常生效）。

## 四、时序 3：用户主动停止（唯一取消通道，语义不变）

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant FE as 前端 runtime
    participant EP as POST /run/cancel
    participant Sig as SignalStore(Redis)
    participant P as 泵线程
    participant Hub as RunHub

    U->>FE: 点击停止
    FE->>EP: cancel(threadId)
    EP->>Sig: 置取消标志(TTL)
    EP-->>FE: {canceled:true}（立即返回）
    FE->>FE: 本地 abort 当前流
    Note over P: 帧级节流检查(≤0.5s)感知标志
    P->>P: 轮次置 CANCELED，半截消息不落库
    P->>Hub: publish 结束（流关闭，无 ag-ui 终止帧）
    Hub->>Hub: 注销 ActiveRun
    Note over FE: 流结束→重拉 history 对账，<br/>渲染「已停止生成」占位
```

与现状的差别仅在：取消必须显式发生（stop 按钮或 REST），断连不再隐式取消。
订阅侧终止表达 = SSE 流结束 + 历史终态对账（ag-ui 无服务端 CANCELLED 事件，
口径与现网一致）。

## 五、时序 4：看门狗强制终止（防失控/防 HITL 卡死）

```mermaid
sequenceDiagram
    autonumber
    participant WD as RunHub 看门狗
    participant Sig as 取消标志
    participant P as 泵线程
    participant Turn as 轮次(DB)
    participant Sub as 订阅者

    Note over WD: ActiveRun 超过 max_duration（默认30min）
    WD->>Sig: 置取消标志(threadId)
    P->>P: 帧边界(≤0.5s)感知 → 走失败收口
    P->>Turn: 置 FAILED
    P->>Sub: publish RUN_ERROR("运行超时")
    P->>P: usage 落库，线程归还

    alt 泵阻塞在不可打断的工具内（next() 未返回）
        Note over WD: 宽限期（如60s）后仍存活：<br/>hub 强制注销 ActiveRun（僵尸标记），
        WD->>Turn: 置 FAILED（hub 侧兜底写库）
        WD-->>Sub: 信封级终止帧，SSE 关闭
        Note over P: 线程池线程不可强杀——<br/>cancel 标志保证工具返回后即退出
    end
```

与 per-user 并发上限共同保证：线程池不会被悬挂 run 耗尽；任何 run 必有终点。

## 六、时序 5：未来 HITL interrupt（本期仅预留，验证「不卡泵」契约）

```mermaid
sequenceDiagram
    autonumber
    participant P as 泵线程
    participant G as LangGraph(interrupt())
    participant Hub as RunHub
    participant C as 客户端
    participant FE as UI(requires-action)

    P->>G: interleave 前进
    G-->>P: __interrupt__（图在边界终止，非阻塞等待）
    P->>Hub: publish CUSTOM {type:"interrupt", interrupts:[...]}
    P->>Hub: publish RUN_FINISHED（终止帧）
    P->>P: 轮次置 WAITING_INPUT，线程归还
    C->>FE: 实时/attach 重放收到 interrupt 事件
    FE->>FE: assistant 消息 → requires-action（审批 UI）
    Note over FE: 人的决策 = submitInterruptResponses<br/>= 全新 POST /agentic/run<br/>（历史回放或将来 checkpoint 重建上下文）
```

契约要点：**泵线程永远不等人工输入**——interrupt 是 run 的终止边界，人的决策
以新 run 请求到达。看门狗（时序 4）兜住「工具内部长轮询审批」这类例外。

## 七、流程不变量清单（评审用）

1. **先落盘再广播**：崩溃最多丢实时广播，不丢事件序；重放以 DB 为准。
2. **重放无缺口**：subscribe 在 per-run 锁内「登记队列 → 快照 last_seq → 读
   日志 → 尾随」；订阅方按 seq 去重兜底。
3. **终止帧必有**：RUN_FINISHED / RUN_ERROR 必达订阅者（cancel 路径 = 流结束 +
   历史终态对账）；看门狗强制超时 run 收口。
4. **断连 ≠ 取消**：断连只摘订阅者；取消只有显式通道（stop / REST / 看门狗）。
5. **run 有限契约**：每次 run 有限时长、有限输出、释放泵线程；人工输入以新
   run 到达。
6. **POST /agentic/run 契约字节级不变**：发起方与重放订阅方是两种消费形态。
