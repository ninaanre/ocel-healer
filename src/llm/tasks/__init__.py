from src.llm.tasks._base import IssueTask 


REGISTRY: dict[str, IssueTask] = {}


def get_task(issue_key: str) -> IssueTask | None:
    return REGISTRY.get(issue_key)


from src.llm.tasks import (
    missing_object_types,
    missing_attributes,
    incorrect_datatypes,
    dangling_o2o_relations,
    dangling_e2o_relations,
    duplicate_object_ids,
    duplicate_object_attributes,
)


__all__ = ["IssueTask", "REGISTRY", "get_task"]
