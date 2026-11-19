# claude-codex-relay

**Let Claude Code and Codex talk to each other directly — stop being the copy-paste messenger between them.**

让 Claude Code 和 Codex 直接对话，不用再当人肉传声筒。

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

Each agent keeps its own permissions, sandbox and approval prompts. Messages are wrapped with a request ID and treated as a user-requested task, not as elevated instructions.

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
| `create cc\|codex [--model M] [--cwd DIR] "task"` | Start a persistent session |
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

Everything stays local. Request receipts are stored in `~/.local/state/agent-relay/` (`0700`). Source session logs are never modified.

## License

MIT
