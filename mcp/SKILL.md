---
name: agy-advise
description: 用本机 Codex 的 agy_advise MCP 工具向 Google Antigravity 请求跨项目的独立全局技术判断与项目级决策治理。适用于任何项目（软件架构、系统重构、性能/排障卡点、ML/RL 实验、产品技术取舍）的全局审查、前提挑战、反方红队评审、阶段复盘与第二意见。
---

# Antigravity Advisor (`agy-advise`)

通过本机注册的 `agy_advise` MCP 工具，请 Google Antigravity 作为**独立全局技术审查员**，基于目标、硬约束与已证实证据给出不顺从局部惯性的判断。CLI 在临时目录的只读禁工具沙箱中运行，文件、命令、浏览器和 MCP 均被禁用，仅接收本次显式发送的文本。

本 Skill 提供两层无缝衔接的能力：
1. **全局问答模式（默认，零配置适用于任意项目）**：单次提问或短追问即可获得“先跳出框架审前提、再落到行动与止损”的全局意见。
2. **项目决策核心模式（`project` 模式）**：面向需要跨多轮迭代防跑偏的项目，持久化保存目标、冻结门槛、红线约束、路线历史与证据账本，在停滞、空转、代理指标漂移或保护项回归时自动拉停并触发无菌室全局复盘。

---

## 一、什么时候问

- 需要跨局部细节做**全局判断**：架构取舍、路线分叉、反复排查仍未收敛的卡点、破坏性改动前的风险与回滚评估、怀疑当前正在“修局部症状而非主矛盾”。
- 需要**独立第二意见或红队挑刺**：在投入大量开发/算力成本前，验证主假设是否成立、有哪些未证实前提和最便宜的证伪测试。
- 文件内容、API 签名、明显拼写、高频工具循环中的机械小问题应在本地直接解决，不调用本工具。

---

## 二、全局问答模式（适用于任何项目的即时咨询）

| 参数 | 说明 |
| --- | --- |
| `question` | 每次聚焦一个核心判断。写清终局目标、硬约束、已观察到的失败与待裁决事项。首问须自包含；不要诱导顾问“从我给的两个小补丁里挑一个”。 |
| `context` | 可省略。提供获授权分享的**高信噪比结构化事实**（建议按：目标 / 硬约束 / 已试方案与实测结果 / 关键未知组织），不限死 10 行但禁止粘贴冗长日志、未授权机密或 Codex 内部推理过程。 |
| `stance` | 顾问立场，默认 `"global"`：<br>• `global`（默认）：先拉高视角审计前提与主矛盾，区分**已证实事实 / 推断 / 关键未知**，有权否定当前提问框架或建议停掉沉没成本分支，再给出最高杠杆下一步与止损条件。<br>• `red_team`：纯反方红队审计，专门构造当前方案会失败的最强论据、暴露代理指标陷阱与单点风险，并给出最便宜的证伪测试。<br>• `tactical`：仅在大方向已锁定且只需要具体实现/实验细节时使用；仍会指出明显前提矛盾。 |
| `session_id` | 首次提问、换主题或换项目时省略。继续同一判断时传入上次返回的 ID，仅补充新证据。 |
| `timeout_seconds` | `1–240` 秒，默认 `240` 秒（含排队时间）。 |
| `effort` | 新会话可选推理档位（`low`/`medium`/`high`/`xhigh`/`max`），追问沿用原档位。 |
| `model` | 新会话可选模型（如 `gemini-4-neon-high`），未传时读取 `AGY_ADVISE_MODEL`。追问沿用原模型。 |

**提问原则与分工边界（战略归顾问，战术与算账归本地）**：
- **严禁把 `agy_advise` 当成局部算账/微操审批员**：脚本参数、步数/批次算术对账、SHA/文件归档、微小的执行口径冲突全部由本地 Codex 自行解决，**禁止**反复用 `stance="tactical"` 追问细化预算或核对微观 SOP。
- 把 Codex 自己的执行限制留在本地，只把影响全局技术与效率判断的真实约束发给顾问。
- 避免“帮我给这个局部微调定一个阈值（200字内）”这类框死视角的问法；应交代全局目标、总成本/耗时和此前失败，允许顾问回答“这个方向或这个代理门本身该停，改走更直接/可并行的路径”。

---

## 三、项目决策核心模式（`project` 模式）

