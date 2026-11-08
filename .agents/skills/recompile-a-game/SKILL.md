---
name: recompile-a-game
description: "Use when starting or continuing a static recompilation of a PC game, or when working on OpenSA2's translation, runtime, or records. Routes to the method pages in docs/method."
---

# Recompile a game

This skill routes to the method. It holds no method of its own; the pages it names do.

1. Read `docs/method/README.md` for the reading order and the page that owns each task.
2. Read `docs/method/principles.md` before making any claim, commit, or publication.
3. Read `docs/method/operating.md` before starting work: work one stage at a time, inside its budget, and close a gate only as that page allows.
4. Keep your own receipts, decisions, and run outputs in the ignored `private/` directory, never in the tracked tree.
5. Stop and ask the maintainer in the cases `docs/method/operating.md` lists under "When to stop".
6. Run `python3 tools/public_check.py` before every commit.

For OpenSA2 itself, `docs/getting-started.md` covers setup and rebuilding the translated image.
