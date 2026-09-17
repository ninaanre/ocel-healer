"""Pure rendering helpers for the OCEL-Healer dashboard.

Extracted from `dashboard.py` so the notebook file can stay focused on cells
and reactivity. Nothing here touches `marimo.state` — `mo` is passed in from
the caller because these helpers build widgets/HTML that marimo consumes but
they do not own any reactive state themselves.
"""

from __future__ import annotations

import hashlib
import json as _json
from typing import Any, Callable, Iterable


# ── Style constants (paper Table 3 aligned) ──────────────────────────────
#
# The overview grid uses a "shared border" model to avoid the double-border
# hairlines and gaps that flex-based grids get when every child draws its
# own 1px border: each cell renders `border-right:1px` + `border-bottom:1px`
# only, and an outer wrapper draws the top + left edge. `SUMMARY_GRID_*`
# constants below carry the wrapper styling.

SUMMARY_GRID_WRAPPER_STYLE = (
    "border:1px solid #d0d7de; border-radius:6px; overflow:hidden; "
    "font-family:system-ui,-apple-system,sans-serif; font-size:13px; "
    "background:#ffffff;"
)
SUMMARY_TABLE_STYLE = (
    "border-collapse:collapse; font-family:system-ui,-apple-system,sans-serif; "
    "font-size:13px; border:1px solid #d0d7de; width:100%; background:#ffffff;"
)
SUMMARY_TH_STYLE = (
    "background:#f6f8fa; border-right:1px solid #d0d7de; "
    "border-bottom:1px solid #d0d7de; padding:8px 12px; "
    "font-weight:600; vertical-align:middle; text-align:center; "
    "white-space:nowrap;"
)
# Vertical variant used for the 13 dimension-column headers in the overview
# grid: labels are rotated 180° with `writing-mode:vertical-rl` so they read
# bottom-up, which lets the column pinch down to header-font-height + a
# little padding instead of the ~90px each horizontal label needed. Keeps
# the paper Table 3 orientation intact without overflowing typical laptop
# widths.
SUMMARY_TH_VERTICAL_STYLE = (
    "background:#f6f8fa; border-right:1px solid #d0d7de; "
    "border-bottom:1px solid #d0d7de; padding:10px 6px; "
    "font-weight:600; vertical-align:middle; text-align:center; "
    "white-space:nowrap; "
    "writing-mode:vertical-rl; transform:rotate(180deg); "
    "line-height:1.2;"
)
SUMMARY_CORNER_STYLE = (
    "background:#f6f8fa; border-right:1px solid #d0d7de; "
    "border-bottom:1px solid #d0d7de; padding:6px 10px;"
)
SUMMARY_TD_STYLE = (
    "border-right:1px solid #d0d7de; border-bottom:1px solid #d0d7de; "
    "padding:6px 8px; text-align:center; white-space:nowrap; "
    "background:#ffffff;"
)
SUMMARY_TD_SELECTED_STYLE = (
    "border-right:1px solid #0969da; border-bottom:1px solid #0969da; "
    "padding:6px 8px; text-align:center; white-space:nowrap; "
    "background:#ddf4ff;"
)
SUMMARY_ROW_LABEL_STYLE = (
    "border-right:1px solid #d0d7de; border-bottom:1px solid #d0d7de; "
    "padding:6px 10px; text-align:left; "
    "font-weight:600; background:#f6f8fa; white-space:nowrap;"
)
# Group-header row above the column labels — spans Events, Objects,
# Relations. Matches paper Table 3's top row.
SUMMARY_GROUP_STYLE = (
    "background:#f6f8fa; border-right:1px solid #d0d7de; "
    "border-bottom:1px solid #d0d7de; padding:6px 10px; "
    "font-weight:700; text-align:center; text-transform:uppercase; "
    "letter-spacing:0.04em; font-size:11px; color:#57606a;"
)
# Applied to cells in the rightmost column to suppress the trailing
# border-right (the outer wrapper already provides it).
SUMMARY_NO_RIGHT_BORDER = "border-right:none;"
# Applied to cells in the bottom row to suppress the trailing
# border-bottom.
SUMMARY_NO_BOTTOM_BORDER = "border-bottom:none;"

