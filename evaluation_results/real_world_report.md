# ocel-healer Rule-Based Detection — Real-World Dataset Evaluation

Generated: 2026-09-14 14:46

## Dataset overview

| Dataset | Events | Objects | E2O rels | O2O rels | Event types | Object types | Size (MB) | Runtime (s) | Total issues |
|---|---|---|---|---|---|---|---|---|---|
| age_of_empires_ocel2 | 2372505 | 630590 | 10587042 | 2825109 | 829 | 120 | 850.55 | 60.73 | 1113760 |
| angular-github-commits | 27842 | 28317 | 2451100 | 0 | 67 | 2 | 171.02 | 9.705 | 18370 |
| blockchain | 3227146 | 167486 | 5988703 | 0 | 232 | 5 | 842.23 | 40.862 | 382 |
| enron-all-mails | 517891 | 316848 | 1765575 | 0 | 9 | 2 | 1004.29 | 16.218 | 153170 |
| period20-procedure-steps | 62719 | 37665 | 66750 | 0 | 148 | 44 | 97.95 | 4.192 | 164195 |
| reasoning-benchmark | 31709 | 1645 | 63418 | 0 | 32 | 2 | 20.69 | 0.536 | 26 |
| wil-m-p-van-der-aalst | 2183 | 5047 | 34279 | 50392 | 7 | 6 | 6.96 | 0.353 | 849 |

## Issue counts by type (all datasets)

| issue_type | age_of_empires_ocel2 | angular-github-commits | blockchain | enron-all-mails | period20-procedure-steps | reasoning-benchmark | wil-m-p-van-der-aalst |
|---|---|---|---|---|---|---|---|
| dangling_e2o_relationship | 199658 | 0 | 0 | 0 | 0 | 0 | 0 |
| dangling_o2o_relationship | 283394 | 0 | 0 | 0 | 0 | 0 | 0 |
| duplicate_e2o_relations | 0 | 0 | 293 | 0 | 0 | 0 | 0 |
| duplicate_events_on_attributes | 0 | 0 | 0 | 131940 | 0 | 26 | 0 |
| duplicate_events_on_ids | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| duplicate_o2o_relations | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| duplicate_objects_on_attributes | 118 | 0 | 22 | 0 | 0 | 0 | 399 |
| duplicate_objects_on_ids | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| incorrect_attribute_datatype | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| incorrect_event_attribute_datatype | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| missing_attribute_value | 630590 | 0 | 67 | 0 | 59210 | 0 | 449 |
| missing_event | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| missing_event_attribute_value | 0 | 18370 | 0 | 21230 | 104985 | 0 | 0 |
| missing_event_timestamp | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| missing_event_type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| missing_object | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| missing_object_type | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| o2o_self_loop | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

## Per-dataset breakdown

### age_of_empires_ocel2

| Issue type | Count |
|---|---|
| missing_attribute_value | 630590 |
| dangling_o2o_relationship | 283394 |
| dangling_e2o_relationship | 199658 |
| duplicate_objects_on_attributes | 118 |

### angular-github-commits

| Issue type | Count |
|---|---|
| missing_event_attribute_value | 18370 |

### blockchain

| Issue type | Count |
|---|---|
| duplicate_e2o_relations | 293 |
| missing_attribute_value | 67 |
| duplicate_objects_on_attributes | 22 |

### enron-all-mails

| Issue type | Count |
|---|---|
| duplicate_events_on_attributes | 131940 |
| missing_event_attribute_value | 21230 |

### period20-procedure-steps

| Issue type | Count |
|---|---|
| missing_event_attribute_value | 104985 |
| missing_attribute_value | 59210 |

### reasoning-benchmark

| Issue type | Count |
|---|---|
| duplicate_events_on_attributes | 26 |

### wil-m-p-van-der-aalst

| Issue type | Count |
|---|---|
| missing_attribute_value | 449 |
| duplicate_objects_on_attributes | 399 |
| o2o_self_loop | 1 |
