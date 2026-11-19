# claude-codex-relay

**Let Claude Code and Codex talk to each other directly — stop being the copy-paste messenger between them.**

让 Claude Code 和 Codex 直接对话，不用再当人肉传声筒。

[English](#what-it-does) | [中文](#中文说明)

## What it does

`agent-relay` is an agent skill (works in both Claude Code and Codex) that lets one agent:

- **read** any local Claude Code or Codex session — hand Codex your Claude session ID and it has the full context
- **create** a persistent session in the other product and give it a task
- **send** follow-up messages and **wait** for a confirmed answer
- **cancel** a running Codex turn
- **search** session history across both products

```text
Claude Code ──create/send──▶ Codex (gpt-*)          Codex ──create/send──▶ Claude Code
     ▲                          │                       ▲                       │
     └──── confirmed answer ────┘                       └─── confirmed answer ──┘
```

## No security bypass needed

It does **not** bypass any sandbox or approval. It only uses each product's official interfaces:

- Codex: the shared `codex app-server` daemon (`turn/start`, `turn/steer`, `turn/interrupt`)
- Claude Code: `claude --bg` background sessions and the cross-session inbox

What this does and does not guarantee:

- **Permissions belong to the receiver.** A delegated task runs with the receiving agent's own sandbox and approval settings, not the sender's. Codex sessions are created with an explicit `danger-full-access` sandbox and `never` approvals, independent of your Codex config, because a relayed session has nobody to answer approval prompts and often needs to reach other agents. Use `create codex --sandbox read-only` (or `workspace-write`) for review-only work; the receipt records the effective `sandbox` and `approval_policy`. Claude Code sessions are likewise created with an explicit `--permission-mode bypassPermissions` (override with `create cc --permission-mode acceptEdits|plan|...`); if your organization disables bypass mode, creation fails with Claude's error instead of silently downgrading.
- **Messages are labeled as coming from a peer agent**, not from you. The receiver is told to apply its own judgment and ask you before destructive or out-of-scope actions. That is an instruction to the model, not an enforcement boundary — a compromised sender can still send a malicious request, so keep sensitive sessions on restrictive settings.
- **Inbound settings are not changed.** agent-relay never turns on auto-accept for cross-session messages.

## Example

In Claude Code:

> Use agent-relay to create a Codex session in this repo. Tell it to first run `agent-relay get <this-session-id>` for context, then do an adversarial review of the unstaged diff.

```bash
agent-relay create codex --cwd ~/code/my-app "Run 'agent-relay get <claude-session-id>' for context, then review the unstaged diff."
agent-relay wait <request-id> --timeout 300
agent-relay send <codex-session-id> "Apply fix #1 and rerun the tests"
agent-relay cancel <request-id>
```

## Install

Requirements: macOS (tested; Linux untested), Python 3.9+, and the Claude Code and/or Codex CLI.

```bash
# Claude Code
git clone https://github.com/makabakaxy/claude-codex-relay ~/.claude/skills/agent-relay
# Codex
git clone https://github.com/makabakaxy/claude-codex-relay ~/.agents/skills/agent-relay

# CLI on PATH (optional)
ln -s ~/.claude/skills/agent-relay/scripts/agent-relay ~/.local/bin/agent-relay

# Needed to control Codex sessions
python3 -m pip install websocket-client
codex app-server daemon start
```

Reading history needs no extra dependencies. Messaging a Claude Code session requires that session to be running and the directory to be trusted.

## Commands

| Command | Purpose |
|---|---|
| `list [cc\|codex\|all] [--project P] [--query Q] [--since/--until]` | Search sessions |
| `get <id> [--around TEXT] [--tools] [--all]` | Read a session as Markdown |
| `models [cc\|codex]` | List available models |
| `create cc\|codex [--model M] [--cwd DIR] [--sandbox MODE \| --permission-mode MODE] "task"` | Start a persistent session (`--sandbox`: Codex, `--permission-mode`: Claude Code) |
| `send <session-id> "msg" [--no-wait] [--timeout S]` | Message an existing session |
| `wait <request-id> [--timeout S]` | Wait for an answer without resending |
| `cancel <request-id>` | Interrupt the running Codex turn |

See [SKILL.md](SKILL.md) for full semantics (receipts, statuses, ownership rules).

## vs. the official Codex plugin for Claude Code

| | Codex plugin | agent-relay |
|---|---|---|
| Session lifetime | Tied to the current Claude session | Independent, persistent, resumable from either side |
| Direction | Claude → Codex only | Both directions |
| Interaction | One-shot task, output returned | Multi-turn, request IDs with delivery receipts |
| Shared context | Prompt only | Either side can read the other's full transcript |
| Built-in review gate | Yes | No (ask for a review in the task) |

## Privacy

The relay itself is local: it talks to the agents over local Unix sockets and the CLIs, and sends nothing anywhere. The agents still send their prompts — including anything relayed to them — to their model providers as usual.

Request receipts in `~/.local/state/agent-relay/` (`0700` directory, `0600` files) keep the full message text and answers and are never pruned automatically; delete them when no longer needed. Source session logs are never modified.

## Known limits

- Claude Code background sessions have no inbox. Follow-up `send`s stop the idle session and continue it with `claude --bg --resume`, so each follow-up restarts that process.
- Claude Code sessions must run in a directory Claude already trusts.
- `cancel` works for Codex only; Claude Code exposes no turn interrupt.

## 中文说明

**痛点**：Claude Code 写完让 Codex review，Codex 提了意见再贴回给 Claude Code……你成了两个 AI 之间的复制粘贴工具人。

**`agent-relay` 是一个同时适用于 Claude Code 和 Codex 的 skill**，让一个 agent 可以：

- **读取**另一方的本地会话：把 Claude 的会话 ID 交给 Codex，它就能自己读到完整上下文
- **创建**另一方的持久会话并下发任务，**多轮发消息**、**等待**带回执确认的回答
- **取消**正在运行的 Codex 任务
- **跨两个产品搜索**历史会话

**不需要绕过安全限制**：只走两个产品的官方接口（Codex app-server daemon、Claude Code 后台会话与跨会话收件箱），不会修改任何入站设置。

- **权限由接收方决定**：任务按接收方自己的沙箱和审批配置执行，不会继承发送方的限制。Codex 会话默认显式使用 `danger-full-access` + 免审批（不依赖每个人的 Codex 配置，因为转发的会话没人点审批）；只做 review 时用 `--sandbox read-only` 收紧；Claude Code 会话同理默认 `--permission-mode bypassPermissions`，可用 `--permission-mode` 收紧。回执里会记录实际生效的权限。
- **消息标注为来自另一个 agent**，而不是来自你；接收方会被要求自行判断，危险或超出任务范围的操作先问你。这是对模型的提示，不是强制隔离。
- **转发通道在本机**（本地 socket 和 CLI），但两个 agent 照常把提示词发给各自的模型服务。回执会保存消息原文和回答，不会自动清理。

**安装**：按上面 [Install](#install) 一节 `git clone` 到 skills 目录，装 `websocket-client` 并启动 `codex app-server daemon start`。之后直接对 agent 说：

> 用 agent-relay 新建一个 Codex 会话，先读取我当前会话 `<session-id>` 的上下文，再 review 未暂存的改动。

## License

MIT
