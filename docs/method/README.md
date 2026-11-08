# Recompiling a game: the method

These pages describe how OpenSA2 turns a shipped x86 PC game into a native program, written so the method applies to other games. The method was derived from one game. Treat the general pages as provisional until a second game has been through them.

Most of OpenSA2 was built by LLM agents working under the rules in [operating.md](operating.md), with a maintainer setting direction and closing the decisions reserved to a person. The pages are written for both kinds of reader: a person planning a project, and an agent doing the work.

## Reading order

1. [principles.md](principles.md): what a claim may say, what belongs in the repository, and what may be published.
2. [operating.md](operating.md): how the work is run: stages, budgets, gates, reviews, and when to stop.
3. [pipeline.md](pipeline.md): the stages from executable to native program, and what each must produce.
4. [gaps.md](gaps.md): the ways the old machine and the new one differ, and the rules for handling them.
5. [verification.md](verification.md): the levels of proof, and what each can and cannot show.
6. [records.md](records.md): receipts, projections, gates, decisions, and failure cases.
7. [new-game.md](new-game.md): the ordered steps for a new game, each with its exit check.
8. [opensa2-examples.md](opensa2-examples.md): every OpenSA2 fact the pages use, one section per topic. The general pages name no game; this one holds them all.

## Find the page for a task

| Task | Read |
| --- | --- |
| Start a recompilation of another game | [new-game.md](new-game.md), then [principles.md](principles.md) and [operating.md](operating.md) |
| Decide whether a status claim is allowed | [principles.md](principles.md), "Claim vocabulary", then [verification.md](verification.md) |
| Decide what may be committed or published | [principles.md](principles.md), "Repository boundary" and "Publication" |
| Plan a stage, its budget, and its gate | [operating.md](operating.md), "Stages and gates" |
| Decide whether you may close a gate | [operating.md](operating.md), "Who may close a gate" |
| Know when an agent must stop and ask | [operating.md](operating.md), "When to stop" |
| Handle a trap or a missing block | [gaps.md](gaps.md), then the OpenSA2 section "Extras" in [opensa2-examples.md](opensa2-examples.md) |
| Decide how to check a change | [verification.md](verification.md) |
| Keep a record, a decision, or a failure case | [records.md](records.md) |
| Rebuild OpenSA2's translated image | [../getting-started.md](../getting-started.md), "Translate the game image" |

Agents start from `.agents/skills/recompile-a-game/SKILL.md`, which routes to these pages.