DETECTOR_BAD_STYLE = "background-color:#fde2e2; color:#b00020; font-weight:600;"
DETECTOR_TABLE_STYLE = (
    "border-collapse:collapse; font-family:system-ui,-apple-system,sans-serif; "
    "font-size:13px; border:1px solid #d0d7de; width:100%; table-layout:fixed;"
)
DETECTOR_TH_STYLE = (
    "background:#f6f8fa; border:1px solid #d0d7de; padding:6px 10px; "
    "text-align:left; font-weight:600; "
    "overflow:hidden; text-overflow:ellipsis; word-break:break-word;"
)
DETECTOR_TD_STYLE = (
    "border:1px solid #d0d7de; padding:6px 10px; "
    "overflow:hidden; text-overflow:ellipsis; word-break:break-word;"
)


# ── Small HTML snippets used in the overview and drill-in ────────────────

DASH_HTML = '<span style="color:#57606a;">—</span>'
PENDING_HTML = (
    '<span title="Detector implemented but the LLM sweep has not been '
    'run yet for this file." style="display:inline-block; padding:1px 8px; '
    'border-radius:10px; font-size:12px; font-weight:700; color:#9a6700; '
    'background:#fff8c5;">?</span>'
)
# Cell that is intentionally not applicable (distinct from a blank/dash
# cell whose detector merely hasn't been implemented yet). Used for
# `Missing / Obj. Attr. Type` and `Missing / Evt. Attr. Type`: a missing
# value has no declared datatype, so the datatype-missing cell can't have
# any content by construction.
NA_HTML = (
    '<span title="Not applicable — a missing value has no declared datatype." '
    'style="display:inline-block; padding:1px 8px; border-radius:10px; '
    'font-size:12px; font-weight:700; color:#57606a; background:#eaeef2;">N/A</span>'
)


def pill_html(n: int) -> str:
    # n > 0  → red    (violations detected — attention needed)
    # n == 0 → green  (detector ran and found nothing — success)
    if n > 0:
        colour, bg = "#cf222e", "#fde2e2"
    else:
        colour, bg = "#1a7f37", "#dafbe1"
    return (
        f'<span style="display:inline-block; padding:1px 8px; border-radius:10px; '
        f'font-size:12px; font-weight:600; color:{colour}; background:{bg};">{n}</span>'
    )


def badge_html(n: int) -> str:
    """Small count badge used in section headings."""
    return pill_html(n)


def render_section_header_html(title: str, subtitle: str = "") -> str:
    """Standard section header used across the dashboard.

    Renders a bold 20px title above an optional muted 13px subtitle, with
    a thin bottom rule separating the header block from what follows.
    Callers pass raw HTML for `subtitle` (may include inline formatting);
    pass an empty string to render the title only.
    """
    _subtitle_html = (
        f'<div style="margin-top:4px; color:#57606a; font-size:13px;">'
        f'{subtitle}</div>'
        if subtitle
        else ""
    )
    return (
        '<div style="margin:8px 0 14px 0; padding-bottom:8px; '
        'border-bottom:1px solid #d0d7de;">'
        f'<div style="font-size:20px; font-weight:700; color:#1f2328; '
        f'letter-spacing:-0.01em;">{title}</div>'
        f'{_subtitle_html}'
        '</div>'
    )


# ── Issue Overview table (static HTML — no widgets) ──────────────────────

