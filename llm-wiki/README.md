# llm-wiki — Claude Code Skill

A [Karpathy-style LLM Wiki][karpathy-gist] skill for [Claude Code][claude-code]. Maintain a continuously compiled, cross-linked Markdown knowledge base in any directory — instead of re-deriving answers from raw documents every time you ask a question, the LLM **compiles knowledge once and keeps it current**. Cross-references are already there. Contradictions have been flagged. The synthesis reflects everything you've ingested.

> Obsidian is the IDE. The LLM is the programmer. The wiki is the codebase.
> — [Andrej Karpathy][karpathy-gist]

This is a Claude Code native port of the [llm-wiki skill][hermes-llm-wiki] from [Nous Research's Hermes Agent][hermes-agent]. The original was written for a Codex CLI–style agent; this adaptation rewires every operation to Claude Code's native tools (`Read` / `Write` / `Edit` / `Glob` / `Grep` / `WebFetch` / `Bash` / `TodoWrite`) and drops the codex / obsidian-headless / systemd setup steps that don't apply.

## What it does

Three core operations, run against a user-designated directory:

- **Ingest** — read a source (URL / PDF / pasted text / external raw directory), extract entities and concepts, create or update wiki pages, cross-link them, update `index.md` and `log.md`. A single source typically touches 5–15 wiki pages.
- **Query** — synthesize an answer from existing wiki pages. Worthwhile answers get filed back as new `queries/` pages with a back-link from one of the source concept pages — so your explorations compound instead of disappearing into chat history.
- **Lint** — programmatic health check: dead wikilinks, orphan pages, frontmatter violations, tags outside the taxonomy, stale content, source drift, log rotation. Implemented in `scripts/lint.sh`; agent reads its output and reports by severity.

Page types (all linked via `[[wikilinks]]`):

- `entities/` — people, orgs, products, models
- `concepts/` — ideas, techniques, phenomena
- `comparisons/` — structured trade-off pages
- `queries/` — filed-back query results

## Architecture

```
<wiki-root>/
├── SCHEMA.md           # domain, conventions, tag taxonomy
├── index.md            # content catalog, one line per page
├── log.md              # append-only timeline
├── raw/                # Layer 1: immutable sources (LLM reads, never writes)
│   ├── articles/
│   ├── papers/
│   ├── transcripts/
│   └── assets/
├── entities/           # Layer 2: LLM-owned wiki pages
├── concepts/
├── comparisons/
└── queries/
```

Raw sources can also live **outside** the wiki root — set `raw_source:` in `.wiki-config` and the agent reads them read-only without copying.

## Install

### As a user-level Claude Code skill

```bash
git clone https://github.com/mydreamhorse/llm-wiki-skill.git ~/.claude/skills/llm-wiki
```

Restart Claude Code. The skill registers as `llm-wiki` and activates on triggers like `建个 wiki` / `初始化 wiki` / `编入 [源]` / `查 wiki` / `lint wiki`, or when you reference "my wiki / knowledge base / notes" in context.

### Via the skills package manager

If you use [`npx skills`][skills-cli]:

```bash
npx skills add https://github.com/mydreamhorse/llm-wiki-skill.git -g -y
```

### Manual invocation

You can also place `SKILL.md` anywhere Claude Code loads skills from, or copy it into a project's `.claude/skills/llm-wiki/` to make it project-local.

## First run

Tell the agent:

> Build a wiki at `docs/my-wiki/`, domain is "AI agent engineering". Raw sources in `inbox/`.

The agent will:

1. Create the directory skeleton
2. Write `SCHEMA.md` tailored to the domain (you'll review conventions and tag taxonomy)
3. Write initial `index.md` and `log.md`
4. Suggest 3–5 seed sources with topical overlap (overlap matters — a single source rarely clears the "2+ sources needed to make a page" threshold that prevents bloat)

Then feed it sources. A single article typically updates 5–15 pages. Run `lint wiki <path>` periodically to catch drift.

## Linting

```bash
bash scripts/lint.sh <wiki-root>
```

Read-only scan. Reports, grouped by severity:

- **`[1] dead wikilinks`** — `[[foo]]` pointing to a nonexistent page
- **`[2] frontmatter violations`** — missing `title` / `created` / `type` / `tags` / `sources`
- **`[3] orphan pages`** — no inbound links (excluding terminal leaves)
- **`[3b] filed query pages`** — `queries/*` are terminal leaves by design, listed separately
- **`[4] stale pages`** — `updated:` older than 90 days since any referenced source
- **`[5] tag audit`** — tags not in the declared taxonomy
- **`[6] page size`** — pages > 200 lines, candidates for splitting
- **`[7] log rotation`** — `log.md` > 500 entries, rotate to `log-YYYY.md`

The agent reads this output, reorganizes by severity (dead links > orphans > drift > contradictions > stale > style), and appends a `## [YYYY-MM-DD] lint | N issues found` entry to `log.md`.

## How this differs from generic RAG

RAG retrieves raw chunks at query time and re-derives answers. Every question is starting from scratch.

This wiki is compiled on ingest and kept current. By the time you ask a question, the cross-references exist, the contradictions are flagged, the synthesis reflects every source you've added. The wiki keeps getting richer with every source and every query.

You're in charge of sourcing and exploration. The agent does the summarizing, cross-referencing, filing, and bookkeeping — all the grunt work that makes humans abandon personal wikis. LLMs don't get bored.

## Attribution

- **Original idea** — [Andrej Karpathy, *LLM Wiki* (2026-04)][karpathy-gist]
- **Direct source** — [Nous Research, `llm-wiki` skill v2.1.0 from hermes-agent][hermes-llm-wiki] (MIT)
- **This adaptation** — Robert Ma, for Claude Code

## License

MIT (see [LICENSE](LICENSE)). Preserves the original Nous Research copyright.

## Related

- [Karpathy's original gist][karpathy-gist] — the pattern, in the abstract
- [Hermes Agent][hermes-agent] — the direct source of this adaptation
- A more opinionated sibling skill (`second-brain`) lives in the author's internal team skill set — it's tightly bound to a specific Obsidian vault layout and adds daily-task integration. Not published, but kept as a reference data point on how the same idea can be locally specialized vs. this generic version.

[karpathy-gist]: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
[hermes-agent]: https://github.com/NousResearch/hermes-agent
[hermes-llm-wiki]: https://github.com/NousResearch/hermes-agent/blob/main/skills/research/llm-wiki/SKILL.md
[claude-code]: https://docs.claude.com/en/docs/claude-code
[skills-cli]: https://www.npmjs.com/package/skills
