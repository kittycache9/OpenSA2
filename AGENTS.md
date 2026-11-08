# OpenSA2 contributor commands

Keep this repository source-only. Game files, replay captures, translated game code,
device records, and compiled output belong in ignored local directories.
Preserve upstream attribution and licenses.

Documentation screenshots belong under `docs/screenshots/`. Inspect the visible
content for personal details and strip PNG metadata before publishing them.

To recompile a game, or to learn how OpenSA2 was built, read
`docs/method/README.md`. Agents load `.agents/skills/recompile-a-game/SKILL.md`.

Read README.md, docs/architecture.md, and docs/status.md before changing a subsystem.
Run `python3 tools/public_check.py` before committing. Create `private/generated`
before importing the build tools or running the synthetic installer tests.

For installer or launcher changes, run:

```bash
python3 tools/test_source.py
```

Test runtime and translation changes against the behavior they affect. Public
source checks do not prove gameplay parity. Keep private logs and workstation
paths out of commits, issues, and pull requests.

Commit using this repository's configured maintainer identity. Do not add
tool or assistant attribution trailers.
