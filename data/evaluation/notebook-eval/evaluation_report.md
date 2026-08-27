# OCEL-Healer System Evaluation Report

**Generated:** 2026-08-27 16:58:12
**Total test runs:** 156
**Models tested:** qwen2.5:1.5b
**Issue types:** 26

## Executive Summary

- **Overall success rate:** 26.9% (detection AND resolution)
- **Detection success rate:** 61.4% ⭐
- **Average detection recall:** 54.9%
- **Average detection precision:** 56.8%
- **Resolution success rate:** 31.8%
- **Average resolution correctness:** 31.8%

## Detection Results by Issue Type

| Issue Type | Difficulty | Detection Success | Recall | Precision | Detected / Injected |
|---|---|---|---|---|---|
| dangling_e2o_relationship | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| dangling_e2o_relationship | hard | 100.0% | 50.0% | 100.0% | 3.0 / 6.0 |
| dangling_o2o_relationship | easy | 100.0% | 50.0% | 100.0% | 3.0 / 6.0 |
| dangling_o2o_relationship | hard | 100.0% | 50.0% | 100.0% | 3.0 / 6.0 |
| duplicate_e2o_relations | easy | 100.0% | 33.3% | 100.0% | 3.0 / 9.0 |
| duplicate_e2o_relations | hard | 100.0% | 33.3% | 100.0% | 3.0 / 9.0 |
| duplicate_events_on_attributes | easy | nan% | nan% | nan% | 0.0 / 0.0 |
| duplicate_events_on_attributes | hard | nan% | nan% | nan% | 0.0 / 0.0 |
| duplicate_events_on_ids | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| duplicate_events_on_ids | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| duplicate_objects_on_attributes | easy | 100.0% | 100.0% | 0.3% | 1155.0 / 3.0 |
| duplicate_objects_on_attributes | hard | 100.0% | 100.0% | 0.5% | 1155.0 / 6.0 |
| duplicate_objects_on_ids | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| duplicate_objects_on_ids | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| incorrect_attribute_datatype | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| incorrect_attribute_datatype | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| incorrect_e2o_relationship_qualifier | easy | 0.0% | 0.0% | 0.0% | 0.0 / 9.0 |
| incorrect_e2o_relationship_qualifier | hard | 0.0% | 0.0% | 0.0% | 0.0 / 9.0 |
| incorrect_e2o_relationship_target | easy | 0.0% | 0.0% | 0.0% | 0.0 / 6.0 |
| incorrect_e2o_relationship_target | hard | 0.0% | 0.0% | 0.0% | 0.0 / 6.0 |
| incorrect_event_attribute_datatype | easy | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| incorrect_event_attribute_datatype | hard | nan% | nan% | nan% | 0.0 / 0.0 |
| incorrect_event_attribute_value | easy | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| incorrect_event_attribute_value | hard | nan% | nan% | nan% | 0.0 / 0.0 |
| incorrect_event_time | easy | nan% | nan% | nan% | 0.0 / 0.0 |
| incorrect_event_time | hard | nan% | nan% | nan% | 0.0 / 0.0 |
| incorrect_event_type | easy | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| incorrect_event_type | hard | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| incorrect_o2o_relationship_qualifier | easy | 0.0% | 0.0% | 0.0% | 0.0 / 9.0 |
| incorrect_o2o_relationship_qualifier | hard | 0.0% | 0.0% | 0.0% | 0.0 / 9.0 |
| incorrect_o2o_relationship_target | easy | 0.0% | 0.0% | 0.0% | 0.0 / 6.0 |
| incorrect_o2o_relationship_target | hard | 0.0% | 0.0% | 0.0% | 0.0 / 6.0 |
| incorrect_object_attribute_value | easy | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| incorrect_object_attribute_value | hard | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| incorrect_object_type | easy | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| incorrect_object_type | hard | 0.0% | 0.0% | 0.0% | 0.0 / 3.0 |
| missing_attribute_value | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_attribute_value | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_event | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_event | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_event_attribute_value | easy | nan% | nan% | nan% | 0.0 / 0.0 |
| missing_event_attribute_value | hard | nan% | nan% | nan% | 0.0 / 0.0 |
| missing_event_timestamp | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_event_timestamp | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_event_type | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_event_type | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_object | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_object | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_object_type | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| missing_object_type | hard | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| o2o_self_loop | easy | 100.0% | 100.0% | 100.0% | 3.0 / 3.0 |
| o2o_self_loop | hard | 0.0% | 0.0% | 0.0% | 0.0 / 9.0 |

## Full Results by Issue Type