def render_overview_table_html(
    rows: list[str],
    col_groups: list[tuple[str, list[str]]],
    cols_flat: list[str],
    cell_meta: dict[str, dict],
    selected_issue_key: str | None,
) -> str:
    """Render the Issue Overview as a static HTML `<table>`.

    Layout follows paper Table 3 (Basmer et al.): a corner cell, a
    group-header row spanning Events / Objects / Relations, a column-label
    row, and one body row per entry in `rows`. The 13 dimension column
    labels are rendered rotated 180° via `writing-mode:vertical-rl` (see
    `SUMMARY_TH_VERTICAL_STYLE`) so the table fits typical laptop widths
    without overflowing horizontally; the group headers stay horizontal
    because their `colspan` gives them room. `cell_meta` is keyed
    "r_idx:c_idx" and carries `{kind, count, row_label, col_label, issue_key}`
    for every cell — the same shape `overview_meta` builds in dashboard.py.

    - `kind == "none"` → renders `DASH_HTML`.
    - `kind == "na"` → renders `NA_HTML` (intentionally not applicable,
      distinct from `none` which merely means "no detector mapped yet").
    - `kind == "pending"` → renders `PENDING_HTML`.
    - `kind == "count"` → renders `pill_html(count)`.

    The cell whose `issue_key` equals `selected_issue_key` is styled with
    `SUMMARY_TD_SELECTED_STYLE` instead of `SUMMARY_TD_STYLE` so the user
    can visually locate the currently-focused issue in the paper layout.

    Because the table uses `border-collapse:collapse` (via
    `SUMMARY_TABLE_STYLE`), no `SUMMARY_NO_RIGHT_BORDER` /
    `SUMMARY_NO_BOTTOM_BORDER` suppression is applied — the browser handles
    shared-border deduplication.
    """
    # Group-header row: a rowspan=2 corner cell (covers the row-label
    # column across both header rows) + one <th colspan=len(cs)> per group.
    _group_cells = [
        f'<th rowspan="2" style="{SUMMARY_CORNER_STYLE}"></th>'
    ]
    for _name, _cs in col_groups:
        _group_cells.append(
            f'<th colspan="{len(_cs)}" style="{SUMMARY_GROUP_STYLE}">{_name}</th>'
        )
    _group_row = "<tr>" + "".join(_group_cells) + "</tr>"

    # Column-label row: one vertically-rotated <th> per data column. The
    # group row's rowspan=2 corner cell already covers the row-label
    # column's slot in this row, so no leading corner cell here.
    _col_cells = []
    for _c in cols_flat:
        _col_cells.append(f'<th style="{SUMMARY_TH_VERTICAL_STYLE}">{_c}</th>')
    _col_row = "<tr>" + "".join(_col_cells) + "</tr>"

    # Body rows.
    _body_rows: list[str] = []
    for _r_idx, _row_label in enumerate(rows):
        _row_cells = [
            f'<th style="{SUMMARY_ROW_LABEL_STYLE}">{_row_label}</th>'
        ]
        for _c_idx, _col in enumerate(cols_flat):
            _spec = cell_meta[f"{_r_idx}:{_c_idx}"]
            _kind = _spec["kind"]
            _is_selected = (
                _spec["issue_key"] is not None
                and _spec["issue_key"] == selected_issue_key
            )
            _style = (
                SUMMARY_TD_SELECTED_STYLE if _is_selected else SUMMARY_TD_STYLE
            )
            if _kind == "none":
                _content = DASH_HTML
            elif _kind == "na":
                _content = NA_HTML
            elif _kind == "pending":
                _content = PENDING_HTML
            else:
                _content = pill_html(_spec["count"])
            _row_cells.append(f'<td style="{_style}">{_content}</td>')
        _body_rows.append("<tr>" + "".join(_row_cells) + "</tr>")

    return (
        f'<table style="{SUMMARY_TABLE_STYLE}">'
        f"<thead>{_group_row}{_col_row}</thead>"
        f"<tbody>{''.join(_body_rows)}</tbody>"
        f"</table>"
    )


# ── Column-highlight predicates for the detector tables ──────────────────

def bad_col(col_name: str) -> Callable[[dict, str], bool]:
    return lambda _row, c: c == col_name


def bad_o2o(row: dict, c: str) -> bool:
    side = row["missing_side"]
    return (
        (c == "ocel_source_id" and side in ("source", "both"))
        or (c == "ocel_target_id" and side in ("target", "both"))
    )


def bad_e2o(row: dict, c: str) -> bool:
    side = row["missing_side"]
    return (
        (c == "ocel_event_id" and side in ("event", "both"))
        or (c == "ocel_object_id" and side in ("object", "both"))
    )


def bad_dup_id(_row: dict, c: str) -> bool:
    return c == "ocel_ids"


def bad_dup_attrs(_row: dict, c: str) -> bool:
    return c == "attribute_values"


def bad_self_loop(_row: dict, c: str) -> bool:
    # Highlight both endpoint columns — they're equal on a self-loop, and
    # that's the whole reason the row is flagged.
    return c in ("ocel_source_id", "ocel_target_id")


