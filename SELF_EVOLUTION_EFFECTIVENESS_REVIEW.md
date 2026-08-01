# Self-evolution effectiveness review

## Conclusion

Repeated skill rejection was not caused by one overly strict numerical
threshold. The safety thresholds mostly protect valid invariants. The low
effective-yield rate came from four breaks in the generation/learning loop:

1. **Missing independent evidence path.** Label-free source replay produces
   directional evidence, but the CLI previously did not pass its selected
   stable canaries into the runner. An all-triggered source pool therefore had
   no possible admission path.
2. **Evidence-starved reflection.** The reflection model saw a lossy detector
   summary, not the evaluator-field-stripped trajectory. It could see that a
   certificate was incomplete but not whether the missing field had an event
   it could cite. A careful model therefore returned `no_edit`, while a less
   careful model emitted abstract or case-shaped rules.
3. **No same-source learning loop.** A legal but ineffective candidate was
   replayed once, rejected as `source_no_effect`, and only exposed to a future
   unrelated case. Small runs therefore did not learn from their own replay.
4. **Transport and experience loss.** DeepSeek V4 defaults to thinking mode.
   Small strict-JSON mutation calls could exhaust output in
   `reasoning_content` and return empty `content`, which was recorded as a
   generic invalid mutation. Persisted type experience also omitted normalized
   rejection reasons and observable before/after deltas.

There was also an accounting defect: structural acceptance reset the
case-level convergence streak before replay, and revocation did not restore the
previous streak. Candidate attempts and source cases were therefore conflated.

Lowering semantic-deduplication, leakage, rule-shape, canary-regression, probe,
or holdout thresholds would hide these defects and admit unsafe or redundant
rules. Those thresholds remain unchanged.

## Repository evidence

- `tmp/five_case_replay_evolution_20260801_233654/evolution_result.json`
  contains three generated edits that passed structural validation but made no
  target-local process change; all were rejected as `source_no_effect`.
- `tmp/five_case_deepseek_v4_flash_trace/run/evolution_result.json` contains a
  valid `no_edit` and one no-effect rule, showing that generation quality, not
  only structural strictness, was limiting yield.
- `tmp/one_case_deepseek_v4_flash_trace/run/evolution_result.json` contains one
  previously accepted development edit, proving that the source replay,
  canary, probe, and commit path can succeed when the candidate has an effect.
- The 2026-08-02 API smoke test reproduced empty final `content` with default
  DeepSeek thinking mode. After mutation calls defaulted to non-thinking JSON
  mode, the same route returned normal JSON.

## Refactor

### Generation

- Reflection now receives a bounded, explicitly untrusted trajectory excerpt
  after evaluator-only fields such as gold labels and explanations are
  removed.
- The causal attribution summary is included instead of silently discarded.
- Every allowed module is paired with its executable provider-output contract
  and target-aligned expected-effect contract. Abstract actions such as
  "improve analysis" are explicitly identified as inert.
- A broad baseline rule that was applicable but not executed may be refined by
  one operational specialization with a distinct observable trigger and an
  exact module-local output. Restating the baseline conclusion is still
  forbidden.
- Detector context now exposes bounded per-certificate fields, coverage,
  quarantines, consensus blocks, and unresolved candidate IDs instead of
  truncating a whole candidate array into an opaque string.

### Search and feedback

- Each source has a bounded mutation budget (default two, maximum three).
- A repairable structural failure or a source `no_effect` result schedules one
  same-source retry from the unchanged parent genome.
- The retry receives normalized rejection stage/reasons plus concrete
  observable deltas. `observable_changes=none` is meaningful evidence that the
  proposed mechanism was inert.
- Each candidate is still applied and replayed independently, so fitness credit
  is not shared across edits.
- Type-conditioned memory persists operation, outcome, normalized reason
  categories, observable deltas, and expected-effect status. Only bounded
  categorical fields are allowed back into later prompts.

### Validation and accounting

- Source replay now records state, trigger, candidate count, prosecution and
  defense coverage, certificate counts, quarantine count, causal-field
  coverage, responsible-step presence, and diagnostic-signal deltas.
- Counterfactual `expected_effect` is checked for target-module alignment and
  audited separately from actual fitness.
- Structural gate certificates checkpoint the prior convergence state.
  Revoked or retryable proposals restore it; a source contributes exactly one
  no-edit outcome only after its bounded attempts are exhausted.
- A merge-required proposal that is successfully repaired is marked
  `superseded`, not counted as a rejected skill.
- DeepSeek V4 mutation calls to the official endpoint default to
  `thinking={"type":"disabled"}` for reliable bounded JSON output. Explicit
  route configuration can still enable thinking. Empty final content is now an
  actionable `provider_empty_content` error, and invalid mutation transport is
  eligible for the bounded retry.

## Acceptance semantics

- Structural legality remains necessary but insufficient.
- Gold-assisted source improvement may become a development candidate.
- Label-free directional improvement may enter only the unprotected,
  reversible working genome after an independent canary is preserved.
- No-effect, regressive, runtime-invalid, and canary-regressive edits remain
  rejected; they now produce learning evidence.
- No development result becomes a final champion without the existing probe,
  final-holdout, provenance, and statistical claim gates.

## Verification

- The full repository suite passes with 76 tests.
- A deterministic end-to-end regression test starts with an inert legal rule,
  feeds its zero process delta into a same-source retry, accepts the second
  effective rule after source replay and independent canary preservation, and
  verifies that the source is counted once.
- The final DeepSeek-V4-Flash smoke request using a synthetic omission trace
  generated this reusable rule and passed the structural gate:

  > When a required action is explicitly stated in the task and the trajectory
  > shows no evidence of that action being performed, create an omission
  > candidate at the point where the action should have occurred.

  Its target is `candidate_trigger`; its predicted observable effect is an
  increase in candidate count. No credential, case identifier, or gold content
  was persisted in the rule or smoke result.

The API smoke test validates mutation generation and structural admission, not
real-dataset generalization. The deterministic source-replay test validates the
full learning control flow. Formal effectiveness still requires a registered
development run followed by the sealed final-holdout protocol.
