from src.llm.tasks._base import REGISTRY, IssueTask


def get_task(issue_key: str) -> IssueTask | None:
    return REGISTRY.get(issue_key)


from src.llm.tasks import (
    missing_object_type,
    incorrect_object_type,
    missing_attribute_value,
    missing_event_attribute_value,
    incorrect_attribute_datatype,
    incorrect_attribute_value,
    incorrect_event_attribute_datatype,
    incorrect_event_attribute_value,
    dangling_o2o_relationship,
    dangling_e2o_relationship,
    duplicate_objects_on_ids,
    duplicate_objects_on_attributes,
    missing_event,
    missing_event_type,
    missing_event_timestamp,
    missing_object,
    missing_object_attribute,
    missing_event_attribute,
)


__all__ = ["IssueTask", "REGISTRY", "get_task"]
