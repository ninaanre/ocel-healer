# OCEL-Healer System Evaluation Report

**Generated:** 2026-09-17 08:06:58
**Total test runs:** 440
**Models tested:** qwen2.5:7b
**Issue types:** 22

## Executive Summary

- **Overall success rate:** 35.2% (detection AND VERIFIED-correct resolution)
- **Detection success rate:** 66.4% ⭐
- **Average detection recall:** 67.9%
- **Average detection precision:** 68.6%
- **Repair-applied rate (plumbing, not correctness):** 45.0%
- **Average resolution correctness:** 35.2%

## Detection Results by Issue Type

| Issue Type | Difficulty | Detection Success | Recall | Precision | Detected / Injected |
|---|---|---|---|---|---|
| dangling_e2o_relationship | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| dangling_e2o_relationship | hard | 100.0% | 100.0% | 100.0% | 10 / 20 |
| dangling_o2o_relationship | easy | 100.0% | 100.0% | 100.0% | 10 / 20 |
| dangling_o2o_relationship | hard | 100.0% | 100.0% | 100.0% | 10 / 20 |
| duplicate_e2o_relations | easy | 100.0% | 100.0% | 100.0% | 10 / 30 |
| duplicate_e2o_relations | hard | 0.0% | 0.0% | 0.0% | 0 / 10 |
| duplicate_events_on_attributes | easy | 0.0% | 0.0% | 0.0% | 0 / 10 |
| duplicate_events_on_attributes | hard | 0.0% | 0.0% | 0.0% | 0 / 10 |
| duplicate_events_on_ids | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| duplicate_events_on_ids | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| duplicate_objects_on_attributes | easy | 0.0% | 0.0% | 0.0% | 10 / 10 |
| duplicate_objects_on_attributes | hard | 0.0% | 0.0% | 0.0% | 10 / 20 |
| duplicate_objects_on_ids | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| duplicate_objects_on_ids | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| incorrect_attribute_datatype | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| incorrect_attribute_datatype | hard | 100.0% | 100.0% | 100.0% | 30 / 10 |
| incorrect_event_attribute_datatype | easy | 0.0% | 0.0% | 0.0% | 0 / 10 |
| incorrect_event_attribute_datatype | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| incorrect_event_attribute_value | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| incorrect_event_attribute_value | hard | 0.0% | 0.0% | 0.0% | 0 / 20 |
| incorrect_event_time | easy | 0.0% | 0.0% | 0.0% | 5 / 10 |
| incorrect_event_time | hard | 0.0% | 0.0% | 0.0% | 54 / 10 |
| incorrect_event_type | easy | 0.0% | 0.0% | 0.0% | 0 / 10 |
| incorrect_event_type | hard | 20.0% | 20.0% | 20.0% | 2 / 10 |
| incorrect_object_attribute_value | easy | 0.0% | 0.0% | 0.0% | 0 / 10 |
| incorrect_object_attribute_value | hard | 0.0% | 0.0% | 0.0% | 3 / 10 |
| incorrect_object_type | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| incorrect_object_type | hard | 0.0% | 0.0% | 0.0% | 0 / 10 |
| missing_attribute_value | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_attribute_value | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_event | easy | 100.0% | 100.0% | 100.0% | 20 / 10 |
| missing_event | hard | 100.0% | 100.0% | 100.0% | 20 / 10 |
| missing_event_attribute_value | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_event_attribute_value | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_event_timestamp | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_event_timestamp | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_event_type | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_event_type | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_object | easy | 100.0% | 100.0% | 100.0% | 60 / 10 |
| missing_object | hard | 0.0% | nan% | 100.0% | 0 / 0 |
| missing_object_type | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| missing_object_type | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |
| o2o_self_loop | easy | 100.0% | 100.0% | 100.0% | 10 / 10 |
| o2o_self_loop | hard | 100.0% | 100.0% | 100.0% | 10 / 10 |

## Full Results by Issue Type

