# agy-advise-mcp

`agy-advise-mcp` 是 [`pi-agy_advise`](../README.md) 的**可选 Python MCP 配套**。它与 Pi 的 TypeScript 扩展共享同一个本机原生 `agy` CLI，面向 Codex 和其他 MCP 客户端，定位为**第一性原理驱动、敢提反对意见、可白名单只读查证代码与评测 JSON 的远程 AI 总指挥**。

它不是 Pi TypeScript 扩展的替代品，也不会随 Pi 扩展自动加载。只使用轻量问答时，无需安装这里的 Python 依赖。

## 两种配套实现的分工

- **Pi TypeScript 扩展**：轻量问答、审查立场、会话控制、模型与推理档位覆盖。
- **`agy-advise-mcp`**：全局问答之外，提供持久化项目决策核心、人类方向变更**强制短红队预验尸**（`directive`）、白名单只读文件一手核验（`readonly_files`）、阶段冻结（`suspend`）、分级置顶 L2 因果记忆、原生 `--json-schema` 结构化裁决、闭环结果归因（`track_record`）与可切片调用审计。

两种实现使用同一个 `agy_advise` 工具名，但不共享会话、SQLite 项目状态或审计日志。

## 核心能力

- **第一性原理指挥与强制方向红队**：默认 `global` / `red_team` 视角先做四类根因判别（`implementation_bug` / `data_coverage` / `optimization_mismatch` / `constraint_or_capacity`）并主动质疑局部最优；**凡人类变更方向、路线、假设大类或解封 `HOLD`，`directive` 在代码层强制运行短红队预验尸**（`concur`/`caution` 自动附护栏与最便宜证伪测试放行，`dissent` 亮红牌暂停呈报并支持人类显式覆盖）。
- **智能内联 + 三层隔离的白名单只读文件取证（`readonly_files`）**：支持显式传入最多 12 个本地代码/配置/评测 JSON（单文件 ≤512KB、总量 ≤2MB）：
  1. 本地预校验拦截目录、二进制及 `.env`/密钥/凭据/`~/.ssh`/`~/.gemini`/账本数据库；
  2. **小文件自动带行号内联**（单文件 ≤24KB 且总计 ≤36KB，零工具单回合完成，消除多轮 `read_file` 往返延迟；可通过 `AGY_ADVISE_INLINE_READONLY=off` 强制走快照模式）；
  3. **大文件三层快照钩子隔离**：复制为 `0o444` 只读快照到独立临时目录 `readonly_evidence/`（`0o555`），启用 `--settings` 禁止写/执行/联网与 `.agents/hooks.json` 失败关闭型 `PreToolUse` 钩子，**仅放行对精确快照路径的 `read_file`/`view_file`**，并将取证模式、实读文件与拦截记录写入审计日志。
- **动态预算与宪法解耦**：不可妥协红线（`non_negotiables`）与可重批阶段预算（`budget_envelope`）分离，杜绝 L1 状态与最新预算自相矛盾。
- **反空转、反 HOLD-churn 与反软异议逃逸**：拦截 `HOLD` 内影子试次（核验走独立 `unblock_check`）、提供 `suspend` 归档终态、分级 `regression_severity="hard"|"soft"`、新增 `directive_churn` 告警与反 `caution-creep` 自动升级机制（空转期 `caution` 夹带 `dissent_points` 自动升级为 `DISSENT` 暂停确认）、修复跨周期护栏误杀新跑道首试，以及封堵定性模式伪装数值门禁。
- **不失忆的分级 L2 记忆与原生 Schema**：`pin_facts` 置顶核心因果规律永不被 FIFO 挤出，`governance_notes` 独立存放行政纪要，配合原生 `agy --json-schema`、`--disable-slash-commands` 与更紧凑的 `directive` 记忆包。
- **闭环结果归因 + MCP 内置反馈**：自动追踪每轮周期真分兑现率、跑道利用率与 `dissent_vindicated / dissent_refuted`，支持 `action: "feedback"` 直接在 MCP 内打标，以及 `audit_report.py --since / --after-line` 切片审计。

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

其他 MCP 客户端可使用相同的 stdio 命令和参数。修改配置后重启对应客户端或 MCP 会话。

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
- `invocations.jsonl`：以 `0600` 权限写入的调用元数据（含 `model_configured`、`prompt_chars`、`directive_alignment`、`is_direction_change`、`readonly_files_staged`、`readonly_files_read`、`readonly_denied_calls`）与截断预览。
- `feedback.jsonl`：人工或 MCP 内置记录的质量反馈。

环境变量：`AGY_ADVISE_DB`、`AGY_ADVISE_LOG`、`AGY_ADVISE_LOG_FULL=1`、`AGY_ADVISE_MODEL`、`AGY_ADVISE_KEEP_SSH_ENV=1`（默认在 Linux 检测到 `/run/user/<uid>/bus` 时自动清理 `SSH_CONNECTION`/`SSH_CLIENT`/`SSH_TTY` 并补齐 `DBUS_SESSION_BUS_ADDRESS`，避免 SSH 会话误退回文件凭证导致重复登录；设为 `1` 可保留原始 SSH 环境）。

未传 `readonly_files` 时，顾问 CLI 在空临时目录中运行且所有工具（读/写/命令/浏览器/MCP/联网）均被禁用；传入 `readonly_files` 时，仅允许对白名单文件的隔离只读快照执行 `read_file`，写文件、命令、联网及白名单外读取均被双重拦截。不要传入密钥、凭据或未授权敏感内容。

## 审计与反馈

```bash
python3 scripts/audit_report.py --days 14
python3 scripts/audit_report.py summary --after-line 227 --project xduckcraft
python3 scripts/audit_report.py feedback <invocation_id> good_dissent --note "提前拦住局部最优陷阱"
```

完整参数、项目动作与故障处置见 [`SKILL.md`](SKILL.md)。许可证见根目录的 [MIT License](../LICENSE)。
