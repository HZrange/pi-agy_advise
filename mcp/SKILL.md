---
name: agy-advise
description: 用本机 Codex 的 agy_advise MCP 工具向 Google Antigravity 请求跨项目的第一性原理决策辅助与战略治理。定位是敢提反对意见、跳出局部最优解、并可在白名单只读文件下核查一手代码与评测 JSON 的远程 AI 总指挥。
---

# Antigravity Advisor (`agy-advise`) — 第一性原理远程 AI 总指挥

通过本机注册的 `agy_advise` MCP 工具，让 Google Antigravity 担任**独立的第一性原理决策辅助者与全局技术总指挥**：它的核心职责不是走流程盖章，而是**挑战前提、识别真实根因类别、揭露局部最优与代理指标陷阱，并在必要时通过白名单只读文件直接核验一手代码与评测 JSON**。

默认在零工具临时目录沙箱运行；当显式传入 `readonly_files` 白名单时，全部文件先经本地敏感/二进制/大小预校验：小体量文本（单文件 ≤24KB 且总量 ≤48KB）自动带行号内联进只读证据块（单回合零工具往返，延迟立降 50%+）；更大文件自动复制为 `0o444` 只读快照放入隔离临时目录，由 `--settings` 禁止写/执行/联网与 `PreToolUse` 白名单钩子双重硬拦截——严禁修改文件、执行命令、联网或读取白名单之外的任何路径。

本 Skill 提供两层协同能力：
1. **全局问答模式（默认，零配置适用任意项目）**：单次提问或短追问，先质疑前提与局部最优、区分四类根因（实现 bug / 数据覆盖 / 优化失配 / 约束或容量上限），再给出高杠杆行动与最便宜证伪测试。
2. **项目总指挥模式（`project` 模式）**：持久化保存目标、冻结门槛、动态预算包络、分级置顶因果记忆与闭环结果归因；**凡涉及人类变更方向/路线/假设/解封，代码级强制触发一次短红队预验尸（Pre-mortem）**——无硬伤则附最便宜证伪测试与护栏自动放行，有实质异议则亮红牌呈报人机对齐。

---

## 一、什么时候问（以及什么时候挂 `readonly_files`）

- **方向与前提判断**：架构取舍、路线分叉、连续 2 轮无真实门禁进展、准备投入高算力/大改动前。
- **人类变更方向（强制短红队）**：负责人拍板新路线、换假设大类或解封 `HOLD` 时，用 `project.action="directive"` 同步最新预算并自动跑一次短红队预验尸（不可跳过；只有纯预算数字同步且 `unblock=false` 可跳过）。
- **挂载 `readonly_files` 白名单做一手核验**：当出现以下场景时，主动把最关键的 **1–6 个**源码文件或评测 JSON 传进 `readonly_files`（上限 12 个、单文件 ≤512KB、总量 ≤2MB）：
  1. 怀疑问题属于实现/接口 bug（如奖励 0 触发、门控/归一化失配、训练与评测脚本不一致）；
  2. 指标出现反直觉跷跷板或复盘存在核心分歧，需要总指挥直接核实真实评测 JSON 与代码逻辑，避免“口述转述失真”；
  3. 人类重大方向变更（`directive`）或 `red_team` 红队审计前，让顾问核对关键约束与实现前提。
- **账本与问答边界（严禁旁路旧账本）**：若当前活跃 `project` 阶段的冻结门禁已不再代表真实目标，**禁止把满屏红灯告警的旧账本晾在 `active` 状态却反复用 `qa:ask(stance="tactical")` 绕过治理**——应先用 `project.action="suspend"` 正式归档旧阶段，再以 `qa:ask(global/red_team)` + `readonly_files` 自由探索，或另起一个带明确数值门禁的新 `phase`。
- **严禁调用 / 严禁传敏感文件**：读普通文件、查签名、步数/批次算术对账、SHA 归档全部由本地 Codex 自行解决；**严禁**向 `readonly_files` 传入 `.env`、密钥、凭据、`auth.json`、`~/.ssh`、`~/.gemini` 或数据库/日志原件（服务端也会在本地直接拦截）。

---

## 二、全局问答模式（即时咨询）

自由问答已内置**紧凑输出与延迟预算**（目标 ≤1,200 字、最多 4 段：前提/异议核查 → 根因归类与 `file:line` 证据 → 至多 2 个高杠杆动作与最便宜证伪测试 → 止损/换路条件），避免 5,000+ 字长文和接近 240s 超时的过度空想。

