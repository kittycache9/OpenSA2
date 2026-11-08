# Gaps between the old machine and the new one

This page owns the ways a recompiled program can differ from the original, how each difference is found, and the two rules for handling them. The stages are in [pipeline.md](pipeline.md), and the levels of proof are in [verification.md](verification.md).

A gap is a place where the new program's behavior is not yet the original's. Every project meets the same five classes. Look for a gap in its class, and fix it there, not at the symptom: a patch applied where the symptom shows up leaves the cause to produce the next one.

## The five classes

### A. Instruction semantics

The new machine's instructions do not behave like the old one's in every case. Flags that an instruction leaves undefined, string and division edge cases, and above all floating point: an old processor with a register-stack floating-point unit has precision and rounding controls that a modern compiler does not reproduce on its own. A program can run for a long time with a wrong rounding mode and look correct.

*Found by:* function-level tests against the original on recorded inputs; a census of which instructions the program executes; a recorded run that diverges in a numeric field first.

### B. Control-flow coverage

Some code is not reached by the flow a lifter follows from the analyzer's entry points: jump-table targets the analyzer did not recognize, code called through pointers stored in data, code created at run time, and code that lives in unusual sections. A translated, compiling program can still contain a path that reaches a block that was never translated.

*Found by:* a trap when control reaches an address with no module; a sweep of the program's own data for code addresses; driving the program through more of its paths and recording every address reached.

*Fixed by:* registering the block as an extra with a recorded reason (see [pipeline.md](pipeline.md), stage 3), never by making the missing code return.

### C. Platform surface

The original calls operating-system and library services that do not exist on the new platform: file, memory, and thread services, graphics, audio, and input devices, and locale and code-page data. Each call is either implemented, replaced with a stub that traps by name, or declared out of scope.

*Found by:* classifying every imported service up front; running the program and recording which imports it reaches; comparing the output of two independent implementations of a service.

### D. Guest state and layout

The original assumes its own address space and its own structures: fixed addresses, relocations, thread-local storage, and structured exception handling. Pointers in the original are addresses in its space, and a runtime that keeps them as addresses avoids a whole family of translation bugs.

*Found by:* a crash at an address the original never uses; a mismatch in a recorded run's pointer-bearing fields; a thread-start failure.

### E. Timing and concurrency

The original relies on the order and timing of its threads, clocks, frame pacing, and input. This is the class where a recorded run diverges first, and the one with the most labelled residues. Window focus, scheduler order, and how long a system call takes can all change a run.

*Found by:* comparing a recorded run to the original's tick by tick and reading the first divergence.

## Rule: a trap names what is missing

An unresolved target produces a diagnostic trap that names the address or the service. Nothing returns success in its place. A stub must name the surface it stands in for, so that a later reader sees what is not implemented.

The trap rule exists because a silent substitute passes every check until someone plays the missing path.

## Rule: a deviation is declared

A difference chosen on purpose, such as a faster floating-point mode, is declared once, with its scope and the mode it replaces, and the original behavior stays available for fidelity checks. Every result that depends on the deviation says so. A declared deviation is not a claim that the results are identical.

## Residues

A divergence that is understood, named, and kept is a residue, not a bug. A residue record says which class it belongs to, why it is not fixed, and what would have to change to remove it. A divergence nobody has labelled is a bug until someone does.
