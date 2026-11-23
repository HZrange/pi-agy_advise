# pi-agy_advise

把本机的 [Antigravity CLI](https://antigravity.google/docs/cli)（`agy`）接成一个 Pi 工具：**`agy_advise`**。
Pi 在需要方案、设计意见或第二次审查时问它一句，agy 在自己的会话里读代码、给建议，把文本返回给 Pi。

Plan mode，只出主意，不改你的文件。

> A Pi extension that exposes the local `agy` (Antigravity CLI) as a read-only planning advisor.

## 为什么不是一个简单的 `agy -p`

每次都 `agy -p` 等于每轮从零开始：重新读文件、重新建立上下文、重复计费。本扩展改成**一个长驻的 `agy` 进程**讲 stream-json：

```
Pi ──agy_advise(question)──▶ 长驻 agy 进程（plan mode, stream-json）
                                  │  自己维护会话与 prompt cache
   ◀──────── 建议文本 ─────────────┘
```

- Pi 侧只发 `question`（可选一条 `context`），**不重传 Pi 的对话历史**。历史在 agy 那边。
- 连续提问复用同一个 `conversation_id`，agy 的 prompt cache 因此持续命中。实测同一会话第二轮：`cache_read_tokens: 32973 / total_tokens: 36090`（约 91% 命中）。
- 请求串行化，不会把 agy 的 stdin 写乱。
- 空闲 10 分钟自动回收进程；超时（默认 240s）或进程崩溃后，下次调用自动用原 `conversation_id` 续上。

## 安装

需要本机已安装并登录 `agy`（`agy --version` 能出结果即可）。

**方式一：放进全局扩展目录**

```bash
git clone https://github.com/fishing-dev-sm/pi-agy_advise
cp pi-agy_advise/index.ts ~/.pi/agent/extensions/
```

然后在 Pi 里 `/reload`。

**方式二：作为 Pi 包安装**

```bash
pi install git:github.com/fishing-dev-sm/pi-agy_advise
```

## 前置：给 agy 开权限白名单

headless 模式没法弹确认框。一旦 agy 想跑一条需要批准的命令（它常先 `find`/`ls` 看仓库结构），**整轮会被静默取消：进程 exit 0、status 仍是 SUCCESS、响应是空字符串**。这是本扩展最容易踩的坑，所以它把空响应当错误抛出，而不是返回空文本。

在 `~/.gemini/antigravity-cli/settings.json` 里加只读命令的 allow 规则：

```json
{
  "permissions": {
    "allow": [
      "command(find)", "command(ls)", "command(pwd)", "command(tree)",
      "command(wc)", "command(rg)",
      "command(git status)", "command(git log)", "command(git diff)",
      "command(git show)", "command(git ls-files)", "command(git rev-parse)"
    ],
    "deny": [
      "command(rm)", "command(sudo)", "command(dd)",
      "command(chmod)", "command(chown)", "command(mkfs)", "command(shutdown)"
    ]
  }
}
```

规则语法见官方 [Agent permissions](https://antigravity.google/docs/permissions)：形如 `action(target)`，`command` 按 token 前缀匹配，优先级 **Deny > Ask > Allow**。

两个必须知道的边界：

1. 这些是 **global scope** 规则，对交互式 `agy` 同样生效。
2. 规则只匹配命令名，管不住范围：`find` 允许 `find /`，`rg` 允许 `rg -uu /`。`deny` 也只是兜底——`bash -c "rm -rf x"` 的首 token 是 `bash`，不会命中 `command(rm)`。要硬隔离请开 `enableTerminalSandbox`。

## 用法

参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `question` | ✅ | 问题本身，必须自包含：agy 看不到 Pi 的对话历史 |
| `context` | — | agy 看不到、但回答必需的最小事实。省略可省钱 |
| `timeoutMs` | — | 单次问答超时，默认 240000 |
| `skipPermissions` | — | 传 `--dangerously-skip-permissions`。仅在没配 allow 规则时应急使用 |

在 Pi 里直接说人话即可，Pi 会自己决定何时调用，例如：

```
用 agy_advise 看下 /path/to/src/reconcile.py 的分层是否合理
```

返回内容是一段建议文本，同时在 `details` 里带回 `conversationId`、耗时和 token 用量。

## 怎么问才不浪费

agy 是本机最强的模型，**别把它当选项打分器**。工具的 `promptGuidelines` 里写死了这条约束，但提问的人（模型）仍要自觉：

- 不要先把问题坍缩成 A/B/C 再让它选。给目标、约束、已尝试、失败现象，让它自己划定方案空间。
- 不要先给结论再问“对不对”。那只会得到一个 yes。
- 不要把 Pi 自己的推理链粘进 `context`。只给 agy 看不到的事实。
- 事实类问题（某个函数有没有加锁）自己 `grep`，别花 100 秒让它重读。

配套的提问规范见 [`agy-advise` skill](https://github.com/fishing-dev-sm/pi-agy_advise)，含四段式模板与反模式对照。

另外，扩展会在每次提问末尾追加一句固定要求：若问题里已带候选方案，不要简单挑一个，而是自己推导最佳解并说明理由。这是对“被喂选择题”的对冲，不是给 agy 加人设。

## 行为与已知限制

- **plan 模式不等于强制只读**。`--mode plan` 的实现方式是展开 `/plan` 斜杠命令，agy 仍会往 `~/.gemini/antigravity-cli/brain/<conversation_id>/implementation_plan.md` 写计划文件；配合 `skipPermissions` 时它也可能改动工作区文件。本扩展不使用 `--dangerously-skip-permissions`，除非你显式传参。
- **`--mode plan` 与 `--disable-slash-commands` 互斥**。同时传入 agy 会警告 `--mode plan has no effect while slash command expansion is disabled`，plan 形同虚设。本扩展只传前者。
- **会话内不能发斜杠命令**。`/model`、`/usage` 之类在 stream-json 会话里会直接报错退出（exit 2）。
- **成本**：一次多文件分析实测约 12 万 token 以内、100 秒上下；简单问答 20–40 秒。agy 会话独立于 Pi，第一条问题务必自包含。
- **`agy` 的输出不会流式显示**，工具返回时一次性给出完整文本。

## 合规

本项目只做**本机单账号**的 CLI 调用：起 `agy` 子进程、读它的 stdout，不代理、不转发、不对外提供接口、不多账号轮换、不绕过任何限额。

Google 的 [Antigravity 服务条款](https://antigravity.google/terms)禁止把服务"用于非我们提供的产品"以及用第三方软件访问服务（例如用 OpenClaw 走 Antigravity OAuth）。用第三方 harness 驱动官方 headless CLI 属于该 CLI 的既有用途（`--output-format text` 就是默认格式），但把 `agy` 变成他人可用的后端服务不属于。使用时请自行确认符合你所在账号类型的条款。

另请注意官方对数据的说明：交互内容会被记录并可能被 Google 员工与承包商查看。不要把机密信息喂给它。

## License

MIT