在同一 `agy_advise` 工具中传入 `project`，以 `(id, phase)` 标识项目阶段。支持**任意领域项目**——既支持数值指标（越高越好或越低越好），也支持无分数的定性里程碑（架构迁移、根因排查、功能交付）。

### 支持的动作（`project.action`）

| `action` | 作用与核心字段 |
| --- | --- |
| `init` | 绑定不可篡改的 `objective`、`frozen_gate`、`route`，可选 `domain`、`non_negotiables`（硬红线列表）、`initial_facts`（预置的跨轮硬事实写入 L2 记忆）。<br>• **数值门槛项目**：同时提供 `score_name`、`baseline_score`、`pass_score`，可选 `score_direction`（`"higher"` 默认 / `"lower"` 如延迟、错误率）和 `min_improvement`（过滤噪声抖动）。<br>• **定性里程碑项目**：直接省略上述三个分数字段，靠 `outcome` 推进，无需编造假分数。<br>• 可选防跑偏阈值：`stagnation_limit`（默认 `2`）、`same_mechanism_limit`（默认 `3`）、`proxy_limit`（默认 `3`）、`review_cadence`（周期强制体检步数）。 |
| `report` | 上报一次已结束的尝试：`trial_id`、`route`、`cycle`、`mechanism`（假设/手段类别）、`evidence`。<br>• 数值项目仅真实端到端门槛评测填 `score`；代理/离线/诊断证据留空 `score` 并可标 `evidence_kind: "proxy" \| "diagnostic"`。<br>• 定性项目不填 `score`，用 `outcome`（`progress` / `no_progress` / `disproved` / `blocked` / `qualified`）表达结果。<br>• 红线/保护项受损填 `regressions`；全部冻结门槛经本地核实通过才填 `qualified: true`（或 `outcome: "qualified"`）；可选 `artifacts` 记录产物路径/哈希（仅作元数据，工具不会读取本地文件）。<br>• 并行多条结果回收时，可设 `auto_review: false` 瞬时批量入账，最后统一调用一次 `review`，避免中间复盘因新证据到达而 `stale`。 |
| `memory` | 显式维护 L2 蒸馏记忆（不调 LLM、不改冻结门槛）：`add_facts` / `remove_facts` 增删跨轮硬事实（上限 12 条），`route_postmortems` 记录或补录已放弃路线的 1–2 句尸检结论（上限 12 条）。 |
| `status` | 仅读本地状态（含 `pending_unblock` 待核销清单与 `memory_counts`）；传 `include_history: true` 可导出完整三层记忆、路线统计与 `markdown_summary`（便于落盘到项目仓库人工审计）。 |
| `list` | 列出本地持久化库中的所有项目阶段（可传 `id` 过滤），仅读本地。 |
| `review` | 主动触发一次无菌室全局复盘，或重试先前超时/无效的复盘。常规解封/短验复查建议传 `effort: "medium"` 提速，重大停滞或换路复盘用默认/高档位。 |
| `activate` | 用当前 `decision_id` 激活 `CONTINUE` 或 `PIVOT` 裁决的新轮次、路线与次数预算，清空 `pending_unblock` 并重新开放 `dispatch_allowed`。 |

### 自动拉停与反死胡同触发器（含跨周期宏观护栏）

满足以下任一条件时，工具先持久化入账，将 `project.dispatch_allowed` 置为 `false`，并自动发起复盘（除非 `auto_review: false` 延迟到手动 `review`）：
1. `stagnation`：单周期内达到 `stagnation_limit` 个不同 `mechanism` 均未实质推进门槛；
2. `same_mechanism_spin`：同一个 `mechanism` 连续空转达到 `same_mechanism_limit` 次（堵住单机制反复微调漏洞）；
3. `proxy_drift` / `global_proxy_drift`：单周期或**跨周期累计**（`activate` 不会洗白）连续 `proxy_limit` 次只报代理/诊断证据、未跑真实门槛评测（堵住“每轮改个路线名就洗白代理空转”的漏洞）；
4. `macro_stagnation` / `pivot_churn`：**跨周期累计**长期未推进真实门槛（默认 ≥6 次试验或 ≥3 次 `PIVOT` 仍无真分进展），自动把复盘切入 **`MACRO ESCALATION` 宏观重置模式**，禁止再发局部微补丁或换皮 `PIVOT`，强制在大尺度结构换路、直取端到端真分或升级请示用户之间抉择；
5. `protected_regression`：任一保护项/红线回归；
6. `blocker`：上报 `outcome: "blocked"`；
7. `budget_exhausted`：当前轮次达到已激活计划的 `max_trials`；
8. `review_cadence`：达到设定的周期性强制体检步数；
9. `qualified_result`：出现满足冻结门槛的验收结果，须复核后才可 `ADVANCE`。

