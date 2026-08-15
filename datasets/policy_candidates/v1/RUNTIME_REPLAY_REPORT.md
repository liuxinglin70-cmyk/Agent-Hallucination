# Runtime-Binding Replay — Oracle vs Runtime

- certificates compared: 162
- binding agreement: 92.0%
- unclassified: 1 (0.6%)

## Configuration deltas (oracle -> runtime)

| configuration | accuracy | FP | FN | TP lost |
|---|---|---|---|---|
| L2-P1 | 79.0% -> 77.0% | 9 -> 11 | 12 -> 12 | 0 -> 0 |
| L2-P2 | 78.0% -> 76.0% | 10 -> 12 | 12 -> 12 | 0 -> 0 |
| L2-P3 | 78.0% -> 76.0% | 10 -> 12 | 12 -> 12 | 0 -> 0 |
| L2-P4 | 78.0% -> 76.0% | 10 -> 12 | 12 -> 12 | 0 -> 0 |
| L2-P5 | 77.0% -> 76.0% | 11 -> 12 | 12 -> 12 | 0 -> 0 |
| baseline | 76.0% -> 76.0% | 12 -> 12 | 12 -> 12 | 0 -> 0 |
| full_set | 87.0% -> 78.0% | 1 -> 10 | 12 -> 12 | 0 -> 0 |

## Class confusion (rows = oracle, columns = runtime)

| oracle \ runtime | absence_based | content_contradiction | delivery_form | expression_variance | premise_conflict | presentational_addition | requirement_compliance | unclassified |
|---|---|---|---|---|---|---|---|---|
| absence_based | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| content_contradiction | 0 | 140 | 0 | 0 | 0 | 0 | 2 | 0 |
| delivery_form | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 |
| expression_variance | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| premise_conflict | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| presentational_addition | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| requirement_compliance | 0 | 2 | 0 | 0 | 0 | 0 | 5 | 1 |
| unclassified | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Calibration directions

- over-bound (oracle baseline -> runtime policy class): requirement_compliance x2
- under-bound (oracle policy class -> runtime baseline): premise_conflict x2, absence_based x1, presentational_addition x3, expression_variance x2, requirement_compliance x2

## Binding disagreements (oracle vs runtime)

- BFCL/044.json/c3: content_contradiction vs requirement_compliance
- BFCL/114.json/c1: expression_variance vs content_contradiction
- BFCL/127.json/c1: presentational_addition vs content_contradiction
- BFCL/127.json/c3: presentational_addition vs content_contradiction
- BFCL/142.json/c1: absence_based vs content_contradiction
- Camel/061.json/c1: premise_conflict vs content_contradiction
- Camel/064.json/c1: expression_variance vs content_contradiction
- Magentic_One/014.json/c3: presentational_addition vs content_contradiction
- Magentic_One/065.json/c1: requirement_compliance vs content_contradiction
- Magentic_One/066.json/c2: requirement_compliance vs content_contradiction
- Octotools/019.json/c1: requirement_compliance vs unclassified
- OpenDeepSearch/031.json/c2: content_contradiction vs requirement_compliance
- SmolAgents/082.json/c1: premise_conflict vs content_contradiction