| 参数 | 说明 |
| --- | --- |
| `question` | 每次聚焦一个核心判断：写清终局目标、硬约束、已观察到的失败与待裁决点。首问须自包含，禁止诱导顾问在两个局部小补丁里二选一。 |
| `context` | 可省略。提供高信噪比结构化事实（目标 / 约束 / 已试路径与实测 / 关键未知），禁止粘贴冗长日志、未授权机密或 Codex 内部推理。 |
| `stance` | 默认 `"global"`：<br>• `global`（默认）：第一性原理总指挥视角，具备明确的反顺从与异议权；探索期与诊断问题优先使用，若证据指向局部最优或更优宏观路径，直接先亮出 **Dissent / Counter-Case**。<br>• `red_team`：纯反方红队审计，专门构造当前方案会翻车的最强论据、暴露代理陷阱，并给出最便宜证伪检查。<br>• `tactical`：仅大方向已验证且只需窄范围局部技术方案时使用；**严禁用 `tactical` 旁路活跃项目账本的门禁复盘**，若发现局部微操陷阱仍会直言反对。 |
| `readonly_files` | 可选白名单文件路径列表（仅限授权分享的只读代码/配置/评测 JSON，最多 12 个、单文件 ≤512KB、总计 ≤2MB；单文件 ≤24KB 且总计 ≤48KB 自动带行号内联，零工具单回合完成）。开启后更大文件仅允许顾问 `read_file` 读取隔离只读快照，所有写文件、命令、联网及白名单外读取均被双重硬拒绝。 |
| `session_id` | 首问、换主题、换项目或更换 `readonly_files` 时省略；继续同一判断时传入上次 ID，仅补新证据。 |
| `timeout_seconds` | `1–240` 秒，默认 `240` 秒。 |
| `effort` / `model` | 新会话可选推理档位（`low`/`medium`/`high`/`xhigh`/`max`）与模型；未传模型读取 `AGY_ADVISE_MODEL` 或 CLI 默认设置。 |

---

## 三、项目总指挥模式（`project` 模式）

在同一 `agy_advise` 工具中传入 `project`，以 `(id, phase)` 标识项目阶段；`review` 与 `directive` 同样支持同时传入顶层 `readonly_files` 让总指挥直接核查一手代码与评测 JSON。

### 支持的动作（`project.action`）

| `action` | 作用与核心字段 |
| --- | --- |
| `init` | 初始化阶段：`objective`、`frozen_gate`、`route`，可选 `domain`、`non_negotiables`（真正不可妥协的物理/安全红线）、`budget_envelope` / `budget_note`（**可随指令动态更新的阶段预算，不要写死进 `non_negotiables`**）、`initial_facts`（自动置顶进 L2 因果记忆）。<br>• **凡有明确数值目标（如 `6/8`、`7/8`、`<= 80ms`）必须同时传 `score_name`、`baseline_score`、`pass_score`**（可选 `score_direction="higher"\|"lower"`、`min_improvement`），不要用定性模式掩盖数值门禁。<br>• 纯定性里程碑可省略分数字段，但只有 `evidence_kind="gate"` 的真实门禁进展才会计为推进。 |
| `directive` | **人类拍板 / 变更方向 / 重批预算专用通道**：传 `directive`（人类指令摘要）、可选 `budget_envelope` / `budget_note`、`route`、`hypothesis_family`、`max_trials`（默认 `3`）、`allow_exploration_regressions`、`direction_change`、`unblock`（默认 `true`）、`override_dissent`（默认 `false`）、`run_advisor_check`（默认 `true`）。<br>• **方向变更强制短红队检查（代码级强制）**：只要涉及变更 `route` / `hypothesis_family` / `max_trials`、解封 `HOLD`（`unblock=true`）或显式 `direction_change=true`，**必须**执行短红队预验尸（传 `run_advisor_check=false` 会被本地直接拒绝）；仅纯预算数字同步且 `unblock=false` 允许本地零 LLM 落账。<br>• 先实时更新 L1 的 `budget_envelope`，再跑紧凑型短红队预验尸（默认 `effort="medium"`，输出 `root_cause_class`、`fatal_assumptions`、`cheapest_falsifier`、`evidence_citations`）：<br>  - `concur` / `caution`：附上风控护栏与最便宜证伪测试，**立即自动激活放行（`dispatch_allowed=true`）**；<br>  - `dissent`：总指挥亮红牌（`dissent_points`、`cheapest_falsifier`、`alternative_path`）并暂停派发，Codex 必须先把反对意见呈报人类；人类若看后仍坚持原决定，调一次 `activate(decision_id=...)` 立刻放行并自动记 `human_overrode_dissent=true` 供事后胜率追踪。 |
| `report` | 上报已结束尝试：`trial_id`、`route`、`cycle`、`mechanism`、`evidence`。<br>• 数值项目仅真实端到端门禁评测填 `score`；代理/诊断留空 `score` 并标 `evidence_kind: "proxy" \| "diagnostic"`。<br>• **分级回归 `regression_severity`**：默认 `"hard"`（触碰不可妥协红线，立即拉停）；在 `max_trials=3..5` 探索跑道内的中间候选瑕疵/次级退步标 `"soft"`，记录在案但不提前腰斩跑道。<br>• **`HOLD` 状态下严禁上报普通试次**：核验动作必须传 `unblock_check: true` 记入独立核验区（不污染主试次统计），或用 `directive` / `review` 解除 `HOLD`，或用 `suspend` 归档旧阶段并单列新阶段。<br>• 多路并行回收时设 `auto_review: false` 批量入账，最后统一 `review`。 |
| `review` | 主动触发无菌室第一性原理全局复盘（采用原生 `--json-schema` 强制结构化输出，包含 `root_cause_class`、`contrarian_check`、`escape_hatch` 与 `evidence_citations`；重大分歧时建议附 `readonly_files`）。 |
| `activate` | 用当前 `decision_id` 激活 `CONTINUE` / `PIVOT`，或确认覆盖 `DISSENT`（标记 `human_overrode_dissent=true`）并恢复 `dispatch_allowed`。 |
| `suspend` | **正式冻结/归档停滞或被替代的阶段**：传 `reason` 与可选 `superseded_by`，将阶段置为 `suspended`（彻底终结无意义的连续 `HOLD` 循环）。 |
| `memory` | 维护分级 L2 记忆（不调 LLM）：<br>• `pin_facts`：**置顶跨轮物理/架构因果硬规律**（上限 10 条，绝不被普通滚动事实挤出失忆）；<br>• `add_facts`：普通滚动因果事实；<br>• `governance_notes`：签收/分账/预算等行政纪要（独立 4 条槽位，不挤占技术因果记忆）；<br>• `remove_facts` / `route_postmortems`。 |
| `feedback` | **MCP 内置事后质量打标**（无需敲终端 CLI）：传 `invocation_id`、`label`（`helpful` / `good_dissent` / `rubber_stamp` / `missed_root_cause` / `false_alarm` / `bad_format`）与 `note`。 |
| `status` / `list` | 仅读本地状态；`status(include_history=true)` 导出完整三层记忆、闭环结果归因（`track_record`）与 `markdown_summary`。 |

