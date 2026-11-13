# Synthetic fixture

These JSONL files are fake. They are not Claude Code sessions, and the token
counts were chosen so the analyzer tests have an exact expected total.

The string `CANARY_PROMPT_do_not_emit_9f3a` appears in message text on purpose.
The analyzer must not copy it into a report.

Layout, matching `~/.claude/projects/<project>/<session>.jsonl`:

- `session-a.jsonl` starts on 2026-09-01. One assistant message is written
  twice (a streaming partial, then the final usage) and must be counted once.
  One line is not JSON. One record is a `compact_boundary` marker.
- `session-b.jsonl` starts on 2026-10-15. One record has usage and a uuid but
  no `message.id`, so it is still counted.
- `session-a/subagents/agent-1.jsonl` is a separate session and is tagged
  `subagent`.

Do not publish a report from this directory as real usage. The analyzer sets
`synthetic: true` when it sees these files.
