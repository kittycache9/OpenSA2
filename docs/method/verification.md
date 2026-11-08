# Verification

This page owns the levels of proof, what each proves and cannot prove, and the rules about regressions and first divergences. [principles.md](principles.md) owns the phrases a claim may use; this page says what stands behind each one.

A recompilation cannot be proven correct as a whole. Each level covers a bounded set of behavior, and a claim is only as strong as the level it cites.

## The levels

| Level | Claim phrase | What it proves | What it cannot prove |
| --- | --- | --- | --- |
| R0 | builds, maps, lifts | The inputs are pinned, the counts reconcile, and the output has the recorded hashes. | That the program runs. |
| R1 | matches on fixtures | Translated functions give the same machine state as the original on recorded inputs. | Paths the fixtures do not take. |
| R2 | reaches | A scripted run arrives at a named point with no traps. | That the behavior on the way was correct. |
| R3 | matches a recorded run through N ticks on these fields | Selected state equals the original's on a recording, tick by tick, up to the first divergence. | Fields that were not selected, and anything after a divergence. |
| R4 | matches output | The same recorded graphics and audio calls give the same output through two independent renderers. | Game logic. |
| R5 | plays on named devices | People played it on the devices listed. | Paths nobody played. |

R4 checks a different thing from R3, not more of it: a program can match a recording and render wrongly, or the reverse.

A claim cites the highest level it reached and names its evidence. A lower level never upgrades a claim: a thousand passing fixtures do not make a program "work".

## How each level is built

**R1, fixtures.** Choose functions that cover the instruction forms the program uses, run each in the original and the translation from the same machine state, and compare the whole state afterward. Keep the inputs and the original's outputs as the fixture. A function that passed and later fails is a regression and is reported as one.

**R2, reaching.** A scripted run drives the program toward a named point, such as a scene's goal, and records every trap on the way. A run that reaches the point with no traps proves coverage of its path, not correctness along it.

**R3, recorded runs.** Record the original, tick by tick, on a chosen list of state fields. Replay the translation and compare. Report the first divergence: the tick, the field, and both values. A run that diverges is read from its first divergence; later differences are usually consequences.

Choose the fields before comparing and record the choice. A field excluded from the comparison is an exclusion with a stated reason, not a silence.

**R4, output.** Capture the calls the program makes to its graphics and audio services, and render them through two independent implementations. Differences between them are measured and either explained or fixed. Two implementations that agree can still both be wrong; the check finds disagreement, not truth.

**R5, playing.** Record who played what, on which device, and what they saw. Treat sessions as a source of bugs to turn into R1 to R3 tests, not as a replacement for them.

## Rules

**Regression rule.** A check that passed must pass after a change, or the change records a decision that explains why not. A gate that changes its own pass condition is a change to the project's target and belongs to the maintainer (see [operating.md](operating.md)).

**First-divergence rule.** A failing comparison reports its first divergence. A summary that says only "fails" tells the next person nothing.

**Independence rule.** The check that finds a defect must not share the defect's cause. A comparer, a mask, or a recorder that nothing else checks needs its own review and its own test that it can fail: show it failing before relying on it passing.

**Recorded evidence stays local.** Recordings and captures are not committed (see [principles.md](principles.md)). The tracked record holds the procedure, the digest, and the result, so another team can repeat the run on its own recording.

**Say what was not measured.** A level that was not run is listed as not run. A gate with an unmeasured answer does not pass.
