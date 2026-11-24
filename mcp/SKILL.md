---
name: agy-advise
description: 用本机 Codex 的 agy_advise MCP 工具向 Google Antigravity 请求跨项目的独立全局技术判断与战略级辅助决策。定位是敢提反对意见、跳出局部最优解的远程 AI 总指挥，支持全局问答、红队质疑、人类指令前置反方核查、分级长期记忆与闭环结果归因。
---

# Antigravity Advisor (`agy-advise`) — 远程 AI 总指挥

通过本机注册的 `agy_advise` MCP 工具，让 Google Antigravity 担任**独立的全局技术总指挥**：它的首要职责不是做流程盖章员，而是**站在全局视角挑战前提、提出不同意见、帮团队跳出局部最优解和代理指标陷阱**。CLI 在临时目录的只读禁工具沙箱中运行，保持现有安全权限不变，仅接收显式提交的文本。

本 Skill 提供两层协同能力：
1. **全局问答模式（默认，零配置适用任意项目）**：单次提问或短追问，先质疑前提与局部最优、再给出高杠杆行动与最便宜证伪测试。
2. **项目总指挥模式（`project` 模式）**：面向需要跨多轮探索与迭代的项目，持久化保存目标、门槛、动态预算包络、置顶因果记忆与闭环结果归因；即使人类拍板了方向，也能做一次快速反方核查，**无硬伤直接生效放行、有实质异议则明确亮红牌提示人机对齐**。

---

## 一、什么时候问

- **方向与前提判断**：架构取舍、路线分叉、反复排查陷入局部最优、准备投入大算力/大改动前。
- **人类拍板后的反方体检**：负责人已定下新路线或重批预算时，用 `project.action="directive"` 同步最新预算并让总指挥做一次快速反方预验尸（Pre-mortem）——避免“只走盖章流程、不提不同意见”。
- **独立红队挑刺**：验证主假设是否存在盲区、代理指标漂移或沉没成本，寻找最便宜的证伪检查。
- 读文件、查 API 签名、参数/步数算术对账、SHA/文件归档等局部执行细节全部在本地直接处理，不调用本工具。

---

## 二、全局问答模式（即时咨询）

| 参数 | 说明 |
| --- | --- |
| `question` | 每次聚焦一个核心判断：写清终局目标、硬约束、已观察到的失败与待裁决点。首问须自包含，禁止诱导顾问在两个局部小补丁里二选一。 |
| `context` | 可省略。提供高信噪比结构化事实（目标 / 约束 / 已试路径与实测 / 关键未知），禁止粘贴冗长日志、未授权机密或 Codex 内部推理。 |
| `stance` | 默认 `"global"`：<br>• `global`（默认）：全局指挥官视角，具备明确的反顺从与异议权；若证据指向局部最优或更优宏观路径，会直接先亮出 **Dissent / Counter-Case**。<br>• `red_team`：纯反方红队审计，专门构造当前方案会翻车的最强论据、暴露代理陷阱，并给出最便宜证伪检查。<br>• `tactical`：仅大方向已定且只需局部技术方案时使用；若发现局部微操陷阱仍会直言反对。 |
| `session_id` | 首问、换主题或换项目时省略；继续同一判断时传入上次 ID，仅补新证据。 |
| `timeout_seconds` | `1–240` 秒，默认 `240` 秒。 |
| `effort` / `model` | 新会话可选推理档位（`low`/`medium`/`high`/`xhigh`/`max`）与模型；未传模型读取 `AGY_ADVISE_MODEL` 或 CLI 默认设置。 |

---

## 三、项目总指挥模式（`project` 模式）

在同一 `agy_advise` 工具中传入 `project`，以 `(id, phase)` 标识项目阶段。

### 支持的动作（`project.action`）