# Mapping of issue_key -> is_bad predicate. Central place so the drill-in
# renderer can pick the right highlighter from the issue_key alone.
IS_BAD_FOR_ISSUE: dict[str, Callable[[dict, str], bool]] = {
    "missing_event":                     bad_col("ocel_event_id"),
    "missing_event_type":                bad_col("ocel_type"),
    "missing_event_timestamp":           bad_col("actual_value"),
    "missing_event_attribute_value":     bad_col("actual_value"),
    "missing_object":                    bad_col("ocel_object_id"),
    "missing_object_type":               bad_col("ocel_type"),
    "missing_attribute_value":           bad_col("actual_value"),
    # Schema-suggestion detectors: the "missing thing" IS the attribute
    # name — highlight the column that carries it in the fanned-out
    # proposal rows.
    "missing_object_attribute":          bad_col("attribute"),
    "missing_event_attribute":           bad_col("attribute"),
    "dangling_o2o_relationship":         bad_o2o,
    "dangling_e2o_relationship":         bad_e2o,
    "duplicate_objects_on_ids":          bad_dup_id,
    "duplicate_objects_on_attributes":   bad_dup_attrs,
    "duplicate_events_on_ids":           bad_dup_id,
    "duplicate_events_on_attributes":    bad_dup_attrs,
    "incorrect_object_type":             bad_col("ocel_type"),
    "incorrect_event_type":              bad_col("ocel_type"),
    "incorrect_event_time":              bad_col("actual_value"),
    "incorrect_attribute_datatype":      bad_col("actual_value"),
    "incorrect_attribute_value":         bad_col("actual_value"),
    "incorrect_event_attribute_datatype": bad_col("actual_value"),
    "incorrect_event_attribute_value":    bad_col("actual_value"),
    # Rule-only relation-side "Incorrect Data" detectors.
    "duplicate_o2o_relations":            bad_col("count"),
    "o2o_self_loop":                      bad_self_loop,
    "duplicate_e2o_relations":            bad_col("count"),
}


# ── Detector table renderer (HTML) ───────────────────────────────────────

def render_detector_row_html(
    row: dict,
    cols: list[str],
    is_bad: Callable[[dict, str], bool],
    zebra: bool,
) -> str:
    """Render a single data row as an HTML `<table>` fragment.

    Used by the drill-in renderer to build per-row hstacks where a data
    table sits alongside per-row Confirm/Dismiss buttons. Wrapping each
    row in its own single-row table keeps borders + column widths
    consistent while letting marimo hstack the buttons in the same
    visual row (fixing the alignment problem in the vstack-based layout).
    """
    _zebra = "background:#fafbfc;" if zebra else ""
    cells = []
    for c in cols:
        style = DETECTOR_TD_STYLE + _zebra + (DETECTOR_BAD_STYLE if is_bad(row, c) else "")
        value = row[c]
        cells.append(
            f'<td style="{style}">{"null" if value is None else value}</td>'
        )
    return (
        f'<table style="{DETECTOR_TABLE_STYLE}"><tbody>'
        f'<tr>{"".join(cells)}</tr>'
        f'</tbody></table>'
    )


def render_detector_header_html(cols: list[str]) -> str:
    """Render just the column-header row as an HTML `<table>` fragment,
    used above the per-row hstacks in the drill-in."""
    head = "".join(f'<th style="{DETECTOR_TH_STYLE}">{c}</th>' for c in cols)
    return (
        f'<table style="{DETECTOR_TABLE_STYLE}"><thead>'
        f'<tr>{head}</tr>'
        f'</thead></table>'
    )


