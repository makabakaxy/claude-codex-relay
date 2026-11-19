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

Reading history needs no extra dependencies.

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

## Known limits

- Created sessions run with full permissions by default (Codex `danger-full-access` + no approvals, Claude Code `bypassPermissions`), since nobody is there to answer prompts. Tighten with `create codex --sandbox read-only` or `create cc --permission-mode plan`.
- Claude Code sessions must run in a directory Claude already trusts.
- `cancel` works for Codex only; Claude Code exposes no turn interrupt.
- Request receipts in `~/.local/state/agent-relay/` keep full message text and answers and are never pruned; delete them when no longer needed.

## 中文说明

**痛点**：Claude Code 写完让 Codex review，Codex 提了意见再贴回给 Claude Code……你成了两个 AI 之间的复制粘贴工具人。

**`agent-relay` 是一个同时适用于 Claude Code 和 Codex 的 skill**，让一个 agent 可以：

- **读取**另一方的本地会话：把 Claude 的会话 ID 交给 Codex，它就能自己读到完整上下文
- **创建**另一方的持久会话并下发任务，**多轮发消息**、**等待**带回执确认的回答
- **取消**正在运行的 Codex 任务
- **跨两个产品搜索**历史会话

**默认全权限**：新建的会话默认 Codex `danger-full-access` + 免审批、Claude Code `bypassPermissions`（后台没人点审批）；只做 review 时用 `--sandbox read-only` 或 `--permission-mode plan` 收紧。

**安装**：按上面 [Install](#install) 一节 `git clone` 到 skills 目录，装 `websocket-client` 并启动 `codex app-server daemon start`。之后直接对 agent 说：

> 用 agent-relay 新建一个 Codex 会话，先读取我当前会话 `<session-id>` 的上下文，再 review 未暂存的改动。

## License

MIT
