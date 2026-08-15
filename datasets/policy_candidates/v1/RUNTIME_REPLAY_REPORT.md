# Phase 1-C3 Runtime-Binding Replay — Oracle vs Runtime

- certificates compared: 162
- binding agreement: 84.0%
- unclassified: 0 (0.0%)

## Configuration deltas (oracle -> runtime)

| configuration | accuracy | FP | FN | TP lost |
|---|---|---|---|---|
| L2-P1 | 79.0% -> 78.0% | 9 -> 10 | 12 -> 12 | 0 -> 0 |
| L2-P2 | 78.0% -> 76.0% | 10 -> 12 | 12 -> 12 | 0 -> 0 |
| L2-P3 | 78.0% -> 76.0% | 10 -> 12 | 12 -> 12 | 0 -> 0 |
| L2-P4 | 78.0% -> 75.0% | 10 -> 12 | 12 -> 13 | 0 -> 1 |
| L2-P5 | 77.0% -> 76.0% | 11 -> 12 | 12 -> 12 | 0 -> 0 |
| baseline | 76.0% -> 76.0% | 12 -> 12 | 12 -> 12 | 0 -> 0 |
| full_set | 87.0% -> 77.0% | 1 -> 10 | 12 -> 13 | 0 -> 1 |

## Binding disagreements (oracle vs runtime)

- BFCL/114.json/c1: expression_variance vs content_contradiction
- BFCL/127.json/c1: presentational_addition vs content_contradiction
- BFCL/127.json/c3: presentational_addition vs content_contradiction
- BFCL/142.json/c1: absence_based vs content_contradiction
- Camel/059.json/c4: content_contradiction vs requirement_compliance
- Camel/060.json/c1: content_contradiction vs premise_conflict
- Camel/060.json/c2: content_contradiction vs premise_conflict
- Camel/061.json/c1: premise_conflict vs content_contradiction
- Camel/064.json/c1: expression_variance vs content_contradiction
- Magentic_One/014.json/c3: presentational_addition vs content_contradiction
- Magentic_One/065.json/c1: requirement_compliance vs content_contradiction
- Magentic_One/075.json/c4: content_contradiction vs premise_conflict
- Magentic_One/075.json/c5: content_contradiction vs premise_conflict
- Magentic_One/075.json/c6: content_contradiction vs premise_conflict
- Magentic_One/087.json/c1: content_contradiction vs premise_conflict
- Magentic_One/087.json/c2: content_contradiction vs premise_conflict
- Octotools/019.json/c1: requirement_compliance vs content_contradiction
- Octotools/022.json/c1: content_contradiction vs premise_conflict
- Octotools/028.json/c1: absence_based vs content_contradiction
- OpenDeepSearch/031.json/c2: content_contradiction vs requirement_compliance
- OpenManus/072.json/c2: delivery_form vs content_contradiction
- OpenManus/076.json/c2: content_contradiction vs requirement_compliance
- SmolAgents/051.json/c2: content_contradiction vs premise_conflict
- SmolAgents/051.json/c5: content_contradiction vs premise_conflict
- SmolAgents/078.json/c5: content_contradiction vs premise_conflict
- SmolAgents/082.json/c1: premise_conflict vs content_contradiction
