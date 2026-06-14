from src.llm.tasks._base import REGISTRY, IssueTask


def get_task(issue_key: str) -> IssueTask | None:
    return REGISTRY.get(issue_key)


from src.llm.tasks import (
    missing_object_type,
    incorrect_object_type,
    missing_attribute_value,
    incorrect_attribute_datatype,
    dangling_o2o_relationship,
    dangling_e2o_relationship,
    duplicate_objects_on_ids,
    duplicate_objects_on_attributes,
)


__all__ = ["IssueTask", "REGISTRY", "get_task"]
