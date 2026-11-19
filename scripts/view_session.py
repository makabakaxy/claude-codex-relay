#!/usr/bin/env python3
"""Find, read, create, and message local Codex and Claude Code sessions."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MAX_MESSAGES = 120

INJECTED_BLOCKS = (
    "system-reminder",
    "local-command-caveat",
    "local-command-stdout",
    "command-name",
    "command-message",
    "command-args",
    "task-notification",
    "bash-input",
    "bash-stdout",
    "bash-stderr",
    "teammate-message",
    "environment_context",
)


@dataclass
class Message:
    role: str
    text: str
    timestamp: str = ""
    tools: list[str] = field(default_factory=list)


@dataclass
class Session:
    provider: str
    session_id: str
    path: Path
    cwd: str = ""
    started_at: str = ""
    branch: str = ""
    title: str = ""
    source: str = ""
    messages: list[Message] = field(default_factory=list)


def compact(value: Any, limit: int = 240) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def text_from_content(content: Any, allowed_types: set[str] | None = None) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        block_type = str(content.get("type", ""))
        if allowed_types is not None and block_type and block_type not in allowed_types:
            return ""
        for key in ("text", "input_text", "output_text"):
            value = content.get(key)
            if isinstance(value, str):
                return value.strip()
        return ""
    if isinstance(content, list):
        parts = [text_from_content(item, allowed_types) for item in content]
        return "\n".join(part for part in parts if part).strip()
    return ""


def strip_injected(text: str) -> str:
    """Remove harness blocks that are stored with user-role messages."""
    for tag in INJECTED_BLOCKS:
        text = re.sub(
            rf"<{re.escape(tag)}\b[^>]*>[\s\S]*?</{re.escape(tag)}>",
            "",
            text,
            flags=re.IGNORECASE,
        )
    return text.strip()


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def source_roots(provider: str) -> list[Path]:
    home = Path.home()
    if provider == "codex":
        override = os.environ.get("AGENT_RELAY_CODEX_DIR")
        if override:
            return [Path(override)]
        roots = [home / ".agents" / "sessions", home / ".codex" / "sessions"]
        if os.environ.get("CODEX_HOME"):
            roots.insert(0, Path(os.environ["CODEX_HOME"]) / "sessions")
        return list(dict.fromkeys(roots))
    return [Path(os.environ.get("AGENT_RELAY_CLAUDE_DIR", Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "projects"))]


def candidate_score(path: Path, provider: str, needle: str) -> tuple[int, int, str]:
    stem = path.stem
    if stem == needle:
        match = 0
    elif provider == "codex" and stem.endswith("-" + needle):
        match = 0
    elif stem.startswith(needle) or stem.endswith(needle):
        match = 1
    else:
        match = 2
    return match, len(stem), str(path)


def resolve_session(session_id: str, provider: str | None = None) -> tuple[str, Path]:
    """Resolve exact IDs before prefixes across providers, collapsing duplicate logs."""
    needle = session_id.removesuffix(".jsonl").lower()
    if not re.fullmatch(r"[a-f0-9-]{4,36}", needle):
        raise RuntimeError("Use a session UUID or a unique prefix of at least four characters")
    matches = {}
    for source in ([provider] if provider else ["codex", "cc"]):
        for root in source_roots(source):
            if not root.is_dir():
                continue
            for path in root.rglob("*.jsonl"):
                if needle not in path.stem.lower() or "subagents" in path.parts:
                    continue
                match = re.search(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$", path.stem.lower())
                identity = match.group() if match else path.stem.lower()
                if not identity.startswith(needle):
                    continue
                key = source, identity
                old = matches.get(key)
                if old is None or path.stat().st_mtime > old.stat().st_mtime:
                    matches[key] = path
    exact = {key: path for key, path in matches.items() if key[1] == needle}
    matches = exact or matches
    if not matches:
        raise RuntimeError(f"No local session matches {session_id!r}")
    if len(matches) != 1:
        raise RuntimeError("Ambiguous session ID; use a full ID or --provider:\n" +
                           "\n".join(f"  {source} {identity}" for source, identity in matches))
    (source, _), path = next(iter(matches.items()))
    return source, path


def find_session(provider: str, session_id: str) -> Path:
    return resolve_session(session_id, provider)[1]


def codex_tool(payload: dict[str, Any]) -> str:
    kind = str(payload.get("type", ""))
    if kind == "function_call":
        return f"{payload.get('name', '?')}({compact(payload.get('arguments', ''))})"
    if kind == "custom_tool_call":
        return f"{payload.get('name', '?')}({compact(payload.get('input', ''))})"
    return ""


def parse_codex(path: Path) -> Session:
    records = list(iter_jsonl(path))
    session = Session(provider="codex", session_id=path.stem, path=path)

    for record in records:
        if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
            continue
        payload = record["payload"]
        session.session_id = str(payload.get("id") or session.session_id)
        session.cwd = str(payload.get("cwd") or "")
        session.started_at = str(payload.get("timestamp") or record.get("timestamp") or "")
        git = payload.get("git")
        if isinstance(git, dict):
            session.branch = str(git.get("branch") or "")
        break

    response_messages: list[Message] = []
    pending_tools: list[str] = []
    for record in records:
        if record.get("type") != "response_item" or not isinstance(record.get("payload"), dict):
            continue
        payload = record["payload"]
        tool = codex_tool(payload)
        if tool:
            pending_tools.append(tool)
            continue
        if payload.get("type") != "message" or payload.get("role") not in ("user", "assistant"):
            continue
        role = str(payload["role"])
        text = text_from_content(payload.get("content"), {"input_text", "output_text", "text"}).strip()
        if role == "user":
            text = strip_injected(text)
        if not text:
            continue
        tools = pending_tools if role == "assistant" else []
        response_messages.append(Message(role, text, str(record.get("timestamp") or ""), list(tools)))
        if role == "assistant":
            pending_tools.clear()

    if response_messages:
        session.messages = response_messages
        return session

    # Older Codex logs may only expose display messages through event_msg.
    for record in records:
        if record.get("type") != "event_msg" or not isinstance(record.get("payload"), dict):
            continue
        payload = record["payload"]
        kind = payload.get("type")
        if kind not in ("user_message", "agent_message"):
            continue
        text = payload.get("message")
        if isinstance(text, str) and text.strip():
            role = "user" if kind == "user_message" else "assistant"
            cleaned = strip_injected(text) if role == "user" else text.strip()
            if cleaned:
                session.messages.append(Message(role, cleaned, str(record.get("timestamp") or "")))
    return session


def claude_tools(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    tools: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tools.append(f"{block.get('name', '?')}({compact(block.get('input', ''))})")
    return tools


def parse_claude(path: Path) -> Session:
    session = Session(provider="cc", session_id=path.stem, path=path)
    seen_ids: set[str] = set()

    for record in iter_jsonl(path):
        if record.get("type") == "custom-title" and isinstance(record.get("customTitle"), str):
            session.title = record["customTitle"]
        if record.get("type") == "user" and record.get("promptSource") in ("typed", "queued", "sdk"):
            source = "sdk" if record["promptSource"] == "sdk" else "interactive"
            session.source = source if not session.source or session.source == source else "mixed"
        if not session.cwd and isinstance(record.get("cwd"), str):
            session.cwd = record["cwd"]
        if not session.started_at and isinstance(record.get("timestamp"), str):
            session.started_at = record["timestamp"]
        if isinstance(record.get("gitBranch"), str) and record["gitBranch"]:
            session.branch = record["gitBranch"]

        role = record.get("type")
        if role not in ("user", "assistant") or record.get("isSidechain") is True:
            continue
        if role == "user":
            if record.get("isCompactSummary") is True:
                continue
            if "promptSource" in record and record.get("promptSource") not in ("typed", "queued", "sdk"):
                content = (record.get("message") or {}).get("content", "")
                if not (isinstance(content, str) and content.startswith("Another Claude session sent a message:")):
                    continue
        message_id = str(record.get("uuid") or "")
        if message_id and message_id in seen_ids:
            continue
        if message_id:
            seen_ids.add(message_id)

        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else message

        # Tool results are encoded as user records; only human text blocks belong in the transcript.
        text = text_from_content(content, {"text", "input_text", "output_text"}).strip()
        if role == "user":
            text = strip_injected(text)
            if text.startswith("Base directory for this skill:"):
                continue
        if role == "user" and record.get("toolUseResult") is not None and not text:
            continue
        tools = claude_tools(content) if role == "assistant" else []
        if text or tools:
            session.messages.append(Message(str(role), text, str(record.get("timestamp") or ""), tools))

    return session


def dedupe_consecutive(messages: list[Message]) -> list[Message]:
    result: list[Message] = []
    for message in messages:
        if result and message.role == result[-1].role and message.text == result[-1].text and message.tools == result[-1].tools:
            continue
        result.append(message)
    return result


def around(messages: list[Message], query: str, context: int) -> tuple[list[Message | None], int]:
    needle = query.casefold()
    hits = [
        index
        for index, message in enumerate(messages)
        if needle in message.text.casefold() or any(needle in tool.casefold() for tool in message.tools)
    ]
    if not hits:
        return [], 0
    indexes: set[int] = set()
    for hit in hits:
        indexes.update(range(max(0, hit - context), min(len(messages), hit + context + 1)))
    output: list[Message | None] = []
    previous = -2
    for index in sorted(indexes):
        if index != previous + 1:
            output.append(None)
        output.append(messages[index])
        previous = index
    return output, len(hits)


def bounded(messages: list[Message], maximum: int) -> tuple[list[Message | None], int]:
    if maximum <= 0 or len(messages) <= maximum:
        return list(messages), 0
    head = maximum // 2
    tail = maximum - head
    omitted = len(messages) - maximum
    return [*messages[:head], None, *messages[-tail:]], omitted


def render(session: Session, entries: list[Message | None], include_tools: bool, note: str = "") -> str:
    lines = [f"# {session.provider.upper()} session {session.session_id}", "", f"- Path: `{session.path}`"]
    if session.cwd:
        lines.append(f"- CWD: `{session.cwd}`")
    if session.branch:
        lines.append(f"- Branch: `{session.branch}`")
    if session.started_at:
        lines.append(f"- Started: {session.started_at}")
    lines.append(f"- Messages: {len(session.messages)}")
    if note:
        lines.extend(["", f"_{note}_"])

    for message in entries:
        if message is None:
            lines.extend(["", "---", "", "_Messages omitted._"])
            continue
        heading = "User" if message.role == "user" else "Assistant"
        lines.extend(["", f"## {heading}", ""])
        if message.text:
            lines.append(message.text)
        if include_tools and message.tools:
            lines.extend(["", "Tools:"])
            lines.extend(f"- `{tool}`" for tool in message.tools)
    return "\n".join(lines).rstrip() + "\n"


def timestamp_value(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        return 0.0


def time_boundary(value: str, end: bool = False) -> tuple[float, bool]:
    """Date-only upper bounds exclude next midnight; exact times are inclusive."""
    try:
        date_only = re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None
        if not date_only and not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            raise ValueError("expected YYYY-MM-DD or an ISO datetime")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date_only and end:
            parsed += timedelta(days=1)
        return parsed.timestamp(), date_only and end
    except (ValueError, OverflowError, OSError) as error:
        raise RuntimeError(f"Invalid time {value!r}: {error}") from error


def is_child_session(path: Path, provider: str) -> bool:
    if path.stem.startswith("agent-"):
        return True
    for record in iter_jsonl(path):
        if provider == "cc" and record.get("type") in ("user", "assistant"):
            return record.get("isSidechain") is True
        if provider == "codex" and record.get("type") == "session_meta":
            source = record.get("payload", {}).get("source")
            return isinstance(source, dict) and "subagent" in source
    return False


def project_paths(project: str | None, sessions: list[Session]) -> list[Path]:
    if not project:
        return []
    if "/" in project or project.startswith((".", "~")):
        return [Path(project).expanduser().resolve()]
    matches = sorted({str(Path(s.cwd)) for s in sessions if s.cwd and Path(s.cwd).name == project})
    if len(matches) > 1:
        raise RuntimeError("Project name is ambiguous; use a full path: " + ", ".join(matches))
    return [Path(p) for p in matches]


def list_sessions(args: argparse.Namespace) -> int:
    since = time_boundary(args.since)[0] if args.since else None
    until, exclusive_end = time_boundary(args.until, end=True) if args.until else (None, False)
    if since is not None and until is not None and (since > until or (exclusive_end and since == until)):
        raise RuntimeError("--since must not be later than --until")
    providers = ("codex", "cc") if args.provider == "all" else ("cc" if args.provider == "claude" else args.provider,)
    sessions: dict[tuple[str, str], Session] = {}
    warnings: list[str] = []
    empty = children = duplicates = 0
    for provider in providers:
        roots = [root for root in source_roots(provider) if root.is_dir()]
        if not roots:
            warnings.append(f"No {provider} session directory found")
        for root in roots:
            # Claude subagent logs live below each session directory.
            paths = root.glob("*/*.jsonl") if provider == "cc" else root.rglob("*.jsonl")
            for path in sorted(paths):
                try:
                    if is_child_session(path, provider):
                        children += 1
                        continue
                    session = parse_claude(path) if provider == "cc" else parse_codex(path)
                    session.messages = [m for m in dedupe_consecutive(session.messages) if m.text]
                    if not session.messages:
                        empty += 1
                        continue
                    key = (provider, session.session_id)
                    if key in sessions:
                        duplicates += 1
                        old = sessions[key]
                        old_time = max((timestamp_value(m.timestamp) for m in old.messages), default=0)
                        new_time = max((timestamp_value(m.timestamp) for m in session.messages), default=0)
                        if new_time <= old_time:
                            continue
                    sessions[key] = session
                except (OSError, ValueError, TypeError, AttributeError) as error:
                    warnings.append(f"Skipped {path}: {error}")

    selected_paths = project_paths(args.project, list(sessions.values()))
    rows = []
    query = args.query.casefold() if args.query else ""
    for session in sessions.values():
        if args.project:
            if not session.cwd or not any(Path(session.cwd) == p or p in Path(session.cwd).parents for p in selected_paths):
                continue
        matching = next((m.text for m in session.messages if query in m.text.casefold()), "") if query else ""
        if query and not matching:
            continue
        latest = max((timestamp_value(m.timestamp) for m in session.messages), default=0)
        time_source = "message"
        if not latest:
            try:
                latest = session.path.stat().st_mtime
            except OSError as error:
                warnings.append(f"Skipped {session.path}: {error}")
                continue
            time_source = "file_mtime"
        if since is not None and latest < since:
            continue
        if until is not None and (latest >= until if exclusive_end else latest > until):
            continue
        first = next((m.text for m in session.messages if m.role == "user"), "")
        snippet = ""
        if matching:
            position = matching.casefold().find(query)
            snippet = compact(matching[max(0, position - 60):], 180)
            if position > 60:
                snippet = "…" + snippet
        rows.append({
            "provider": session.provider, "session_id": session.session_id,
            "project": session.cwd, "path": str(session.path), "source": session.source or "unknown",
            "updated_at": datetime.fromtimestamp(latest, timezone.utc).isoformat(),
            "time_source": time_source, "title": session.title,
            "preview": compact(first or session.messages[0].text, 120), "match": snippet,
            "_time": latest,
        })
    rows.sort(key=lambda row: (row["_time"], row["provider"], row["session_id"]), reverse=True)
    total = len(rows)
    start = (args.page - 1) * args.page_size
    items = rows[start:start + args.page_size]
    for row in items:
        del row["_time"]
    result = {"total": total, "page": args.page, "page_size": args.page_size,
              "total_pages": (total + args.page_size - 1) // args.page_size,
              "skipped_empty": empty, "skipped_children": children, "duplicates": duplicates,
              "warnings": warnings, "sessions": items}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Sessions: {total} | Page {args.page}/{result['total_pages']} | Page size: {args.page_size}")
        print("\n| Source | Session ID | Updated (local timezone) | Project | Title / preview | Match |")
        print("|---|---|---|---|---|---|")
        for row in items:
            values = [row["provider"] + "/" + row["source"], row["session_id"],
                      datetime.fromisoformat(row["updated_at"]).astimezone().strftime("%Y-%m-%d %H:%M:%S %z") + (" (mtime)" if row["time_source"] == "file_mtime" else ""),
                      row["project"], compact(row["title"] or row["preview"], 120), row["match"]]
            print("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |")
        if not items:
            print("\nNo sessions on this page.")
        print(f"\nSkipped: {empty} empty, {children} child sessions; deduplicated: {duplicates}.")
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
    return 0


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def parse_args() -> argparse.Namespace:
    if sys.argv[1:2] == ["list"]:
        parser = argparse.ArgumentParser(description="List local sessions across projects, or search message text.")
        parser.add_argument("command", choices=("list",))
        parser.add_argument("provider", choices=("all", "codex", "cc", "claude"), default="all", nargs="?")
        parser.add_argument("--project", help="Project directory name or full path; includes subdirectories")
        parser.add_argument("--query", help="Case-insensitive search of user and assistant text, excluding tool output")
        parser.add_argument("--since", help="Latest activity on/after this date or ISO datetime; timezone defaults to local")
        parser.add_argument("--until", help="Latest activity on/before this date or ISO datetime; dates include the whole day")
        parser.add_argument("--page", type=positive_int, default=1)
        parser.add_argument("--page-size", type=positive_int, default=20)
        parser.add_argument("--json", action="store_true", help="Print structured results with full IDs and paths")
        return parser.parse_args()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.epilog = "To list or search sessions: view_session.py list [all|codex|cc] --help"
    parser.add_argument("command", choices=("get",))
    parser.add_argument("session_id", help="Full session ID or a unique prefix")
    parser.add_argument("--provider", choices=("codex", "cc", "claude"))
    parser.add_argument("--tools", action="store_true", help="Include summarized assistant tool calls")
    parser.add_argument("--around", metavar="TEXT", help="Show matching messages with nearby context")
    parser.add_argument("--context", type=int, default=2, help="Messages around each --around hit (default: 2)")
    parser.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES, help="Bounded output size")
    parser.add_argument("--all", action="store_true", help="Print every normalized message")
    parser.add_argument("--raw", action="store_true", help="Print the original JSONL without parsing")
    return parser.parse_args()


def main() -> int:
    if sys.argv[1:2] and sys.argv[1] in ("models", "create", "send", "wait", "cancel"):
        from session_control import main as control_main
        return control_main(sys.argv[1:])
    if sys.argv[1:2] in (["--help"], ["-h"], []):
        print("Usage: agent-relay {list,get,models,create,send,wait,cancel} ...\n"
              "Use COMMAND --help for options. send waits by default; --no-wait returns immediately.")
        return 0
    args = parse_args()
    try:
        if getattr(args, "command", None) == "list":
            return list_sessions(args)
        provider = "cc" if args.provider == "claude" else args.provider
        provider, path = resolve_session(args.session_id, provider)
        if args.raw:
            sys.stdout.write(path.read_text(encoding="utf-8", errors="replace"))
            return 0
        session = parse_codex(path) if provider == "codex" else parse_claude(path)
        session.messages = dedupe_consecutive(session.messages)
        if not args.tools:
            session.messages = [message for message in session.messages if message.text]
        if not session.messages:
            raise RuntimeError(f"No user or assistant messages could be parsed from {path}")

        if args.around:
            entries, hit_count = around(session.messages, args.around, max(0, args.context))
            if not entries:
                raise RuntimeError(f"No message contains {args.around!r} in session {session.session_id}")
            note = f"{hit_count} matching message(s) for {args.around!r}; showing nearby context."
        elif args.all:
            entries, note = list(session.messages), ""
        else:
            entries, omitted = bounded(session.messages, max(1, args.max_messages))
            note = f"{omitted} middle message(s) omitted; use --all or --around." if omitted else ""
        sys.stdout.write(render(session, entries, args.tools, note))
        return 0
    except (OSError, RuntimeError) as error:
        print(f"agent-relay: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