| `action` | 作用与核心字段 |
| --- | --- |
| `init` | 初始化阶段：`objective`、`frozen_gate`、`route`，可选 `domain`、`non_negotiables`（真正不可妥协的物理/安全红线）、`budget_envelope` / `budget_note`（**可随指令动态更新的阶段预算，不要写死进 `non_negotiables`**）、`initial_facts`（自动置顶进 L2 因果记忆）。<br>• **凡有明确数值目标（如 `6/8`、`7/8`、`<= 80ms`）必须同时传 `score_name`、`baseline_score`、`pass_score`**（可选 `score_direction="higher"\|"lower"`、`min_improvement`），不要用定性模式掩盖数值门禁。<br>• 纯定性里程碑可省略分数字段，但只有 `evidence_kind="gate"` 的真实门禁进展才会计为推进。 |
| `directive` | **人类拍板 / 重批预算 / 指定路线专用通道**：传 `directive`（人类指令摘要）、可选 `budget_envelope` / `budget_note`、`route`、`hypothesis_family`、`max_trials`（默认 `3`）、`unblock`（默认 `true`）、`override_dissent`（默认 `false`）、`run_advisor_check`（默认 `true`）。<br>• 先实时更新 L1 的 `budget_envelope`（杜绝宪法与最新预算自相矛盾），再默认触发一次精简快速的**反方预验尸（Pre-mortem）**：<br>  - `concur` / `caution`：附上风控护栏与最便宜证伪检查，**立即自动激活放行（`dispatch_allowed=true`）**，省去额外 `activate` 往返；<br>  - `dissent`：总指挥基于实测证据提出明确异议（`dissent_points`、`cheapest_falsifier`、`alternative_path`），先暂停并把反对意见呈给人类确认；若人类看后仍坚持原决定，调一次 `activate(decision_id=...)` 即可立刻放行并自动标记 `human_overrode_dissent=true` 供事后胜率追踪。 |
| `report` | 上报已结束尝试：`trial_id`、`route`、`cycle`、`mechanism`、`evidence`。<br>• 数值项目仅真实端到端门禁评测填 `score`；代理/诊断留空 `score` 并标 `evidence_kind: "proxy" \| "diagnostic"`。<br>• **分级回归 `regression_severity`**：默认 `"hard"`（触碰不可妥协红线，立即拉停）；若是在 `max_trials=3..5` 探索跑道内的中间候选瑕疵/次级退步，标 `"soft"` 记录在案但不提前打断跑道。<br>• **`HOLD` 状态下严禁上报普通试次**：必须传 `unblock_check: true` 记入独立核验区（不污染主试次统计），或先用 `directive` / `review` 解除 `HOLD`，或用 `suspend` 冻结旧阶段并单列新阶段。<br>• 多路并行回收时设 `auto_review: false` 批量入账，最后统一 `review`。 |
| `review` | 主动触发无菌室全局复盘（采用原生 `--json-schema` 强制结构化输出，强制包含 `contrarian_check` 反方视角与 `escape_hatch` 逃生舱）。 |
| `activate` | 用当前 `decision_id` 激活 `CONTINUE` / `PIVOT`，或确认覆盖 `DISSENT`（标记 `human_overrode_dissent=true`）并恢复 `dispatch_allowed`。 |
| `suspend` | **正式冻结/归档停滞或被替代的阶段**：传 `reason` 与可选 `superseded_by`，将阶段置为 `suspended`（彻底终结无意义的连续 `HOLD` 循环）。 |
| `memory` | 维护分级 L2 记忆（不调 LLM）：<br>• `pin_facts`：**置顶跨轮物理/架构因果硬规律**（上限 10 条，绝不被普通滚动事实挤出失忆）；<br>• `add_facts`：普通滚动因果事实；<br>• `governance_notes`：签收/分账/预算等行政纪要（独立 4 条槽位，不挤占技术因果记忆）；<br>• `remove_facts` / `route_postmortems`。 |
| `feedback` | **MCP 内置事后质量打标**（无需敲终端 CLI）：传 `invocation_id`、`label`（`helpful` / `good_dissent` / `rubber_stamp` / `missed_root_cause` / `false_alarm` / `bad_format`）与 `note`。 |
| `status` / `list` | 仅读本地状态；`status(include_history=true)` 导出完整三层记忆、闭环结果归因（`track_record`）与 `markdown_summary`。 |

### 核心护栏与闭环进化机制

- **反空转与反 HOLD-churn**：除单周期 `stagnation` / `same_mechanism_spin` / `proxy_drift` 与跨周期 `macro_stagnation` / `pivot_churn` / `global_proxy_drift` 外，新增 **`hold_churn`**（连续 ≥3 次 HOLD 强制要求退出或 `suspend`）与 **`qualitative_numeric_mismatch`**（提醒数值门禁勿伪装定性）。
- **真正让 `max_trials=2..5` 跑起来**：跨周期 `macro_stagnation`/`pivot_churn` 不会在多步跑道刚跑完 2 条准备诊断、还没测真分时就提前腰斩；配合 `regression_severity="soft"`，实质路线可完整跑完探索跑道再统一复盘。
- **自动结果归因（Outcome Attribution）**：每轮周期结束时自动比对真分变化与跑道利用率，沉淀到 `project.track_record`；尤其对“总指挥 `dissent` 后人类强行覆盖”的路线，自动判定 **`dissent_vindicated`（异议被证成）** 或 **`dissent_refuted`（异议被推翻）**，让总指挥的判断力可量化追踪。
- **瘦身三层记忆包 + 原生 Schema**：压缩冗余全量路线表、过滤空字段、`--json-schema` 强制输出，降低 token 与延迟，让复盘更聚焦主矛盾。

---

## 四、故障处置与审计报表

- **唯一运行入口**：统一使用原生 `~/.local/bin/agy`，禁用 `/snap/bin/agy`。
- **超时与档位**：常规问答、`directive` 反方检查、解封复查优先 `effort="medium"`；重大宏观换路可用 `high` 并留 `180–240` 秒。
- **查看审计与切片对比**：
  ```bash
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py --days 14
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py summary --after-line 227 --project xduckcraft
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py feedback <invocation_id> good_dissent --note "提前拦住低效路线"
  ```

基础传输规范演化自[上游 dbc9ad2](https://github.com/fishing-dev-sm/pi-agy_advise/commit/dbc9ad2954797cd0a95ccfac4de6440839641de8)。
