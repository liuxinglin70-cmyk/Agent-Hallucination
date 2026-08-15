# Phase 1-A L2 Policy Candidate Set

- schema: sehd2.policy_candidates.v1
- review status: first_pass
- policies: 5
- policy-gap false positives to account for: 12

## Policies

### L2-P1 — advisory requirements are not verdict-bearing; content errors convict

- certificate class: `requirement_compliance`
- action: `require_corroborating_content_error`
- gap names aggregated: strictness_miscalibration
- failure cases: 5
- expected FP reduction: 4

The court reads instructions about HOW to work — output format, search procedure, effort scope, plan hygiene, answer-quality preferences — as factual requirements, so trajectories whose content is correct are convicted on compliance style. At benchmark tolerance these instructions are advisory: the hallucination taxonomy targets fabricated, misreported or contradicted content, not working style. A compliance deviation may only convict alongside a separately established content error. Delivery-channel deviations are OUT OF SCOPE: they are non-separable under the current evidence representation (NS-1) and the policy abstains on them.

Trigger: The certificate's asserted deviation is non-compliance with an instruction about how to work — output form, procedure, tool or channel choice, number or order of steps, plan content, or answer quality judged against a preferred identification — and the certificate asserts no fabricated, misreported or contradicted content.

Risk boundary: Only certificates whose entire proof basis is the instruction text are covered, and the action removes sole-basis conviction rather than the certificate itself.

### L2-P2 — presentational additions over cited observations are not fabricated content

- certificate class: `presentational_addition`
- action: `withhold_conviction`
- gap names aggregated: strictness_miscalibration, expression_tolerance
- failure cases: 3
- expected FP reduction: 2

Verification convicts qualitative material layered on top of real observations — evaluative characterizations of cited data, code-to-name expansions, unit renderings — treating interpretation as fabrication. These additions introduce no new checkable specific: everything they assert renders content the cited observations already contain.

Trigger: The certificate asserts ADDED material over content the cited observations already contain — evaluative qualifiers, expansions of codes into names, unit or format renderings — and the addition introduces no new checkable specific (no new number, name, date, place, result or completion claim) and no self-assessment of the agent's own work.

Risk boundary: The policy protects renderings of observed content only; it never protects a specific the evidence base lacks or a claim about the agent's own performance.

### L2-P3 — restatement granularity in prose is not misreporting

- certificate class: `expression_variance`
- action: `withhold_conviction`
- gap names aggregated: expression_tolerance
- failure cases: 2
- expected FP reduction: 2

The exact-match standard that correctly governs tool arguments leaks into prose restatements: near-tie directional characterizations and paraphrases of user text are convicted as misreports. Outside tool arguments the benchmark tolerates wording, formatting and rounding-granularity differences.

Trigger: The certificate asserts a prose restatement differs from its cited source only in wording, formatting or near-tie directionality — a directional characterization whose quantitative margin is within the verification micro-difference tolerance — and the restatement is not a tool argument and does not change the referent.

Risk boundary: Applies to prose outside tool arguments where the certificate itself cites the source being restated; margins beyond the existing micro-difference tolerance are not covered.

### L2-P4 — task- and user-established premises outrank outside corrections

- certificate class: `premise_conflict`
- action: `withhold_conviction`
- gap names aggregated: strictness_miscalibration, evidence_weighting
- failure cases: 2
- expected FP reduction: 2

The court convicts internally consistent content that rests on premises the conversation itself establishes — a user's guidance framing, a construction the task text states — by recomputing against outside knowledge. Within a task the stated premise defines what counts as correct; an outside correction without in-trajectory contradiction is not a deviation.

Trigger: The certificate's contradiction source is outside knowledge applied against content traceable to premises the task text or user statements establish, and no cited trajectory evidence contradicts the content.

Risk boundary: Protects restatement of established premises only, never adoption of user suggestions that in-task evidence or the task's own correctness standard contradicts.

### L2-P5 — absence in one evidence slice does not convict summaries and process narration

- certificate class: `absence_based`
- action: `withhold_conviction`
- gap names aggregated: strictness_miscalibration
- failure cases: 2
- expected FP reduction: 2

Verification treats absence — the cited document or episode list does not contain the claimed material — as proof of fabrication. For summaries that draw on multiple retrieved sources and for process narration about the work, absence in one slice is not contradiction: the claimed material is grounded elsewhere in the trajectory or no slice contradicts it.

Trigger: The certificate's proof basis is absence — the cited evidence slice does not contain the claimed material — the claim is a multi-source summary or a process-narration attribution rather than a specific factual assertion, and the material is grounded elsewhere in the trajectory or no cited slice contradicts it.

Risk boundary: Absence alone never protects a specific factual claim, a completion claim, or content any cited evidence positively contradicts.

## FP coverage matrix

| case | L2-P1 | L2-P2 | L2-P3 | L2-P4 | L2-P5 |
|---|---|---|---|---|---|
| BFCL/114.json |  |  | x |  |  |
| BFCL/127.json |  | x |  |  |  |
| BFCL/142.json |  |  |  |  | x |
| Camel/061.json |  |  |  | x |  |
| Camel/064.json |  |  | x |  |  |
| Magentic_One/014.json |  | x |  |  |  |
| Magentic_One/065.json | x |  |  |  |  |
| Magentic_One/066.json | x |  |  |  |  |
| Octotools/016.json | x |  |  |  |  |
| Octotools/028.json | x |  |  |  | x |
| OpenManus/048.json |  |  |  |  |  |
| SmolAgents/082.json |  |  |  | x |  |

- covered by at least one policy: 11/12
- ceiling if every trigger fires and no FN/TP regresses: 11 FP removed

## Replay entry checklist (P1-B)

1. certificate-class binding for every surviving certificate in the 100-case run tree, with audit;
2. deterministic replay of adjudication under each policy alone and the set together;
3. report FP delta, FN delta, TP retention and decision stability per policy;
4. promotion requires net accuracy gain, no FN regression beyond the stated risk boundaries, and generalization beyond the deriving cases.