| Issue Type | Difficulty | Detection Success | Detection Recall | Detection Precision | Resolution Correctness | Overall Success |
|---|---|---|---|---|---|---|
| dangling_e2o_relationship | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| dangling_e2o_relationship | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| dangling_o2o_relationship | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| dangling_o2o_relationship | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| duplicate_e2o_relations | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| duplicate_e2o_relations | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| duplicate_events_on_attributes | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| duplicate_events_on_attributes | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| duplicate_events_on_ids | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| duplicate_events_on_ids | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| duplicate_objects_on_attributes | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| duplicate_objects_on_attributes | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| duplicate_objects_on_ids | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| duplicate_objects_on_ids | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| incorrect_attribute_datatype | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| incorrect_attribute_datatype | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| incorrect_event_attribute_datatype | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_attribute_datatype | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| incorrect_event_attribute_value | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| incorrect_event_attribute_value | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_time | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_time | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_type | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_type | hard | 20.0% | 20.0% | 20.0% | 0.0% | 0.0% |
| incorrect_object_attribute_value | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_attribute_value | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_type | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| incorrect_object_type | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| missing_attribute_value | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_attribute_value | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event | hard | 100.0% | 100.0% | 100.0% | 50.0% | 50.0% |
| missing_event_attribute_value | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event_attribute_value | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event_timestamp | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event_timestamp | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event_type | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event_type | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_object | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_object | hard | 0.0% | nan% | 100.0% | 0.0% | 0.0% |
| missing_object_type | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_object_type | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| o2o_self_loop | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| o2o_self_loop | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Difficulty Comparison

### Easy
- **Detection success:** 72.7%
- Detection recall: 72.7%
- Detection precision: 72.7%
- Resolution correctness: 45.5%
- Overall success: 45.5%

### Hard
- **Detection success:** 60.0%
- Detection recall: 62.9%
- Detection precision: 64.5%
- Resolution correctness: 25.0%
- Overall success: 25.0%

## Detection Performance (Detailed)

- **Detection success rate:** 66.4% (292 / 440 tests)
- **Average recall:** 0.679
- **Average precision:** 0.686
- **Total detected:** 464 issues
- **Total injected:** 500 issues
- **Detection rate:** 92.8%

## Resolution Performance (Detailed)

- **Repair-applied rate (plumbing, not correctness):** 45.0% (198 / 440 tests)
- **Average correctness:** 0.352
- **Attempted:** 372
- **Proposed:** 278
- **Applied:** 258

## Summary by Issue Type

| Issue Type | Runs | Detection Success | Detection Recall | Detection Precision | Resolution Correctness | Overall Success |
|---|---|---|---|---|---|---|
| dangling_e2o_relationship | 20 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| dangling_o2o_relationship | 20 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| duplicate_e2o_relations | 20 | 50.0% | 50.0% | 50.0% | 50.0% | 50.0% |
| duplicate_events_on_attributes | 20 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| duplicate_events_on_ids | 20 | 100.0% | 100.0% | 100.0% | 50.0% | 50.0% |
| duplicate_objects_on_attributes | 20 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| duplicate_objects_on_ids | 20 | 100.0% | 100.0% | 100.0% | 50.0% | 50.0% |
| incorrect_attribute_datatype | 20 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| incorrect_event_attribute_datatype | 20 | 50.0% | 50.0% | 50.0% | 50.0% | 50.0% |
| incorrect_event_attribute_value | 20 | 50.0% | 50.0% | 50.0% | 50.0% | 50.0% |
| incorrect_event_time | 20 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_type | 20 | 10.0% | 10.0% | 10.0% | 0.0% | 0.0% |
| incorrect_object_attribute_value | 20 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_type | 20 | 50.0% | 50.0% | 50.0% | 50.0% | 50.0% |
| missing_attribute_value | 20 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event | 20 | 100.0% | 100.0% | 100.0% | 75.0% | 75.0% |
| missing_event_attribute_value | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event_timestamp | 20 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event_type | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_object | 20 | 50.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_object_type | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| o2o_self_loop | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

---

*Report generated by OCEL-Healer evaluation framework*