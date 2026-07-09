"""Pydantic output models for the domain-expert LLM.

Every task declares an `OutputModel` that inherits `TaskOutput`. The client
validates the model's JSON reply against this schema; a validation failure
triggers one retry with the model's error appended to the conversation.
Extra fields (like the legacy `source` / `unit` keys) are silently ignored
so historical prompts don't break the parser.

The proposed-value field names here MUST match `_PROPOSED_KEYS` in
`src/llm/actions.py` — `from_task_result` pulls the value from the payload
by name.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskOutput(BaseModel):
    """Fields every task output carries."""

    rationale: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)

    # Silently accept legacy keys (source, unit, etc.) so a stray field in
    # the LLM's reply doesn't nuke the whole payload.
    model_config = ConfigDict(extra="ignore")


class InferredTypeOutput(TaskOutput):
    """missing_object_type + incorrect_object_type.

    `inferred_type` is null when the current type is correct (detection
    task) or when the model can't confidently pick one (resolution task).
    """

    inferred_type: str | None = None


class CoercedValueOutput(TaskOutput):
    """incorrect_attribute_datatype.

    `coerced_value` may be null when no meaning-preserving coercion exists
    (e.g. 'banana' → INTEGER).
    """

    coerced_value: Any = None


class InferredValueOutput(TaskOutput):
    """missing_attribute_value.

    `inferred_value` must be concrete — this task's contract is "always
    guess". Pydantic rejects null so the retry loop kicks in when the
    model ignores the instruction.
    """

    inferred_value: Any

    @field_validator("inferred_value")
    @classmethod
    def _not_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError(
                "inferred_value must be a concrete value; null is not allowed for this task"
            )
        return v


class InferredReferentOutput(TaskOutput):
    """dangling_e2o_relationship + dangling_o2o_relationship.

    `inferred_referent` is null when no candidate plausibly matches.
    """

    inferred_referent: str | None = None


class CanonicalValueOutput(TaskOutput):
    """duplicate_objects_on_attributes."""

    canonical_value: Any = None


class DuplicateResolutionOutput(TaskOutput):
    """duplicate_objects_on_ids — a compound decision (winner + losers)."""

    canonical_id: str
    ids_to_delete: list[str] = Field(default_factory=list)


__all__ = [
    "TaskOutput",
    "InferredTypeOutput",
    "CoercedValueOutput",
    "InferredValueOutput",
    "InferredReferentOutput",
    "CanonicalValueOutput",
    "DuplicateResolutionOutput",
]
