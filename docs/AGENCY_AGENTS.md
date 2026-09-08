# Agency Agents (subagent roster)

`.claude/agents/` holds specialist subagent definitions installed from
[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) (MIT).

- Installed: 2026-09-08, upstream commit `647c8ba`
- Trimmed: 2026-09-08, from all 273 upstream agents down to **87** — see below
- Flat layout, `<division>-<slug>.md`, exactly as the upstream installer writes it

Claude Code picks these up automatically for anyone working in this repo — no
per-machine setup, nothing to run on the store PCs.

## Why trimmed

The full roster covers 18 divisions built for a general AI agency — game dev,
GIS, enterprise sales, paid media, healthcare, academic worldbuilding, dozens
of single-platform China marketing specialists. None of that is Printosky.
Kept only what maps onto something this repo actually runs:

- **engineering (43)** — backend, database, DevOps, payments, security-adjacent
  work; dropped Solidity/GaussDB/Feishu/WeChat Mini Program/embedded-IoT/video-
  streaming/Section 508/USWDS/Drupal/WordPress/mobile-app/enterprise-network
  agents that assume a stack this project doesn't have
- **security (11)** — everything except Blockchain Security Auditor
- **testing (9)** — all of it (Evidence Collector / Reality Checker match the
  fail-loud, verify-on-paper standard in this CLAUDE.md)
- **specialized (8)** — Document Generator, MCP Builder, Codebase Archaeologist,
  Master Plan Architect, Workflow Architect, Customer Service, HR Onboarding,
  Data Privacy Officer
- **support (6)** — all of it (Analytics Reporter, Infrastructure Maintainer, …)
- **marketing (4)** — Instagram Curator, Content Creator, LinkedIn Content
  Creator, SEO Specialist only — cut the ~30 single-platform/China specialists
- **design (3)** — UI Designer, UX Architect, Brand Guardian only
- **finance (1)** — Bookkeeper & Controller only
- **project-management (1)** — Meeting Notes Specialist only
- **research (1)** — Research Synthesist

Cut entirely: game-development, gis, spatial-computing, sales, paid-media,
academic, healthcare, and the product division — none apply to a one-store
print shop with a backlog of one.

## Update or re-scope

```bash
git clone --depth 1 https://github.com/msitarzewski/agency-agents.git /tmp/agency-agents
CLAUDE_CONFIG_DIR=/path/to/printosky/.claude \
  /tmp/agency-agents/scripts/install.sh --tool claude-code
```

That reinstalls the full 273-agent set (the installer doesn't know about this
project's trim). To re-scope by division at install time instead, add
`--division engineering,security,testing,...` (`--list teams` prints every
division and its agent count). Fine-grained trimming — dropping specific
agents within a division, as done here — is just deleting files from
`.claude/agents/`; nothing else references them.

The files are upstream verbatim so future installs diff cleanly; edit them only
if you intend to carry the change forward yourself.
