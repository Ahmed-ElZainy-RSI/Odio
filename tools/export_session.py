"""Export a Claude Code session transcript (.jsonl) to readable Markdown.

Usage:
  python3 tools/export_session.py                       # newest session, default output
  python3 tools/export_session.py --out notes.md
  python3 tools/export_session.py --session 24e95e1a --no-thinking --max-result 40
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"


def resolve_project(explicit: str | None) -> Path:
    """Locate the transcript dir for the current working directory.

    Claude Code flattens the project path into a folder name, replacing both
    '/' and '_' with '-'. Rather than rely on reproducing that encoding
    exactly, fall back to reading the `cwd` recorded inside each transcript —
    that is the authoritative link back to the project.
    """
    if explicit:
        p = PROJECTS / explicit
        if not p.exists():
            raise SystemExit(f"project dir not found: {p}")
        return p

    here = Path.cwd().resolve().as_posix()
    guess = PROJECTS / here.replace("/", "-").replace("_", "-")
    if guess.exists():
        return guess

    for d in sorted(PROJECTS.iterdir()):
        if not d.is_dir():
            continue
        for f in d.glob("*.jsonl"):
            try:
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        rec = json.loads(line)
                        if rec.get("cwd") == here:
                            return d
                        break
            except Exception:
                continue
    raise SystemExit(f"no transcript dir found for {here}")


def find_session(project_dir: Path, needle: str | None):
    files = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"no .jsonl transcripts in {project_dir}")
    if needle:
        for f in files:
            if needle in f.name:
                return f
        raise SystemExit(f"no session matching {needle!r}")
    return files[0]


def load(path: Path):
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def ts(raw: str) -> str:
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def clip(text: str, max_lines: int, label: str) -> str:
    lines = text.rstrip("\n").split("\n")
    if len(lines) <= max_lines:
        return "\n".join(lines)
    hidden = len(lines) - max_lines
    return "\n".join(lines[:max_lines] + [f"… [{hidden} more {label} lines truncated]"])


def as_text(content) -> str:
    """tool_result content is sometimes a string, sometimes a block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                out.append(b.get("text") or f"[{b.get('type', 'block')}]")
            else:
                out.append(str(b))
        return "\n".join(out)
    return "" if content is None else str(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="substring of the session id")
    ap.add_argument("--project", default=None, help="project dir name under ~/.claude/projects")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-result", type=int, default=25, help="max lines kept per tool result")
    ap.add_argument("--max-input", type=int, default=20, help="max lines kept per tool input")
    ap.add_argument("--no-thinking", action="store_true", help="omit assistant reasoning blocks")
    ap.add_argument("--no-tools", action="store_true", help="omit tool calls and results entirely")
    args = ap.parse_args()

    proj = resolve_project(args.project)

    src = find_session(proj, args.session)
    records = list(load(src))

    stamps = [r["timestamp"] for r in records if r.get("timestamp")]
    counts = {"user": 0, "assistant": 0, "tool_use": 0, "thinking": 0}
    tools_used = {}
    body = []

    for rec in records:
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue

        content = msg.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]

        for b in blocks:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")

            if btype in ("text", "str"):
                text = (b.get("text") or "").strip()
                if not text:
                    continue
                counts[role] += 1
                who = "You" if role == "user" else "Claude"
                body.append(f"\n### {who} · {ts(rec.get('timestamp'))}\n\n{text}\n")

            elif btype == "thinking" and not args.no_thinking:
                think = (b.get("thinking") or "").strip()
                if not think:
                    continue
                counts["thinking"] += 1
                body.append(
                    "<details><summary>reasoning</summary>\n\n"
                    f"{clip(think, args.max_result, 'reasoning')}\n\n</details>\n"
                )

            elif btype == "tool_use" and not args.no_tools:
                name = b.get("name", "?")
                counts["tool_use"] += 1
                tools_used[name] = tools_used.get(name, 0) + 1
                raw = b.get("input") or {}
                if isinstance(raw, dict):
                    shown = raw.get("command") or raw.get("file_path") or \
                            raw.get("pattern") or raw.get("query") or \
                            json.dumps(raw, ensure_ascii=False, indent=1)
                else:
                    shown = str(raw)
                body.append(
                    f"**→ {name}**\n\n```\n{clip(str(shown), args.max_input, 'input')}\n```\n"
                )

            elif btype == "tool_result" and not args.no_tools:
                out = as_text(b.get("content")).strip()
                if not out:
                    continue
                flag = " *(error)*" if b.get("is_error") else ""
                body.append(
                    f"<details><summary>result{flag}</summary>\n\n"
                    f"```\n{clip(out, args.max_result, 'output')}\n```\n\n</details>\n"
                )

    # cwd/gitBranch only appear on some record kinds, not necessarily the first
    def first_of(field, default="?"):
        return next((r[field] for r in records if r.get(field)), default)

    header = [
        f"# Session transcript — {src.stem}",
        "",
        f"| | |",
        f"|---|---|",
        f"| Project | `{first_of('cwd')}` |",
        f"| Branch | `{first_of('gitBranch')}` |",
        f"| Started | {ts(min(stamps)) if stamps else '?'} |",
        f"| Ended | {ts(max(stamps)) if stamps else '?'} |",
        f"| Records | {len(records):,} |",
        f"| Your messages | {counts['user']} |",
        f"| Claude messages | {counts['assistant']} |",
        f"| Tool calls | {counts['tool_use']} |",
        "",
    ]
    if tools_used:
        header += ["**Tools used:** " + ", ".join(
            f"`{k}` ×{v}" for k, v in sorted(tools_used.items(), key=lambda x: -x[1])), ""]
    header += ["---", ""]

    out_path = Path(args.out) if args.out else Path(f"session-{src.stem[:8]}.md")
    out_path.write_text("\n".join(header) + "\n".join(body) + "\n", encoding="utf-8")

    kb = out_path.stat().st_size / 1024
    print(f"source : {src}")
    print(f"wrote  : {out_path}  ({kb:,.0f} KB)")
    print(f"         {counts['user']} user · {counts['assistant']} assistant · "
          f"{counts['tool_use']} tool calls · {counts['thinking']} reasoning blocks")


if __name__ == "__main__":
    main()