| Issue Type | Difficulty | Detection Success | Detection Recall | Detection Precision | Resolution Correctness | Overall Success |
|---|---|---|---|---|---|---|
| dangling_e2o_relationship | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| dangling_e2o_relationship | hard | 100.0% | 50.0% | 100.0% | 0.0% | 0.0% |
| dangling_o2o_relationship | easy | 100.0% | 50.0% | 100.0% | 0.0% | 0.0% |
| dangling_o2o_relationship | hard | 100.0% | 50.0% | 100.0% | 0.0% | 0.0% |
| duplicate_e2o_relations | easy | 100.0% | 33.3% | 100.0% | 100.0% | 100.0% |
| duplicate_e2o_relations | hard | 100.0% | 33.3% | 100.0% | 100.0% | 100.0% |
| duplicate_events_on_attributes | easy | nan% | nan% | nan% | nan% | 0.0% |
| duplicate_events_on_attributes | hard | nan% | nan% | nan% | nan% | 0.0% |
| duplicate_events_on_ids | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| duplicate_events_on_ids | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| duplicate_objects_on_attributes | easy | 100.0% | 100.0% | 0.3% | 0.0% | 0.0% |
| duplicate_objects_on_attributes | hard | 100.0% | 100.0% | 0.5% | 0.0% | 0.0% |
| duplicate_objects_on_ids | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| duplicate_objects_on_ids | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| incorrect_attribute_datatype | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| incorrect_attribute_datatype | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| incorrect_e2o_relationship_qualifier | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_e2o_relationship_qualifier | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_e2o_relationship_target | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_e2o_relationship_target | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_attribute_datatype | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_attribute_datatype | hard | nan% | nan% | nan% | nan% | 0.0% |
| incorrect_event_attribute_value | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_attribute_value | hard | nan% | nan% | nan% | nan% | 0.0% |
| incorrect_event_time | easy | nan% | nan% | nan% | nan% | 0.0% |
| incorrect_event_time | hard | nan% | nan% | nan% | nan% | 0.0% |
| incorrect_event_type | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_type | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_o2o_relationship_qualifier | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_o2o_relationship_qualifier | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_o2o_relationship_target | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_o2o_relationship_target | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_attribute_value | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_attribute_value | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_type | easy | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_type | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| missing_attribute_value | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_attribute_value | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event_attribute_value | easy | nan% | nan% | nan% | nan% | 0.0% |
| missing_event_attribute_value | hard | nan% | nan% | nan% | nan% | 0.0% |
| missing_event_timestamp | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event_timestamp | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event_type | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event_type | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_object | easy | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_object | hard | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_object_type | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_object_type | hard | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| o2o_self_loop | easy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| o2o_self_loop | hard | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## Difficulty Comparison

### Easy
- **Detection success:** 60.9%
- Detection recall: 55.8%
- Detection precision: 56.5%
- Resolution correctness: 39.1%
- Overall success: 34.6%

### Hard
- **Detection success:** 61.9%
- Detection recall: 54.0%
- Detection precision: 57.2%
- Resolution correctness: 23.8%
- Overall success: 19.2%

## Detection Performance (Detailed)

- **Detection success rate:** 61.4% (81 / 156 tests)
- **Average recall:** 0.549
- **Average precision:** 0.568
- **Total detected:** 2385.0 issues
- **Total injected:** 198.0 issues
- **Detection rate:** 1204.5%

## Resolution Performance (Detailed)

- **Resolution success rate:** 31.8% (42 / 156 tests)
- **Average correctness:** 0.318
- **Attempted:** 105.0
- **Proposed:** 42.0
- **Applied:** 42.0

## Summary by Issue Type

| Issue Type | Runs | Detection Success | Detection Recall | Detection Precision | Resolution Correctness | Overall Success |
|---|---|---|---|---|---|---|
| dangling_e2o_relationship | 6 | 100.0% | 75.0% | 100.0% | 0.0% | 0.0% |
| dangling_o2o_relationship | 6 | 100.0% | 50.0% | 100.0% | 0.0% | 0.0% |
| duplicate_e2o_relations | 6 | 100.0% | 33.3% | 100.0% | 100.0% | 100.0% |
| duplicate_events_on_attributes | 6 | nan% | nan% | nan% | nan% | 0.0% |
| duplicate_events_on_ids | 6 | 100.0% | 100.0% | 100.0% | 50.0% | 50.0% |
| duplicate_objects_on_attributes | 6 | 100.0% | 100.0% | 0.4% | 0.0% | 0.0% |
| duplicate_objects_on_ids | 6 | 100.0% | 100.0% | 100.0% | 50.0% | 50.0% |
| incorrect_attribute_datatype | 6 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| incorrect_e2o_relationship_qualifier | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_e2o_relationship_target | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_attribute_datatype | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_attribute_value | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_event_time | 6 | nan% | nan% | nan% | nan% | 0.0% |
| incorrect_event_type | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_o2o_relationship_qualifier | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_o2o_relationship_target | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_attribute_value | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| incorrect_object_type | 6 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| missing_attribute_value | 6 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_event | 6 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_event_attribute_value | 6 | nan% | nan% | nan% | nan% | 0.0% |
| missing_event_timestamp | 6 | 100.0% | 100.0% | 100.0% | 50.0% | 50.0% |
| missing_event_type | 6 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| missing_object | 6 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| missing_object_type | 6 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| o2o_self_loop | 6 | 50.0% | 50.0% | 50.0% | 50.0% | 50.0% |

---

*Report generated by OCEL-Healer evaluation framework*