# agy-advise-mcp

`agy-advise-mcp` 是 [`pi-agy_advise`](../README.md) 的**可选 Python MCP 配套**。它与 Pi 的 TypeScript 扩展共享同一个本机原生 `agy` CLI，面向 Codex 和其他 MCP 客户端，定位为**敢提反对意见、跳出局部最优解的远程 AI 总指挥**。

它不是 Pi TypeScript 扩展的替代品，也不会随 Pi 扩展自动加载。只使用轻量问答时，无需安装这里的 Python 依赖。

## 两种配套实现的分工

- **Pi TypeScript 扩展**：轻量问答、审查立场、会话控制、模型与推理档位覆盖。
- **`agy-advise-mcp`**：全局问答之外，提供持久化项目决策核心、人类指令反方预验尸（`directive`）、阶段冻结（`suspend`）、分级置顶 L2 因果记忆、原生 `--json-schema` 结构化裁决、闭环结果归因（`track_record`）与可切片调用审计。

两种实现使用同一个 `agy_advise` 工具名，但不共享会话、SQLite 项目状态或审计日志。

## 核心能力

- **全局指挥与反顺从异议**：默认 `global` / `red_team` 视角主动挑战前提与局部最优解；即使是人类拍板的 `directive` 也会默认做一次快速反方预验尸（`concur` / `caution` 自动生效免盖章往返，`dissent` 亮红牌呈报异议并允许人类显式覆盖）。
- **动态预算与宪法解耦**：不可妥协红线（`non_negotiables`）与可重批阶段预算（`budget_envelope`）分离，杜绝 L1 状态与最新预算自相矛盾。
- **反空转与反 HOLD-churn**：拦截 `HOLD` 内影子试次（核验走独立 `unblock_check`）、提供 `suspend` 归档终态、分级 `regression_severity="hard"|"soft"`、修复跨周期护栏误杀 `max_trials=3..5` 跑道，以及封堵定性模式伪装数值门禁。
- **不失忆的分级 L2 记忆与瘦身 Prompt**：`pin_facts` 置顶核心因果规律永不被 FIFO 挤出，`governance_notes` 独立存放行政纪要；配合原生 `agy --json-schema` 与 `--disable-slash-commands`，显著压缩输入/输出噪声。
- **闭环结果归因 + MCP 内置反馈**：自动追踪每轮周期的真分兑现率、跑道利用率与 `dissent_vindicated / dissent_refuted`，支持 `action: "feedback"` 直接在 MCP 内打标，以及 `audit_report.py --since / --after-line` 切片审计。

## 前置条件

- Python 3.10 或更高版本。
- 已安装并登录原生 [Antigravity CLI](https://antigravity.google/docs/cli/install/)（当前在 Python 3.10 与 agy 1.5.16 验证）。

先确认原生无交互调用可用：

```bash
~/.local/bin/agy --print-timeout 30s --print "Reply with exactly: OK"
```

## 安装

```bash
git clone https://github.com/fishing-dev-sm/pi-agy_advise.git
cd pi-agy_advise/mcp
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Codex 的 MCP 配置示例（将两个绝对路径替换为本机路径）：

```toml
[mcp_servers.agy-advisor]
command = "/absolute/path/to/pi-agy_advise/mcp/.venv/bin/python"
args = ["/absolute/path/to/pi-agy_advise/mcp/scripts/server.py", "/home/USER/.local/bin/agy"]
tool_timeout_sec = 270
```

如需安装调用规范 Skill，可将本目录链接到 Codex 的 Skill 目录；不要覆盖已有安装：

```bash
ln -s /absolute/path/to/pi-agy_advise/mcp ~/.codex/skills/agy-advise
```

## 验证

在本目录运行：

```bash
.venv/bin/python -m unittest discover -s scripts -p 'test_*.py' -v
```

测试使用假 CLI 和临时数据库，不需要真实模型调用，也不会写入正式项目账本。

## 数据与隐私

默认状态目录为 `~/.local/share/codex-agy-advise/`：

- `projects.sqlite3`：项目目标、预算包络、试次、核验记录、分级记忆、裁决与闭环归因。
- `invocations.jsonl`：以 `0600` 权限写入的调用元数据（含 `model_configured`、`prompt_chars`、`directive_alignment`）与截断预览。
- `feedback.jsonl`：人工或 MCP 内置记录的质量反馈。

环境变量：`AGY_ADVISE_DB`、`AGY_ADVISE_LOG`、`AGY_ADVISE_LOG_FULL=1`、`AGY_ADVISE_MODEL`。

仅显式提交的 `question`、`context` 和项目复盘包会发送给 Google。顾问 CLI 保持现有安全权限：在临时目录中运行，文件、命令、浏览器和 MCP 工具均被禁用。不要发送密钥、凭据或未授权敏感内容。

## 审计与反馈

```bash
python3 scripts/audit_report.py --days 14
python3 scripts/audit_report.py summary --after-line 227 --project xduckcraft
python3 scripts/audit_report.py feedback <invocation_id> good_dissent --note "提前拦住局部最优陷阱"
```

完整参数、项目动作与故障处置见 [`SKILL.md`](SKILL.md)。许可证见根目录的 [MIT License](../LICENSE)。
