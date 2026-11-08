# Principles

This page owns three things: the words a status claim may use, what belongs in the repository, and what may be published. The other method pages rely on these rules and do not repeat them.

## Definition of done

Write the project's target in one sentence before work starts, in the claim vocabulary below. For example: "Every stage plays on named devices, and the attract demo matches a recorded run through 15,000 ticks on the selected fields." Every later claim is measured against that sentence.

A claim never goes above the highest level of evidence the project has reached. A gap in the evidence is stated, not smoothed over.

## Claim vocabulary

A status claim uses one of these phrases. Each names a level of evidence, from weakest to strongest.

| Phrase | It means | It does not mean |
| --- | --- | --- |
| builds | The program builds from pinned inputs. | That it runs. |
| maps | An analyzer has listed the program's functions, and the counts reconcile. | That the list is complete. |
| lifts | Every mapped function has been translated, or is listed with a reason. | That the translation is correct. |
| matches on fixtures | Translated functions produce the same machine state as the original on recorded inputs. | Anything about paths the fixtures miss. |
| reaches | A scripted run reaches a named point with no traps. | That the behavior on the way was correct. |
| matches a recorded run through N ticks on these fields | Selected state matches a recording of the original, tick by tick, up to the first divergence. | Fields that were not selected, or anything after the divergence. |
| matches output | The same recorded graphics and audio calls produce the same output through two independent renderers. | Anything about game logic. |
| plays on named devices | People played it on the devices listed. | Paths nobody played. |

Words outside this list, such as "works", "complete", "supported", or "faithful", need one of these phrases beside them. A claim names its evidence: the receipt, the run, or the devices.

## Repository boundary

The tracked tree holds:

- source code, written by the project or under a compatible license, with its attribution;
- synthetic tests that need no game data;
- receipts: hashes, counts, and verdicts that a tool generated;
- projections: the stable, decision-bearing part of a generated state, such as the list of extra blocks and the reason for each.

The tracked tree never holds the game's executable, its assets, translated game code, compiled output, replay captures, or device records. Those stay in an ignored local directory. Players supply their own copy of the game; the repository records only its hashes, so a tool can check a copy without holding it.

A file that a tool can regenerate is not committed. A file that records a decision a tool cannot rediscover is committed, as a projection with no timings and no machine-specific paths.

## Publication

A published file, page, issue, or commit may contain repository paths, command lines, hashes, counts, and reasoning written fresh by its author.

It must not contain:

- names, account names, email addresses, chat handles, or device serial numbers of the people working on the project;
- absolute paths, machine names, or anything else that identifies a workstation;
- excerpts of agent transcripts or local logs;
- references to material that is not public;
- dates or identifiers that tie a record to a person rather than to a decision.

Local builds embed the absolute paths of their source files. Check every package you publish with `tools/release/check_package.py` and a local deny file that lists your private terms. Check every source change with `python3 tools/public_check.py`, and add `--deny-file` with the same list before you push. Keep the deny file outside the repository.

When private notes inform a public page, write the page fresh. Do not copy, quote, link, or name the notes, and do not process them in bulk. Leave out anything the public tree cannot support.

## Reviewer checklist

Before a documentation change is committed, check that:

1. every status claim uses the claim vocabulary and names its evidence;
2. every path, command, and receipt it cites exists in the tracked tree;
3. it holds no personal or workstation detail, and no reference to non-public material;
4. each fact has one owning page, and other pages link to it rather than restate it;
5. `python3 tools/public_check.py` passes.
