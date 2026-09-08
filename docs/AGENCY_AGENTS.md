# Agency Agents (subagent roster)

`.claude/agents/` holds 273 specialist subagent definitions installed from
[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) (MIT).

- Installed: 2026-09-08, upstream commit `647c8ba`
- All 18 divisions (engineering, design, marketing, security, finance, testing, …)
- Flat layout, `<division>-<slug>.md`, exactly as the upstream installer writes it

Claude Code picks these up automatically for anyone working in this repo — no
per-machine setup, nothing to run on the store PCs.

## Update or re-scope

```bash
git clone --depth 1 https://github.com/msitarzewski/agency-agents.git /tmp/agency-agents
CLAUDE_CONFIG_DIR=/path/to/printosky/.claude \
  /tmp/agency-agents/scripts/install.sh --tool claude-code
```

To keep only some divisions, add `--division engineering,security,testing`
(`--list teams` prints every division and its agent count). Trimming is just
deleting files from `.claude/agents/` — nothing else references them.

The files are upstream verbatim so future installs diff cleanly; edit them only
if you intend to carry the change forward yourself.
