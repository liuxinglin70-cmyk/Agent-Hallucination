# Repository work instructions

Read `IMPLEMENTATION_PLAN.md` before implementing, refactoring, reviewing, or
fixing this repository. It is the single current plan and progress ledger.
Use `docs/technical-solution.md` for implemented behavior and the referenced
research design for proposed capabilities.

## Current scope

- Complete code improvements and offline verification module by module.
- Do not run live providers, benchmark measurements, tuning, or real evolution
  rounds under this scope. Use explicit Fake/Mock or controlled loopback fixtures.
- Preserve raw datasets, labels, frozen protocols, original evidence reports,
  and ledger bytes. They are evidence, not cleanup candidates.
- The root plan's four-field checkpoint is the resumption record; update it
  in place together with task/defect states and concrete verification evidence.

## Implementation and review

- Apply Pristine v2.2 at commit
  `0b6f659fb5296ad19f6ca020d124714a8b9dac70`:
  <https://github.com/newbanser/pristine-skill/blob/0b6f659fb5296ad19f6ca020d124714a8b9dac70/SKILL.md>.
- Fix one demonstrated root cause at a time. Change its source rule and all
  consumers; remove superseded implementations after reference checks.
- Reuse the standard library and existing contracts. Preserve input, authority,
  evidence, deadline, cancellation, coverage, and immutable identity boundaries.
- Do not delete a CLI, exported contract, dynamic hook, fixture, or historical
  evidence solely because a static import search finds no caller.
- Mark a work package complete only after its offline checks, cleanup, review,
  and plan/documentation updates pass. Record unrun checks and blockers honestly.

Parallel work must have disjoint write ownership. Shared contracts and files
are integrated sequentially. Later direct user instructions override this
current scope; update the root plan when the authorized scope changes.
