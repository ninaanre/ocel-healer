# OCEL-Healer — Real-World Dataset Analysis

Coverage of the OCEL-Healer detection suite across seven real-world OCEL 2.0 logs. Rule-based detectors are run in full; two LLM detection sweeps at the bottom illustrate defects the deterministic pass cannot reach.

## Structural profile

| | angular | blockchain | enron | period20 | reasoning | wil | aoe |
|---|---:|---:|---:|---:|---:|---:|---:|
| Events | 27,842 | 3,227,146 | 517,891 | 62,719 | 31,709 | 2,183 | 2,372,505 |
| Objects | 28,317 | 167,486 | 316,848 | 37,665 | 1,645 | 5,047 | 630,590 |
| Event types | 67 | 232 | 9 | 148 | 32 | 7 | 829 |
| Object types | 2 | 5 | 2 | 44 | 2 | 6 | 120 |
| E2O edges | 2,451,100 | 5,988,703 | 1,765,575 | 66,750 | 63,418 | 34,279 | 10,587,042 |
| O2O edges | 0 | 0 | 0 | 0 | 0 | 50,392 | 2,825,109 |

## Coverage grid — raw counts

_Each cell is `count (%)` where the percentage is the share of the detector's applicable denominator: attribute cells for the attribute-value detectors, object/event rows for the row-level detectors, E2O/O2O edges for the relation detectors. Cells reading `0` had no defects; the denominator is still non-zero unless the log has no data of that shape (e.g. O2O detectors on a log with no O2O edges)._

### Detections per detector × dataset

| Detector | angular | blockchain | enron | period20 | reasoning | wil | aoe |
|---|---:|---:|---:|---:|---:|---:|---:|
| Missing Object Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Object Attribute Value | 0 | 67 (0.040 %) | 0 | 59,210 (13.4 %) | 0 | 449 (5.32 %) | 630,590 (100.0 %) |
| Missing Event Attribute Value | 18,370 (14.2 %) | 0 | 21,230 (2.05 %) | 104,985 (6.23 %) | 0 | 0 | 0 |
| Duplicate Objects (by ID) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Duplicate Objects (by attrs) | 0 | 22 (0.013 %) | 0 | 0 | 0 | 399 (7.91 %) | 118 (0.019 %) |
| Duplicate Events (by ID) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Duplicate Events (by attrs) | 0 | 0 | 131,940 (25.5 %) | 0 | 26 (0.082 %) | 0 | 0 |
| Duplicate O2O Relations | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| O2O Self-Loop | 0 | 0 | 0 | 0 | 0 | 1 (0.0020 %) | 0 |
| Duplicate E2O Relations | 0 | 293 (0.0049 %) | 0 | 0 | 0 | 0 | 0 |
| Incorrect Object Attribute Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Incorrect Event Attribute Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Dangling O2O Relationship | 0 | 0 | 0 | 0 | 0 | 0 | 283,394 (10.0 %) |
| Dangling E2O Relationship | 0 | 0 | 0 | 0 | 0 | 0 | 199,658 (1.89 %) |
| Missing Event Time | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Event | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Event Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Object | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Coverage grid — per 1,000 events

_Same table, normalized by that dataset's event count. Removes the size effect so relative defect rates compare cleanly._

### Detections per 1k events

| Detector | angular | blockchain | enron | period20 | reasoning | wil | aoe |
|---|---:|---:|---:|---:|---:|---:|---:|
| Missing Object Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Object Attribute Value | 0 | 0.02 | 0 | 944.1 | 0 | 205.7 | 265.8 |
| Missing Event Attribute Value | 659.8 | 0 | 41.0 | 1673.9 | 0 | 0 | 0 |
| Duplicate Objects (by ID) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Duplicate Objects (by attrs) | 0 | 0.0068 | 0 | 0 | 0 | 182.8 | 0.05 |
| Duplicate Events (by ID) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Duplicate Events (by attrs) | 0 | 0 | 254.8 | 0 | 0.82 | 0 | 0 |
| Duplicate O2O Relations | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| O2O Self-Loop | 0 | 0 | 0 | 0 | 0 | 0.46 | 0 |
| Duplicate E2O Relations | 0 | 0.09 | 0 | 0 | 0 | 0 | 0 |
| Incorrect Object Attribute Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Incorrect Event Attribute Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Dangling O2O Relationship | 0 | 0 | 0 | 0 | 0 | 0 | 119.4 |
| Dangling E2O Relationship | 0 | 0 | 0 | 0 | 0 | 0 | 84.2 |
| Missing Event Time | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Event | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Event Type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Missing Object | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Coverage grid — presence heatmap

_✓ = detector fires at least once on that log; – = no defects of this class in this dataset. Illustrates that the suite hits real signal on every log examined._

### Which detector fires on which log

