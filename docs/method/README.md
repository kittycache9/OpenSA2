# Recompiling a game: the method

These pages describe how OpenSA2 turns a shipped x86 PC game into a native program, written so the method applies to other games. The method was derived from one game. Treat the general pages as provisional until a second game has been through them.

Most of OpenSA2 was built by LLM agents working under the rules in [operating.md](operating.md), with a maintainer setting direction and closing the decisions reserved to a person. The pages are written for both kinds of reader: a person planning a project, and an agent doing the work.

## Reading order

1. [principles.md](principles.md): what a claim may say, what belongs in the repository, and what may be published.
2. [operating.md](operating.md): how the work is run: stages, budgets, gates, reviews, and when to stop.

More pages follow: the pipeline stages, the gap classes, the verification rungs, the record types, a playbook for a new game, and a page of OpenSA2 examples.

## Find the page for a task

| Task | Read |
| --- | --- |
| Decide whether a status claim is allowed | [principles.md](principles.md), "Claim vocabulary" |
| Decide what may be committed or published | [principles.md](principles.md), "Repository boundary" and "Publication" |
| Plan a stage, its budget, and its gate | [operating.md](operating.md), "Stages and gates" |
| Decide whether you may close a gate | [operating.md](operating.md), "Who may close a gate" |
| Know when an agent must stop and ask | [operating.md](operating.md), "When to stop" |
| Rebuild OpenSA2's translated image | [../getting-started.md](../getting-started.md), "Translate the game image" |

Agents start from `.agents/skills/recompile-a-game/SKILL.md`, which routes to these pages.
