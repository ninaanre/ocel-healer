"""Corruption entry point + tier stages.

`corrupt_database` copies the clean SQLite to the destination and runs the
selected corruption level. Each `_stage_*` function calls one injector per
issue in a fixed order — new event/relation issues are appended to the
existing object-side stage members so `easy` / `medium` / `hard` all
grow at the same time.
"""

from __future__ import annotations

import shutil
import sqlite3
from typing import Callable

from src.corruption._common import (
    DEFAULT_CLEAN_PATH,
    DEFAULT_DIRTY_PATH,
    DEFAULT_FULL_PATH,
    _default_dst_for_level,
    _remove_object_primary_key,
)
from src.corruption.event_issues import (
    inject_m1_empty_pick_item_medium,
    inject_m1_null_item_out_of_stock_hard,
    inject_m1_null_place_order_easy,
    inject_missing_event_bare_id_hard,
    inject_missing_event_pick_item_medium,
    inject_missing_event_place_order_easy,
    inject_missing_event_type_empty_pay_medium,
    inject_missing_event_type_null_confirm_easy,
    inject_missing_event_type_whitespace_package_hard,
)
from src.corruption.object_issues import (
    inject_datatype_blob_in_role,
    inject_datatype_string_in_order_price,
    inject_datatype_string_in_weight,
    inject_dup_id_conflicting_types,
    inject_dup_id_product,
    inject_dup_id_triple_null_type,
    inject_n2,
    inject_n2_empty_string_order,
    inject_n2_null_employee,
    inject_n2_whitespace_product,
    inject_n3a_empty_string_role,
    inject_n3a_null_order_price,
    inject_n3a_null_product_weight,
    inject_n6a,
    inject_n6b,
    inject_n6b_clone_employee,
    inject_n6b_clone_order_and_referenced,
    inject_n6b_clone_product,
    inject_n7a_case_variant_customers,
    inject_n7a_swap_item_to_product,
    inject_n7a_swap_order_to_employee,
)
from src.corruption.relation_issues import (
    inject_m5_missing_item_medium,
    inject_m5_missing_order_easy,
    inject_m5_missing_product_hard,
    inject_n10_event,
    inject_n10_missing_both,
    inject_n10_missing_event,
    inject_n10_missing_object_easy,
    inject_n10_object,
    inject_o2o_missing_both_typo,
    inject_o2o_missing_source,
    inject_o2o_missing_target,
)


def corrupt_database(
    src_path: str,
    dst_path: str | None = None,
    *,
    level: str = "legacy",
) -> str:
    """Copy `src_path` to `dst_path` and inject corruptions for `level`.

    When `dst_path` is None, the default per-level location under ``data/`` is
    used. Returns the resolved destination path.
    """
    dst_path = dst_path or _default_dst_for_level(level)
    shutil.copy2(src_path, dst_path)
    stages = _LEVEL_STAGES.get(level)
    if stages is None:
        raise ValueError(
            f"Unknown corruption level {level!r}; expected one of "
            f"{sorted(_LEVEL_STAGES)}"
        )
    with sqlite3.connect(dst_path) as conn:
        _remove_object_primary_key(conn)
        for stage in stages:
            stage(conn)
        conn.commit()
    return dst_path


def _stage_legacy(conn: sqlite3.Connection) -> None:
    """Reproduce the pre-`level` corruption sequence exactly."""
    n6a_id = inject_n6a(conn)
    inject_n6b(conn)
    inject_n2(conn, exclude_id=n6a_id)
    inject_n10_object(conn)
    inject_n10_event(conn)


def _stage_easy(conn: sqlite3.Connection) -> None:
    # Object-side
    inject_dup_id_product(conn)                # N6a first (uses PK-less object table)
    inject_n2_null_employee(conn)              # then N2 (on a disjoint id)
    inject_n3a_null_product_weight(conn)
    inject_n7a_swap_order_to_employee(conn)
    inject_datatype_string_in_weight(conn)
    inject_n6b_clone_product(conn)
    inject_n10_missing_object_easy(conn)
    inject_o2o_missing_source(conn)
    # Event-side (Missing Data row)
    inject_m1_null_place_order_easy(conn)
    inject_missing_event_place_order_easy(conn)
    inject_missing_event_type_null_confirm_easy(conn)
    # Relation-borne missing object
    inject_m5_missing_order_easy(conn)


def _stage_medium(conn: sqlite3.Connection) -> None:
    # Object-side
    inject_dup_id_conflicting_types(conn)
    inject_n2_empty_string_order(conn)
    inject_n3a_empty_string_role(conn)
    inject_n7a_swap_item_to_product(conn)
    inject_datatype_string_in_order_price(conn)
    inject_n6b_clone_employee(conn)
    inject_n10_missing_event(conn)
    inject_o2o_missing_target(conn)
    # Event-side (Missing Data row)
    inject_m1_empty_pick_item_medium(conn)
    inject_missing_event_pick_item_medium(conn)
    inject_missing_event_type_empty_pay_medium(conn)
    # Relation-borne missing object
    inject_m5_missing_item_medium(conn)


def _stage_hard(conn: sqlite3.Connection) -> None:
    # Object-side
    inject_dup_id_triple_null_type(conn)
    inject_n2_whitespace_product(conn)
    inject_n3a_null_order_price(conn)
    inject_n7a_case_variant_customers(conn)
    inject_datatype_blob_in_role(conn)
    inject_n6b_clone_order_and_referenced(conn)
    inject_n10_missing_both(conn)
    inject_o2o_missing_both_typo(conn)
    # Event-side (Missing Data row)
    inject_m1_null_item_out_of_stock_hard(conn)
    inject_missing_event_bare_id_hard(conn)
    inject_missing_event_type_whitespace_package_hard(conn)
    # Relation-borne missing object
    inject_m5_missing_product_hard(conn)


_LEVEL_STAGES: dict[str, list[Callable[[sqlite3.Connection], None]]] = {
    "legacy": [_stage_legacy],
    "easy":   [_stage_easy],
    "medium": [_stage_medium],
    "hard":   [_stage_hard],
    "all":    [_stage_easy, _stage_medium, _stage_hard],
}


__all__ = [
    "corrupt_database",
    "DEFAULT_CLEAN_PATH",
    "DEFAULT_DIRTY_PATH",
    "DEFAULT_FULL_PATH",
]