### 核心护栏与闭环进化机制

- **第一性原理四类根因判别**：复盘与方向预验尸均要求先明确区分 `implementation_bug`（修代码）、`data_coverage`（补态/补示范）、`optimization_mismatch`（改目标/算法）与 `constraint_or_capacity`（硬约束或容量上限冲突），杜绝把代码 bug 或约束矛盾误当成“继续加训练步数”。
- **反空转、反 HOLD-churn 与反软异议逃逸**：除单周期与跨周期护栏外，内置 **`hold_churn`**（连续 ≥3 次 HOLD）、**`directive_churn`**（连续 ≥3 次 `directive` 换路且 0 真实门禁）与 **`qualitative_numeric_mismatch`**；当项目已陷入空转且顾问在 `directive` 中写出了实质 `dissent_points` 却客气地标了 `caution` 时，代码层会自动升级为 **`DISSENT`** 并暂停派发（`auto_escalated_dissent=true`），杜绝“软异议悄悄放行”。
- **真正让 `max_trials=2..5` 跑起来**：在多步跑道内，第 1 条探索/可行性诊断不会被旧路线的 `global_proxy_drift` 或 `macro_stagnation` 提前腰斩；配合 `regression_severity="soft"`，实质路线可完整跑完探索跑道再统一复盘。
- **自动结果归因（Outcome Attribution）**：每轮周期结束时自动比对真分变化与跑道利用率，沉淀到 `project.track_record`；对“总指挥 `dissent` 后人类强行覆盖”的路线，自动判定 **`dissent_vindicated`（异议被证成）** 或 **`dissent_refuted`（异议被推翻）**。

---

## 四、故障处置与审计报表

- **唯一运行入口**：统一使用原生 `~/.local/bin/agy`，禁用 `/snap/bin/agy`。
- **SSH 终端误触发重新授权（GNOME Keyring 复用）**：`agy 1.5.16` 检测到 `SSH_CONNECTION`/`SSH_CLIENT`/`SSH_TTY` 时会改走文件凭证而漏掉已解锁的本机 GNOME 钥匙串。`server.py` 已在 Linux 本机存在 `/run/user/<uid>/bus` 时自动清理子进程 SSH 环境变量并补齐 `DBUS_SESSION_BUS_ADDRESS`（如需强制走文件凭证可设 `AGY_ADVISE_KEEP_SSH_ENV=1`）。在 SSH 终端手动运行 `agy` 时同样可用：
  ```bash
  env -u SSH_CONNECTION -u SSH_CLIENT -u SSH_TTY \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" \
    ~/.local/bin/agy
  ```
- **超时与档位**：快速合理性核查可用 `effort="low"`；常规问答、`directive` 短红队检查、解封复查优先 `effort="medium"`；重大宏观换路或挂载超过 48KB 的大型 `readonly_files` 深度核查可用 `high` 并留 `180–240` 秒。
- **查看审计与切片对比**：
  ```bash
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py --days 14
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py summary --after-line 227 --project xduckcraft
  python3 ~/.codex/skills/agy-advise/scripts/audit_report.py feedback <invocation_id> good_dissent --note "提前拦住局部最优陷阱"
  ```

基础传输规范演化自[上游 dbc9ad2](https://github.com/fishing-dev-sm/pi-agy_advise/commit/dbc9ad2954797cd0a95ccfac4de6440839641de8)。