def render_detector_table(
    mo,
    key: str,
    df,
    is_bad: Callable[[dict, str], bool],
    pager_buttons,
    page_size: int,
    extra_row_marks: dict[int, str] | None = None,
):
    """Render a detector's Polars DataFrame as a paginated HTML table.

    Ported from the original `_render` helper in dashboard.py. Adds
    `extra_row_marks`: a dict of {slice-relative-row-index: mark_html} used
    by the drill-in to show a small "Confirmed" / "Dismissed" badge per row.
    Returns a marimo view (either a single table or table + pager controls).
    """
    if df.height == 0:
        return mo.md("_No violations found._")

    n = df.height
    max_page = max(0, (n - 1) // page_size)
    prev_btn = pager_buttons[f"{key}__prev"]
    next_btn = pager_buttons[f"{key}__next"]
    raw_page = (next_btn.value or 0) - (prev_btn.value or 0)
    page = min(max(raw_page, 0), max_page)
    start = page * page_size
    stop = min(start + page_size, n)
    slice_df = df.slice(start, stop - start)

    cols = df.columns
    head_cells = [f'<th style="{DETECTOR_TH_STYLE}">{c}</th>' for c in cols]
    if extra_row_marks is not None:
        head_cells.append(f'<th style="{DETECTOR_TH_STYLE}">Status</th>')
    head = "".join(head_cells)

    body_rows = []
    for i, row in enumerate(slice_df.iter_rows(named=True)):
        zebra = "background:#fafbfc;" if i % 2 else ""
        cells = []
        for c in cols:
            style = DETECTOR_TD_STYLE + zebra + (DETECTOR_BAD_STYLE if is_bad(row, c) else "")
            value = row[c]
            cells.append(
                f'<td style="{style}">{"null" if value is None else value}</td>'
            )
        if extra_row_marks is not None:
            mark = extra_row_marks.get(i, "")
            style = DETECTOR_TD_STYLE + zebra
            cells.append(f'<td style="{style}">{mark}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    rows = "".join(body_rows)

    table = mo.Html(
        f'<table style="{DETECTOR_TABLE_STYLE}">'
        f'<thead><tr>{head}</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )
    if n <= page_size:
        return table
    status = mo.Html(
        f'<div style="font-size:12px; color:#57606a; padding:0 8px;">'
        f"Page {page + 1} / {max_page + 1} &nbsp;·&nbsp; "
        f"rows {start + 1:,}–{stop:,} of {n:,}</div>"
    )
    controls = mo.hstack([prev_btn, status, next_btn], justify="start", gap=0.5)
    return mo.vstack([table, controls], gap=0.25)


def page_bounds(n: int, page_size: int, prev_clicks: int, next_clicks: int) -> tuple[int, int, int, int]:
    """Return (page, max_page, start, stop) using the same clamping logic
    as `render_detector_table`. Exposed so cells that create per-row buttons
    can align their button arrays to the currently-visible slice."""
    max_page = max(0, (n - 1) // page_size)
    raw_page = (next_clicks or 0) - (prev_clicks or 0)
    page = min(max(raw_page, 0), max_page)
    start = page * page_size
    stop = min(start + page_size, n)
    return page, max_page, start, stop


# ── Row identity (stable hash across detect_all reruns) ──────────────────

# Some detector rows carry large blobs (e.g. `attribute_values` for
# duplicate_objects_on_attributes). Hashing everything is fine correctness-
# wise but a whitelist keeps the identity stable if minor columns wobble.
# The `issue_key` is always folded in so merged N6 rows can't collide.
_ROW_IDENTITY_COLUMNS: dict[str, list[str]] = {
    # Empty list -> hash all columns.
}


def row_identity_columns(issue_key: str) -> list[str]:
    return _ROW_IDENTITY_COLUMNS.get(issue_key, [])


def row_hash(issue_key: str, row: dict) -> str:
    """Content-addressed 8-byte blake2b hex of a detector row.

    Stable across `detect_all` reruns for the same underlying data. Includes
    `issue_key` so rows from `duplicate_objects_on_ids` and
    `duplicate_objects_on_attributes` cannot collide in the merged N6 cell.
    Strips underscore-prefixed metadata keys (e.g. `_detected_suggestion`)
    which are display-only and vary in shape across sweeps.
    """
    keep = row_identity_columns(issue_key)
    filtered = {
        k: v for k, v in row.items()
        if not k.startswith("_") and (not keep or k in keep)
    }
    payload = _json.dumps(
        {"k": issue_key, "r": filtered}, sort_keys=True, default=str
    ).encode()
    return hashlib.blake2b(payload, digest_size=8).hexdigest()


# ── Fix action renderer (LLM suggestion → HTML card) ─────────────────────

def render_action(mo, action: dict):
    """Render an ActionResult dict as a marimo view. Ported from
    dashboard.py::expert_helpers::render_action."""
    if action["kind"] == "noop":
        return mo.md(
            f"**No-op suggestion** (confidence {action['confidence']:.2f}).\n\n"
            f"Rationale: {action['rationale']}"
        ).callout(kind="info")
    bar_pct = int(round(action["confidence"] * 100))
    confidence_bar = (
        f"<div style='margin-top:6px'><b>Confidence:</b> {action['confidence']:.2f}"
        f" <div style='display:inline-block; background:#eee; border-radius:4px; "
        f"width:200px; height:10px; vertical-align:middle; margin-left:8px;'>"
        f"<div style='background:#0969da; width:{bar_pct}%; height:10px; border-radius:4px;'></div>"
        f"</div></div>"
    )
    if action["kind"] == "delete":
        pk_str = ", ".join(f"{k}={v!r}" for k, v in action["target_pk"].items())
        return mo.Html(
            f"<div style='font-family:system-ui'>"
            f"<div><b>Kind:</b> delete &nbsp; <b>Table:</b> {action['target_table']}</div>"
            f"<div style='margin:6px 0'><b>Where:</b> <code>{pk_str}</code></div>"
            f"<div style='margin:4px 0; color:#cf222e;'>Keeps the first row (MIN rowid), deletes all duplicates.</div>"
            f"<div><b>Rationale:</b> {action['rationale']}</div>"
            f"{confidence_bar}</div>"
        )
    if action["kind"] == "insert":
        inserts = action.get("inserts") or []
        rows_html = []
        for ins in inserts:
            cols = ins.get("columns") or {}
            cells = " &nbsp; ".join(
                f"<b>{k}</b>=<code>{v!r}</code>" for k, v in cols.items()
            )
            rows_html.append(
                f"<div style='margin:4px 0'>→ <code>{ins.get('table', '')}</code>: {cells}</div>"
            )
        body = "".join(rows_html) or "<div><i>(no rows)</i></div>"
        return mo.Html(
            f"<div style='font-family:system-ui'>"
            f"<div><b>Kind:</b> insert &nbsp; <b>Rows:</b> {len(inserts)}</div>"
            f"<div style='margin:6px 0'>{body}</div>"
            f"<div><b>Rationale:</b> {action['rationale']}</div>"
            f"{confidence_bar}</div>"
        )
    if action["kind"] == "alter_add_column":
        # Schema addition: no `old_value` to show, and `new_value` carries
        # the SQLite affinity token rather than a row value.
        return mo.Html(
            f"<div style='font-family:system-ui'>"
            f"<div><b>Kind:</b> alter_add_column &nbsp; "
            f"<b>Table:</b> {action['target_table']} &nbsp; "
            f"<b>Column:</b> {action['column'] or '—'} &nbsp; "
            f"<b>Affinity:</b> {action['new_value'] or 'TEXT'}</div>"
            f"<div style='margin:6px 0'>Existing rows keep NULL for the new column; "
            f"the value-level missing-attribute detector will pick them up on the next sweep.</div>"
            f"<div><b>Rationale:</b> {action['rationale']}</div>"
            f"{confidence_bar}</div>"
        )
    return mo.Html(
        f"<div style='font-family:system-ui'>"
        f"<div><b>Kind:</b> {action['kind']} &nbsp; <b>Table:</b> {action['target_table']} "
        f"&nbsp; <b>Column:</b> {action['column'] or '—'}</div>"
        f"<div style='margin:6px 0'><b>Old →</b> <code>{action['old_value']!r}</code> "
        f"&nbsp; <b>New →</b> <code>{action['new_value']!r}</code></div>"
        f"<div><b>Rationale:</b> {action['rationale']}</div>"
        f"{confidence_bar}</div>"
    )


def is_routable(action: dict) -> bool:
    if action.get("kind") == "insert":
        return bool(action.get("inserts"))
    if action.get("kind") == "alter_add_column":
        # Schema addition: no target_pk (there is no row to route to), but
        # a table + column name is sufficient for apply_repair to run the
        # ALTER TABLE. new_value carries the affinity token (defaulted to
        # TEXT downstream) so its absence is not disqualifying.
        return bool(action.get("target_table") and action.get("column"))
    return (
        action.get("target_table")
        and action.get("column")
        and action.get("target_pk")
    )


def parse_override(text: str | None) -> Any:
    if text is None:
        return None
    stripped = text.strip()
    if stripped == "":
        return ""
    try:
        return _json.loads(stripped)
    except (ValueError, TypeError):
        return text


# ── Row-preview label used in the fix picker dropdown ────────────────────

def row_preview_label(row: dict, i: int) -> str:
    """One-line label for a detector row, used in the fix-picker dropdown."""
    preview_keys = [
        k for k in ("ocel_id", "ocel_event_id", "ocel_source_id", "ocel_ids")
        if k in row
    ]
    preview = ", ".join(f"{k}={row[k]}" for k in preview_keys[:2]) or f"row {i}"
    return f"#{i}  {preview}"