### 三层外置记忆 + 效率记分卡 + 无菌室复盘协议

- **绝不复用长聊天历史**：`project` 模式禁止传 `session_id` 和 `stance`，每次复盘必开干净会话，彻底避免沉没成本锚定与上下文滚雪球。
- **O(1) 有界三层外置记忆包**（存在本地 SQLite，发送给顾问时自动压缩微观噪声，保留高信噪比全局视图）：
  1. **L1 宪法层（永不压缩）**：`objective`、`frozen_gate`、`non_negotiables`、**跨周期 `efficiency_scorecard`**（真实门槛评测占比、距上次真分提升的试验/周期/`PIVOT` 数、距上次真分评测的试验数、`max_trials=1` 占比及 `macro_alerts`）、精简版 `active_plan` 及生效中的 `pending_unblock`；
  2. **L2 蒸馏记忆层（战略级认知，各限 12 条）**：`route_postmortems`（已放弃路线的 1–2 句证伪结论）、`tried_hypothesis_families`（已试策略大类，防止同大类换皮 `PIVOT`）、`verified_facts`（只沉淀跨轮因果规律，**不塞单次运行的 SHA、拍号或原始报表**）与 `route_stats`；
  3. **L3 近期工作记忆层（紧凑滑动窗口）**：最近 4 条裁决、最近 16 条试验摘要、最近 4 条压缩证据（完整原文仍持久化在 SQLite）。
- **战略授权而非保姆式微操**：
  - `CONTINUE`/`PIVOT` 强制包含 `route`、`next_trial`、`expected_signal`、`stop_condition`、`max_trials`，并建议给出 `hypothesis_family`、`efficiency_rationale` 与 `parallel_tracks`（可并行推进的非阻塞工作流）；
  - 实质路线默认授权 `max_trials = 2–5` 的连续跑道，避免每跑 1 次就停下来审一次；仅单次高成本/不可逆验收或前置阻塞才用 `max_trials = 1`；
  - `next_trial` 只锁定假设、核心机制变化、高层预算与证伪条件，不替本地写脆弱的多阶段微观算术 SOP；
  - `HOLD` **代码级强制包含** `unblock_actions`（1–3 项具体修复/排查动作）与 `unblock_criteria`（重新申请放行的最小可验证证据），并挂入 `project.pending_unblock`；下次 `review` 时自动置顶核销；
  - 调用 `review` 时不要在 `question` 里替顾问提前定调，只陈述事实与约束。
- 每次派发新任务前检查 `project.dispatch_allowed`；为 `false` 时暂停新派发，但继续据实上报已完成的在途任务。超时或新证据并发到达时，待复盘状态保留且旧裁决自动作废（`stale`），重启 MCP 后可直接 `review` 续接。

### 跨领域调用示例

1. **定性项目（后端架构迁移 / 复杂排障，无需伪造分数）**：
```json
{
  "project": {
    "id": "order-service",
    "phase": "cutover-design",
    "action": "init",
    "domain": "architecture",
    "objective": "消除同一 SKU 并发扣减时的丢写并完成无停机迁移",
    "frozen_gate": "100 并发压测零丢写且旧版 API 契约全量回归通过",
    "non_negotiables": ["不破坏现有 HTTP API", "P99 延迟增幅 < 10ms"],
    "route": "optimistic-version-lock",
    "review_cadence": 4
  }
}
```

2. **量化项目（越低越好：P99 延迟优化；或越高越好：通过率）**：
```json
{
  "model": "gemini-4-neon-high",
  "project": {
    "id": "search-engine",
    "phase": "p99-latency",
    "action": "init",
    "domain": "performance",
    "objective": "将线上查询 P99 从 240ms 压至 <= 80ms",
    "frozen_gate": "真实回放流量 10 分钟 P99 <= 80ms 且召回率下降 < 0.2%",
    "score_name": "p99_ms",
    "baseline_score": 240,
    "pass_score": 80,
    "score_direction": "lower",
    "min_improvement": 5,
    "route": "index-pruning"
  }
}
```