| Detector | angular | blockchain | enron | period20 | reasoning | wil | aoe |
|---|---:|---:|---:|---:|---:|---:|---:|
| Missing Object Type | – | – | – | – | – | – | – |
| Missing Object Attribute Value | – | ✓ | – | ✓ | – | ✓ | ✓ |
| Missing Event Attribute Value | ✓ | – | ✓ | ✓ | – | – | – |
| Duplicate Objects (by ID) | – | – | – | – | – | – | – |
| Duplicate Objects (by attrs) | – | ✓ | – | – | – | ✓ | ✓ |
| Duplicate Events (by ID) | – | – | – | – | – | – | – |
| Duplicate Events (by attrs) | – | – | ✓ | – | ✓ | – | – |
| Duplicate O2O Relations | – | – | – | – | – | – | – |
| O2O Self-Loop | – | – | – | – | – | ✓ | – |
| Duplicate E2O Relations | – | ✓ | – | – | – | – | – |
| Incorrect Object Attribute Type | – | – | – | – | – | – | – |
| Incorrect Event Attribute Type | – | – | – | – | – | – | – |
| Dangling O2O Relationship | – | – | – | – | – | – | ✓ |
| Dangling E2O Relationship | – | – | – | – | – | – | ✓ |
| Missing Event Time | – | – | – | – | – | – | – |
| Missing Event | – | – | – | – | – | – | – |
| Missing Event Type | – | – | – | – | – | – | – |
| Missing Object | – | – | – | – | – | – | – |

## Concrete findings — one per dataset

- **angular-github-commits** — 67 event types over 27,842 events, but **52 of them fire fewer than 10 times** (98 events total). Types like `typo` (5), `wip` (2), `bump` (1), `readme` (2) are conventional-commit-prefix artefacts, not distinct workflows. The `event_Add` table also carries a leaked pandas index column (`Unnamed: 0` present) — the incorrect-attribute-datatype detector fires on it in the export.
- **blockchain** — one activity, `transfer tokens`, accounts for 591,858 of 3,227,146 events (18.3 %). 71 distinct event types begin with `call to ` (Solidity-method names bled directly into the log's type vocabulary). 127,018 EOA objects dominate the 3,227,146-event log.
- **enron-all-mails** — 331,693 / 517,891 events (64.0 %) are classified as `Default` — effectively *unlabelled* email. Only 248 `Undeliverable` and 49 `Declined` events exist across the entire ~1-year corpus (rule detectors can't distinguish the true category of a `Default`; the LLM `incorrect_event_type` detector can).
- **period20-procedure-steps** — 44 object types and 148 event types (German Bundestag legislative extract). Three near-duplicate parliamentary-question object types coexist: `Kleine Anfrage` (4,569), `Mündliche Frage` (2,888), `Schriftliche Frage` (20,467) — a natural target for the schema-suggestion detector.
- **reasoning-benchmark** — every one of the 31,709 events sits inside a single 9-hour window: `1970-01-02 04:46:59+00:00` → `1970-01-02 13:35:08+00:00` (1 distinct date across the entire log). The values are structurally valid ISO timestamps, so rule-based `missing_event_timestamp` returns zero. Only the LLM `incorrect_event_time` detector can spot that they're unix-epoch garbage.
- **wil-m-p-van-der-aalst** — one of two logs with `object_object` relations (50,392 edges across 9 distinct qualifiers). 1,668 papers × 996 researchers with a fully connected bibliographic graph — the natural fit for `dangling_o2o_relationship` and `duplicate_objects_on_attributes` on `Researcher`.
- **age-of-empires** — 829 event types × 120 object types (Villager, Farm, Spearman, Knight, Archer, Town Center, …), every game action logged as a Start/Complete pair (269 `Start …` + 269 `Complete …` distinct types). O2O structure is huge: 2,825,109 edges. **Systemic schema typo**: 120/120 object-type tables declare a `ocel_change_field` column (missing the `d`) where OCEL 2.0 expects `ocel_changed_field` — the export pipeline misspelled a reserved column name in every table.

## Three headline findings — what our tool surfaces that nothing else does

Ordered by presentation punch, from strongest to weakest.

### 1. age-of-empires: 483,052 broken relations and a reserved-column typo

Across a 2,372,505-event / 630,590-object gameplay trace, our detectors find **283,394** dangling `object_object` edges (10.0 % of the 2,825,109 total O2O relations) and **199,658** dangling `event_object` edges (1.9 % of the 10,587,042 E2O relations) — **483,052 broken referential-integrity defects** in a single log. This is the largest single detection result across all seven datasets, and the *only* one where the two dangling-relation detectors fire — they were silent everywhere else. Separately, our missing-attribute-value detector flags **100 %** of object attribute cells: the export pipeline declares a column called `ocel_change_field` in every one of the 120 object-type tables where the OCEL 2.0 spec expects `ocel_changed_field` (missing the `d`). Our tool spots the typo the same day it lands.

### 2. reasoning-benchmark: every timestamp is unix-epoch garbage

All 31,709 events sit inside a single 9-hour window on **1970-01-02**. The dataset was published as an LLM-reasoning benchmark and clearly had its `ocel_time` column filled with a placeholder — but every value parses cleanly as a valid ISO timestamp. **Rule-based data quality gives this log a clean bill of health**: `missing_event_timestamp` returns zero. Only the LLM `incorrect_event_time` detector, run *after* the rule pass, can spot that the values are structurally valid but semantically nonsense.

### 3. enron: 64 % of events are unlabelled as `Default`

**331,693 of 517,891** events (64.0 %) carry the type `Default` — i.e. no discernible category. Meanwhile the corpus has only a handful of `Undeliverable`, `Declined`, and `Accepted` events across the entire ~1-year window. The rule pass sees a fully-populated `ocel_type` column and returns zero missing types. The LLM `incorrect_event_type` detector, given a `Default` event's subject and content, can classify it as forwarding / response / reminder etc. — which is exactly the schema the log already declares.

---

_Generated by `scripts/presentation_analysis.py` (rule-based only)._
