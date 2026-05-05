# Repository Guidelines

## Project Structure & Module Organization

This repository is a collection of installable agent skills. Each top-level skill directory should be self-contained:

- `llm-wiki/`: Claude Code skill for maintaining Markdown knowledge bases. Includes `SKILL.md`, `README.md`, and `scripts/lint.py`.
- `getnote-sync/`: Codex skill for syncing Getnote notes into Obsidian. Includes `SKILL.md` and `scripts/getnote-sync.py`.
- `inbox-dispatch/`: Obsidian inbox routing skill. Classification rules in `scripts/dispatch_rules.json`; scanner is `scripts/scanner.py`; mover is `scripts/mover.py`. Claude Code does semantic classification (Pattern B).

Keep new skills in their own directory with a root `SKILL.md`. Put helper programs in `scripts/` and small static fixtures or assets beside the skill that owns them.

## Build, Test, and Development Commands

There is no repository-wide build step. Validate the specific skill you changed:

```bash
python3 -m py_compile llm-wiki/scripts/lint.py
python3 -m py_compile getnote-sync/scripts/getnote-sync.py
python3 -m py_compile inbox-dispatch/scripts/scanner.py
python3 -m py_compile inbox-dispatch/scripts/mover.py
python3 llm-wiki/scripts/lint.py <wiki-root>
python3 getnote-sync/scripts/getnote-sync.py --dry-run
python3 inbox-dispatch/scripts/scanner.py --since-days 3
python3 inbox-dispatch/scripts/mover.py <plan.json> --dry-run
```

Run skill commands from the skill directory when the `SKILL.md` says so, especially for scripts that store state relative to their own path.

## Coding Style & Naming Conventions

Python scripts are plain Python 3 and currently use the standard library only. Follow the existing compact script style: 4-space indentation, snake_case functions, uppercase constants such as `STATE_FILE`, and direct CLI flag parsing for small scripts. Name skill directories with lowercase kebab-case, for example `getnote-sync`, and keep the trigger-facing instructions in `SKILL.md`.

## Testing Guidelines

Prefer dry-run paths before executing file-moving or network-backed workflows. For Python changes, run `python3 -m py_compile` on touched scripts and then exercise the narrow command path you changed. For `llm-wiki`, use a small temporary wiki fixture and run `scripts/lint.py` against it.

## Commit & Pull Request Guidelines

Git history uses concise, imperative messages with optional Conventional Commit prefixes, for example `feat: add llm-wiki skill`, `fix: move SKILL.md into getnote-sync/ subfolder`, and `refactor: move scripts/ into getnote-sync/scripts/`. Keep commits scoped to one skill or behavior change.

Pull requests should describe the affected skill, summarize behavior changes, list validation commands run, and call out any Obsidian paths, API credentials, or local state files involved. Include screenshots only when changing user-visible documentation or generated visual output.

## Security & Configuration Tips

Do not commit personal runtime state, generated progress files, or local credentials. Use environment variables for secrets such as `GETNOTE_API_KEY`, and keep files like `scripts/.getnote_sync_state.json` out of version control unless intentionally adding a fixture.