3. **并行结果批量入账后统一复盘**：
```json
{
  "project": {
    "id": "robotics",
    "phase": "phase2",
    "action": "report",
    "trial_id": "side-drift-ablation",
    "route": "local-actor-refinement",
    "cycle": 0,
    "mechanism": "side-drift-reward",
    "score": 2,
    "evidence": "冻结动态仍为 2/8，侧漂失分增加。",
    "auto_review": false
  }
}
```

持久化库默认路径为 `$XDG_DATA_HOME/codex-agy-advise/projects.sqlite3`（未设 XDG 时为 `~/.local/share/codex-agy-advise/projects.sqlite3`），测试可用 `AGY_ADVISE_DB` 指定隔离库。

---

## 四、冷启动、追问与核验

- **冷启动与追问**：新会话提供完整背景；同一判断的追问复用 `session_id` 并只补新事实。空闲 10 分钟后子进程退出，仍可凭已有 `session_id` 续接；MCP 重启、换模型或换档位时省略 `session_id` 开新会话。
- **拿到建议后**：在本地核实建议依赖的代码、日志、假设与测试，再决定是否采纳。同一判断最多追问 3 次；仍有核心分歧时停止循环，向用户呈现证据、分歧与待验证事实。

---

## 五、调用故障处置

- **只给过程描述不给判断**：补齐判据或目标，要求直接输出结论与理由。
- **空回复或权限相关失败**：由本地先核查文件/命令事实，改写成纯文本可判断的问题后省略旧 `session_id` 重试。
- **超时**：检查 `queue_seconds` 与 `elapsed_seconds` 区分排队还是执行耗时；项目复盘超时会保留待复盘状态，可安全用 `action: "review"` 重试；普通问答超时应省略旧 `session_id` 精简重问。
- **登录或配额错误**：直接向用户报告工具返回的具体原因。安装或迁移后先用原生 `~/.local/bin/agy --print-timeout 30s --print "Reply with exactly: OK"` 验证登录与无交互模式；真实顾问联调走已注册的 `agy_advise` MCP 工具，不要在 Codex 命令沙箱子 shell 里嵌套启动 `server.py`/`agy` 代替，避免凭据与运行环境差异造成误判。
- **OAuth `Code Challenge must be base64 encoded`**：检查复制到浏览器的授权 URL 是否混入换行、缩进或空格，只使用完整单行 URL。授权码仅粘贴到本机 CLI，不发送到聊天或其他工具。
- **超时预防**：常规问答或 `HOLD` 解封复查优先传 `effort="medium"` 且 `timeout_seconds >= 120`；重大停滞或换路复盘留 `180–240` 秒。
- 连续两次仍无有效判断时停止重试并向用户报告现象与处置记录。

---

## 六、调用审计日志与反馈迭代

每次 `agy_advise` 调用（无论普通问答还是 `project` 模式、成功还是超时/报错）都会以 `0o600` 权限自动追加一条结构化记录到与 `projects.sqlite3` 同目录的 `invocations.jsonl`（隔离测试设 `AGY_ADVISE_DB` 时自动随库隔离，不污染生产日志；日志写入失败不会影响主调用）。

- **隐私与开关**：默认只记元数据（`invocation_id`、模式、动作、`stance`、耗时、排队、token、模型、触发原因、裁决、状态）与短预览（question 前 240 字 / advice 前 320 字）；需要深度提示词复盘时设 `AGY_ADVISE_LOG_FULL=1`；如需关闭日志设 `AGY_ADVISE_LOG=off`；自定义路径设 `AGY_ADVISE_LOG=/path/to/invocations.jsonl`。
- **查看统计与调优提示**：
  ```bash
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py --days 14
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py summary --project robotics --json
  ```
- **事后质量打标（用于持续优化提示词与触发阈值）**：
  ```bash
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py feedback <invocation_id> <helpful|rubber_stamp|missed_root_cause|false_alarm|bad_format> --note "简短复盘原因"
  ```

基础传输与复核规范演化自[上游 dbc9ad2](https://github.com/fishing-dev-sm/pi-agy_advise/commit/dbc9ad2954797cd0a95ccfac4de6440839641de8)。
