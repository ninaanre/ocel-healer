import marimo

app = marimo.App(width="medium")


@app.cell
def imports():
    import os
    import marimo as mo
    from pathlib import Path
    from src.detection.error_detection import (
        _connect as connect_sqlite,
        _object_type_tables as object_type_tables,
        detect_all,
    )
    from src.llm import (
        MODEL,
        apply_repair,
        detect_all_with_llm,
        llm_ready,
        suggest_repair,
        set_active_model,
    )
    from src.exploration import explore_database, guide_is_stale, load_guide, load_report
    from src import dashboard_render as dr

    # Resolve the project root once so the dashboard works from any cwd.
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
    return (
        DATA_DIR,
        MODEL,
        apply_repair,
        connect_sqlite,
        detect_all,
        detect_all_with_llm,
        dr,
        explore_database,
        guide_is_stale,
        load_guide,
        load_report,
        mo,
        object_type_tables,
        llm_ready,
        os,
        suggest_repair,
        set_active_model,
    )


@app.cell
def issue_labels():
    # Single source of truth for user-facing issue names. The wording follows
    # the classification of Basmer et al., "Data Quality in Object-Centric
    # Event Data: Issues Classification and Evaluation" (extending the
    # 5th Intl. Workshop on Event Data and Behavioral Analytics, ICPM 2024) —
    # see Table 3 in the paper. Keys are the canonical issue_key strings
    # produced by src/detection/error_detection.py::detect_all and consumed
    # by src/llm/tasks/*; values are the labels shown in the Issue Overview
    # table, the drill-in section header, and the fix-picker dropdown.
    ISSUE_LABELS = {
        "missing_event":                     "Missing Event",
        "missing_event_type":                "Missing Event Type",
        "missing_event_timestamp":           "Missing Event Time",
        "missing_object":                    "Missing Object",
        "missing_object_type":               "Missing Object Type",
        "missing_attribute_value":           "Missing Object Attribute",
        "dangling_o2o_relationship":         "Missing Object-to-Object",
        "dangling_e2o_relationship":         "Missing Event-to-Object",
        "duplicate_objects_on_ids":          "Incorrect Object (by ID)",
        "duplicate_objects_on_attributes":   "Incorrect Object (by attributes)",
        "incorrect_object_type":             "Incorrect Object Type",
        "incorrect_attribute_datatype":      "Incorrect Object Attribute",
    }
    # Synthetic sentinel for the merged N6 cell (paper §4.2), which spans
    # both duplicate detectors under a single "Incorrect Object" heading.
    # DRILL_LABELS extends ISSUE_LABELS with the sentinel so the drill-in
    # section header renders the paper label rather than the raw key.
    N6_MERGED_KEY = "__n6_incorrect_object"
    N6_SUB_KEYS = ["duplicate_objects_on_ids", "duplicate_objects_on_attributes"]
    DRILL_LABELS = {**ISSUE_LABELS, N6_MERGED_KEY: "Incorrect Object"}
    return DRILL_LABELS, ISSUE_LABELS, N6_MERGED_KEY, N6_SUB_KEYS


@app.cell
def header(mo):
    mo.md("""
    # OCEL Error Detection & Resolution Dashboard
    Inspect rule-based data-quality violations in an object-centric event log,
    and ask a local LLM domain expert to suggest a repair for one.
    Pick a SQLite log and click a cell in the Issue Overview to drill into it.
    """)
    return


@app.cell
def llm_status(mo, llm_ready, set_active_model):
    reachable, available_models = llm_ready()
    if not reachable:
        _status = mo.md(
            "⚠️ LLMs not reachable. Start Ollama Desktop or activate the SSH tunnel."
        ).callout(kind="warn")
    else:
        _status = mo.md(
            f"✅ LLMs reachable — {len(available_models)} model(s) available."
        ).callout(kind="success")

    model_picker = mo.ui.dropdown(
        options=available_models if available_models else ["(none)"],
        value=available_models[0] if available_models else "(none)",
        label="Model",
    ) if reachable else None

    _picker_view = model_picker if reachable else mo.md("")
    mo.vstack([_status, _picker_view], gap=0.5)
    return model_picker, reachable


@app.cell
def llm_model_apply(model_picker, reachable, set_active_model):
    llm_enabled = reachable and model_picker is not None and model_picker.value not in (None, "(none)")
    if llm_enabled:
        set_active_model(model_picker.value)
    return (llm_enabled,)


@app.cell
def file_picker(DATA_DIR, mo, os):
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".sqlite"))
    default = "new.sqlite" if "new.sqlite" in files else (files[0] if files else None)
    file_picker = mo.ui.dropdown(options=files, value=default, label="OCEL File:")
    return (file_picker,)


@app.cell
def _top_bar(mo, file_picker):
    # Layout-only cell so the file picker sits above the Exploration section.
    mo.hstack([file_picker], justify="start", gap=1.5, align="end")
    return


@app.cell
def repair_trigger(mo):
    get_repair_tick, set_repair_tick = mo.state(0)
    return get_repair_tick, set_repair_tick


@app.cell
def selection_state(mo):
    # Which issue key (or the synthetic N6 sentinel) is currently drilled
    # into. None = nothing selected; the drill-in section then shows a
    # placeholder pointing the user at the Issue Overview.
    get_selected_issue, set_selected_issue = mo.state(None)
    return get_selected_issue, set_selected_issue


@app.cell
def overview_picker_state(mo):
    # Persisted (row_label, col_label) picks for the overview cascade,
    # plus the LLM sweep's picked object type. Widget rebuilds — which
    # fire whenever `overview_meta` recomputes (e.g. after any set_flags
    # write) or `ctx` transitions — preserve these via
    # `mo.ui.dropdown(value=…)`, so a Confirm click no longer wipes the
    # user's drill selection or type pick.
    get_overview_row, set_overview_row = mo.state(None)
    get_overview_col, set_overview_col = mo.state(None)
    get_picked_type, set_picked_type = mo.state(None)
    # (sqlite_path, issue_key, ocel_type) of the sweep whose "Judged N …
    # → K proposed flag(s)" banner is currently visible. Set to the just-
    # completed key by `drill_llm_sweep`; cleared to None by any user
    # interaction (Confirm, Dismiss, type-picker change, file switch).
    get_banner_key, set_banner_key = mo.state(None)
    return (
        get_banner_key,
        get_overview_col,
        get_overview_row,
        get_picked_type,
        set_banner_key,
        set_overview_col,
        set_overview_row,
        set_picked_type,
    )


@app.cell
def fix_row_selection_state(mo):
    # Flat index into `fix_rows` for the row currently queued for fixing.
    # Written either by the "Select" buttons next to each detection-table
    # row or by the searchable fix-row dropdown; both views read this
    # state so they stay in sync. Reset to 0 when the file or drilled-in
    # issue changes (see `fix_row_reset_on_context`).
    get_fix_row_idx, set_fix_row_idx = mo.state(0)
    return get_fix_row_idx, set_fix_row_idx


@app.cell
def selection_reset_on_file(
    file_picker,
    mo,
    set_banner_key,
    set_fix_row_idx,
    set_overview_col,
    set_overview_row,
    set_picked_type,
    set_selected_issue,
):
    # Side-effect cell: whenever the picked file changes, clear the drill-in
    # selection so nothing leaks across files. `get_flags` and
    # `get_sweep_ran` already key by path, so they don't need reset. The
    # overview / type pickers are backed by state (see
    # `overview_picker_state`) so we clear those explicitly here to avoid
    # a stale row/col/type appearing after switching files.
    get_prev_path, set_prev_path = mo.state("__init__")

    def _reset(current_path: str):
        set_selected_issue(None)
        set_fix_row_idx(0)
        set_overview_row(None)
        set_overview_col(None)
        set_picked_type(None)
        set_banner_key(None)
        set_prev_path(current_path)

    _current = file_picker.value or ""
    _prev = get_prev_path()
    if _prev != "__init__" and _prev != _current:
        _reset(_current)
    elif _prev == "__init__":
        # First view — record the initial path but don't wipe state.
        set_prev_path(_current)
    return


@app.cell
def fix_row_reset_on_issue(ctx, mo, set_fix_row_idx):
    """Reset the fix-row selection to 0 whenever the drilled-in issue
    key changes, so switching between issues doesn't leave a stale
    high index pointing past the new issue's `fix_rows` length."""
    get_prev_issue, set_prev_issue = mo.state("__init__")
    _current = ctx["issue_key"]
    _prev = get_prev_issue()
    if _prev == "__init__":
        set_prev_issue(_current)
    elif _prev != _current:
        set_fix_row_idx(0)
        set_prev_issue(_current)
    return


@app.cell
def load_results(DATA_DIR, detect_all, file_picker, get_repair_tick):
    # Detection re-runs automatically whenever the file changes (via
    # file_picker) or a repair is applied (via repair_tick). No manual
    # refresh button — Apply always bumps the tick, and the DAG rerenders.
    _ = get_repair_tick()
    sqlite_path = str(DATA_DIR / file_picker.value)
    results = detect_all(sqlite_path)
    return results, sqlite_path


@app.cell
def exploration_button(llm_enabled, mo):
    explore_btn = mo.ui.run_button(
        label="🔎 Run exploration", disabled=not llm_enabled
    )
    return (explore_btn,)


@app.cell
def exploration_run(DATA_DIR, explore_btn, explore_database, file_picker, load_guide, mo, model_picker):
    # Runs the exploration agent for the selected log (~0.5 min per object
    # type). The spinner subtitle mirrors the agent's per-section progress.
    exploration_result = None
    if explore_btn.value and file_picker.value:
        _db = str(DATA_DIR / file_picker.value)
        _model = model_picker.value if model_picker is not None else None
        try:
            with mo.status.spinner(title="Exploring the log…") as _spinner:
                def _tick(name, i, total, _s=_spinner):
                    _s.update(subtitle=f"{name} — step {i}/{total}")
                explore_database(
                    _db,
                    model=_model,
                    base_dir=DATA_DIR / "exploration",
                    on_progress=_tick,
                )
            _warnings = (load_guide(_db, DATA_DIR / "exploration") or {}).get("warnings", [])
            if _warnings:
                exploration_result = mo.md(
                    f"⚠️ Exploration finished with {len(_warnings)} warning(s) — "
                    f"some sections may be empty. Last: `{_warnings[-1]}`"
                ).callout(kind="warn")
            else:
                exploration_result = mo.md("✅ Exploration finished.").callout(kind="success")
        except Exception as e:
            exploration_result = mo.md(f"❌ Exploration failed: `{e}`").callout(kind="danger")
    return (exploration_result,)


@app.cell
def exploration_view(
    DATA_DIR, dr, exploration_result, explore_btn, file_picker, guide_is_stale, load_report, mo
):
    _base = DATA_DIR / "exploration"
    _db = str(DATA_DIR / file_picker.value) if file_picker.value else None
    _report = load_report(_db, _base) if _db else ""
    _stale = guide_is_stale(_db, _base) if _db else True

    if not _report:
        _status = mo.md(
            "No exploration report for this log yet. Run exploration once to give "
            "the repair agents log-specific context (id semantics, attribute hints)."
        ).callout(kind="neutral")
        _report_view = mo.md("")
    else:
        _status = (
            mo.md(
                "⚠️ The log's structure changed since this report was built — re-run exploration."
            ).callout(kind="warn")
            if _stale
            else mo.md(
                "✅ Exploration report is up to date — repair agents receive hints from it."
            ).callout(kind="success")
        )
        _report_view = mo.accordion({
            "Exploration report": mo.md(_report).style(
                {"max-height": "480px", "overflow-y": "auto", "padding-right": "1rem"}
            )
        })

    _section_header = mo.Html(dr.render_section_header_html(
        "Exploration",
        "Run once per file so the repair agents have log-specific hints "
        "(id semantics, attribute patterns).",
    ))

    mo.vstack(
        [
            _section_header,
            mo.hstack([explore_btn], justify="start"),
            exploration_result or mo.md(""),
            _status,
            _report_view,
        ],
        gap=0.5,
    )
    return


@app.cell
def pagers(mo, results):
    # One Prev/Next button pair per detector. Each button accumulates clicks
    # via on_click; current page = next.value - prev.value, clamped to the
    # valid range. The buttons are bundled into mo.ui.dictionary so that
    # clicks in one cell flow back through marimo's reactivity graph and
    # re-run the renderer below — a plain dict would NOT propagate clicks.
    PAGE_SIZE = 5

    pager_buttons = mo.ui.dictionary({
        f"{key}__prev": mo.ui.button(
            value=0, on_click=lambda v: v + 1, label="◀ Prev",
        )
        for key in results
    } | {
        f"{key}__next": mo.ui.button(
            value=0, on_click=lambda v: v + 1, label="Next ▶",
        )
        for key in results
    })
    return PAGE_SIZE, pager_buttons


@app.cell
def expert_state(mo):
    get_flags, set_flags = mo.state({})
    # Tracks (sqlite_path, issue_key) pairs for which the LLM sweep has been
    # run at least once — even when it flagged zero rows. Distinct from
    # `get_flags` (which only holds confirmed flags), so the Issue Overview
    # can tell "sweep hasn't run" apart from "sweep ran and found nothing".
    get_sweep_ran, set_sweep_ran = mo.state(set())
    return get_flags, get_sweep_ran, set_flags, set_sweep_ran


@app.cell
def sweep_result_state(mo):
    # Persistent per-sweep results, keyed by (sqlite_path, issue_key,
    # ocel_type). Value shape:
    #   {"proposals":     list[{"row": dict, "verdict": Verdict}],
    #    "summary_text":  str (markdown-ready),
    #    "dismissed":     set[str],   # ocel_id strings
    #    "total_judged":  int,
    #    "flagged_count": int,
    #    "chosen_type":   str,
    #    "order":         int}
    # `order` fixes per-type vertical position at first sweep so re-runs
    # don't reshuffle blocks. Kept in its own state so `set_sweep_result`
    # can atomically overwrite one key's entry without a race.
    get_sweep_result, set_sweep_result = mo.state({})
    get_sweep_ordinal, set_sweep_ordinal = mo.state(0)
    return (
        get_sweep_ordinal,
        get_sweep_result,
        set_sweep_ordinal,
        set_sweep_result,
    )


# ── Issue Overview (Basmer et al. Table 3 grid) ──────────────────────────
#
# Split into four cells so marimo's "no reading own value" rule holds:
#   overview_config   — static row / column schema
#   overview_meta     — computes per-cell {kind, count, issue_key, …}
#   overview_layout   — renders the static HTML overview table
#   overview_selector — dropdown widget that writes set_selected_issue

@app.cell
def overview_config():
    # Static schema for the overview grid. Kept in its own cell so both
    # `overview_meta` and `overview_layout` see identical rows/columns
    # without duplicating the constants.
    rows = ["Missing Data", "Incorrect Data", "Imprecise Data", "Irrelevant Data"]

    # Columns follow paper Table 3 (Basmer et al.): three OCED dimensions —
    # Events, Objects, Relations — with the columns inside each group listed
    # in paper order. `Event Attribute` under Events is included even
    # though no detector exists yet — it renders as dashes. The paper's
    # `Position` column is omitted per user preference (never populated).
    col_groups = [
        ("Events",    ["Event", "Event Type", "Event Time", "Event Attribute"]),
        ("Objects",   ["Object", "Object Type", "Object Attribute"]),
        ("Relations", ["Object-to-Object", "Event-to-Object"]),
    ]
    cols_flat = [c for _, cs in col_groups for c in cs]

    # (row_label, col_label) -> list[issue_key]
    mapping = {
        # Events row  (paper codes in trailing comments)
        ("Missing Data",   "Event"):            ["missing_event"],               # I2
        ("Missing Data",   "Event Type"):       ["missing_event_type"],          # I6
        ("Missing Data",   "Event Time"):       ["missing_event_timestamp"],     # I7
        # ("Missing Data", "Event Attribute"):  I9  — no detector yet
        # ("Missing Data", "Position"):         I5  — no detector yet

        # Objects row
        ("Missing Data",   "Object"):           ["missing_object"],              # N1
        ("Missing Data",   "Object Type"):      ["missing_object_type"],         # N2
        ("Missing Data",   "Object Attribute"): ["missing_attribute_value"],     # N3

        # Relations row
        ("Missing Data",   "Object-to-Object"): ["dangling_o2o_relationship"],   # N4
        ("Missing Data",   "Event-to-Object"):  ["dangling_e2o_relationship"],   # N5

        # Incorrect Data — N6 (paper §4.2) covers "erroneous duplicate" objects,
        # which is why both duplicate detectors live in a single cell here.
        ("Incorrect Data", "Object"):           [                                 # N6
            "duplicate_objects_on_ids", "duplicate_objects_on_attributes",
        ],
        ("Incorrect Data", "Object Type"):      ["incorrect_object_type"],       # N7
        ("Incorrect Data", "Object Attribute"): ["incorrect_attribute_datatype"],# N8
    }
    llm_detected_keys = {"incorrect_object_type"}
    return col_groups, cols_flat, llm_detected_keys, mapping, rows


@app.cell
def overview_meta(
    N6_MERGED_KEY,
    cols_flat,
    get_flags,
    get_sweep_ran,
    llm_detected_keys,
    mapping,
    results,
    rows,
    sqlite_path,
):
    """Compute per-cell state for the Issue Overview.

    Returns:
      overview_cell_meta — plain dict keyed "row_idx:col_idx" for every cell
        in the paper-Table-3 grid, carrying enough info for the layout cell
        to render its HTML and for the selector to build its options.

    Shape per entry:
      {"kind": "none" | "count" | "pending",
       "count": int | None,
       "row_label": str,
       "col_label": str,
       "issue_key": str | None}

    Cells with no mapped detector have kind == "none" (rendered as a dash).
    Interactive cells that resolve to a single detector store its
    issue_key; the N6 duplicate pair collapses into the synthetic
    `N6_MERGED_KEY` sentinel that drill_router already knows how to expand.
    """
    _llm_confirmed = sum(1 for k in get_flags() if k[0] == sqlite_path)
    _sweep_ran = get_sweep_ran()
    _llm_ever_run = any(
        (sqlite_path, key) in _sweep_ran for key in llm_detected_keys
    )

    def _cell_state(row, col):
        keys = mapping.get((row, col))
        if keys is None:
            return ("none", None)
        # A cell is "pending" only if every one of its underlying detectors
        # is LLM-based AND the sweep has not run for any of them.
        all_llm_pending = all(
            key in llm_detected_keys and not _llm_ever_run for key in keys
        )
        if all_llm_pending:
            return ("pending", None)
        total = 0
        for key in keys:
            if key in llm_detected_keys:
                total += _llm_confirmed
            else:
                total += results[key].height
        return ("count", total)

    def _issue_key_for(keys):
        # A cell may map to 1 issue key or (for N6) 2. Store the single key
        # directly; store the synthetic merged sentinel for the pair.
        if len(keys) == 1:
            return keys[0]
        if set(keys) == {"duplicate_objects_on_ids", "duplicate_objects_on_attributes"}:
            return N6_MERGED_KEY
        # Fallback — shouldn't happen with today's mapping.
        return keys[0]

    overview_cell_meta: dict[str, dict] = {}
    for _r_idx, _row in enumerate(rows):
        for _c_idx, _col in enumerate(cols_flat):
            _kind, _count = _cell_state(_row, _col)
            _key = f"{_r_idx}:{_c_idx}"
            if _kind == "none":
                overview_cell_meta[_key] = {
                    "kind": "none",
                    "count": None,
                    "row_label": _row,
                    "col_label": _col,
                    "issue_key": None,
                }
                continue
            _issue_key = _issue_key_for(mapping[(_row, _col)])
            overview_cell_meta[_key] = {
                "kind": _kind,          # "count" | "pending"
                "count": _count,        # int | None
                "row_label": _row,
                "col_label": _col,
                "issue_key": _issue_key,
            }
    return (overview_cell_meta,)


@app.cell
def overview_layout(
    col_groups,
    cols_flat,
    dr,
    get_selected_issue,
    mo,
    overview_cell_meta,
    rows,
):
    """Render the paper-Table-3 overview as a static HTML `<table>`.

    No widgets live inside cells — this cell is pure HTML. Interaction
    lives one cell down in `overview_selector` (a dropdown) which writes
    the same `set_selected_issue(...)` state. The cell whose issue_key
    matches the current selection is highlighted so the user can see
    where the focused issue sits in Basmer et al.'s classification.
    """
    _summary_header = mo.Html(dr.render_section_header_html(
        "Issue Overview",
        "Detected data-quality issues per category and dimension. "
        "Pick a category and dimension below to drill in.",
    ))

    _table_html = dr.render_overview_table_html(
        rows=rows,
        col_groups=col_groups,
        cols_flat=cols_flat,
        cell_meta=overview_cell_meta,
        selected_issue_key=get_selected_issue(),
    )

    mo.vstack([_summary_header, mo.Html(_table_html)], gap=0)
    return


@app.cell
def overview_selector(
    cols_flat,
    get_overview_row,
    mo,
    overview_cell_meta,
    rows,
    set_overview_row,
):
    """Row-first cascading dropdowns for the drill-in focus.

    Replaces the single "Focus on:" dropdown which grew unwieldy as issue
    types were added. The two dropdowns mirror the Basmer et al. Table 3
    grid rendered above: pick a data-quality *category* (row) first, then
    the *OCED dimension* (column). The picked (row, col) cell is
    resolved to an issue key downstream in `overview_selector_resolve`.

    The dropdown's value is mirrored in `get_overview_row`/`set_overview_row`
    so that widget rebuilds (which fire whenever `overview_meta` recomputes
    — e.g. after any `set_flags` write) don't clobber the user's selection.
    Empty combinations are hidden (rows with no populated columns are
    excluded from the row dropdown; columns not intersecting the picked
    row are excluded from the column dropdown).
    """
    # Inverse index: (row_label, col_label) -> cell meta. `overview_cell_meta`
    # is keyed by "r_idx:c_idx" — build a label-keyed view once so filtering
    # below reads cleanly.
    _by_labels: dict[tuple[str, str], dict] = {
        (_spec["row_label"], _spec["col_label"]): _spec
        for _spec in overview_cell_meta.values()
    }

    def _has_issues(row_label: str, col_label: str) -> bool:
        _spec = _by_labels.get((row_label, col_label))
        return _spec is not None and _spec["kind"] in ("count", "pending")

    # Row dropdown — restricted to rows with at least one populated column
    # under any group. Value = the row label itself (e.g. "Missing Data").
    # Rendered in `overview_column_selector` below alongside the column
    # picker so both dropdowns share one hstack row.
    _row_options = [_row for _row in rows if any(_has_issues(_row, _c) for _c in cols_flat)]
    _persisted_row = get_overview_row()
    _initial_row = _persisted_row if _persisted_row in _row_options else None
    row_picker_widget = mo.ui.dropdown(
        options=_row_options,
        label="Category:",
        value=_initial_row,
        on_change=set_overview_row,
    )
    return (row_picker_widget,)


@app.cell
def overview_column_selector(
    col_groups,
    get_overview_col,
    mo,
    overview_cell_meta,
    row_picker_widget,
    set_overview_col,
):
    """Column dropdown, cascaded from the row picker. Rebuilt whenever the
    row picker's value changes so its options only include columns that
    intersect the picked row and are non-empty.

    Mirrored in `get_overview_col`/`set_overview_col` so rebuilds
    triggered by upstream state changes (e.g. `set_flags` writes rippling
    through `overview_meta` → `overview_selector`) don't clobber the
    user's column pick."""
    _selected_row = row_picker_widget.value

    _by_labels: dict[tuple[str, str], dict] = {
        (_spec["row_label"], _spec["col_label"]): _spec
        for _spec in overview_cell_meta.values()
    }

    def _has_issues(row_label: str, col_label: str) -> bool:
        _spec = _by_labels.get((row_label, col_label))
        return _spec is not None and _spec["kind"] in ("count", "pending")

    # Options: for the picked row, iterate columns in paper order, prefix
    # them with the OCED group name (Events / Objects / Relations) so
    # "Event Type" reads distinctly from "Object Type".
    _col_options: dict[str, str] = {}  # label -> col
    if _selected_row is not None:
        for _group_name, _cols in col_groups:
            for _col in _cols:
                if not _has_issues(_selected_row, _col):
                    continue
                _col_options[f"{_group_name}: {_col}"] = _col

    _persisted_col = get_overview_col()
    _initial_col = _persisted_col if _persisted_col in _col_options.values() else None
    col_picker_widget = mo.ui.dropdown(
        options=_col_options,
        label="Dimension:",
        value=_initial_col,
        on_change=set_overview_col,
    )
    mo.hstack([row_picker_widget, col_picker_widget], justify="start", gap=1)
    return (col_picker_widget,)


@app.cell
def overview_selector_resolve(
    N6_MERGED_KEY,
    N6_SUB_KEYS,
    col_picker_widget,
    get_selected_issue,
    mapping,
    row_picker_widget,
    set_selected_issue,
):
    """Turn the (row, col) pick into an issue key and write it into
    `set_selected_issue`. Kept in its own cell so `overview_selector`
    only owns widget layout — downstream re-runs of the widgets don't
    re-fire until the user actually changes a selection."""
    _row = row_picker_widget.value
    _col = col_picker_widget.value

    def _issue_key_for(row_label, col_label):
        keys = mapping.get((row_label, col_label))
        if not keys:
            return None
        if len(keys) == 1:
            return keys[0]
        if set(keys) == set(N6_SUB_KEYS):
            return N6_MERGED_KEY
        return keys[0]

    if _row is None or _col is None:
        _resolved = None
    else:
        _resolved = _issue_key_for(_row, _col)

    if _resolved != get_selected_issue():
        set_selected_issue(_resolved)
    return


# ── Drill-in section ─────────────────────────────────────────────────────
#
# One section below the overview that owns detection + confirm/dismiss + fix
# for exactly one selected issue at a time. Modes:
#   "empty"           — no selection; render a placeholder.
#   "rule"            — a rule-based key with height > 0 (or the merged N6).
#   "rule_empty"      — a rule-based key clicked but detector returned 0 rows.
#   "llm_pending"     — an LLM-detected key; sweep has not run yet.
#   "llm_with_flags"  — an LLM-detected key with confirmed flags for this file.

@app.cell
def drill_router(
    N6_MERGED_KEY,
    N6_SUB_KEYS,
    get_flags,
    get_selected_issue,
    get_sweep_ran,
    llm_detected_keys,
    results,
    sqlite_path,
):
    _issue_key = get_selected_issue()

    def _rule_row_count(key):
        return results[key].height if key in results else 0

    ctx = {
        "mode": "empty",
        "issue_key": _issue_key,
        "sub_keys": [],
        "n_rows": 0,
    }
    if _issue_key is None:
        pass
    elif _issue_key == N6_MERGED_KEY:
        _total = sum(_rule_row_count(k) for k in N6_SUB_KEYS)
        ctx["mode"] = "rule" if _total > 0 else "rule_empty"
        ctx["sub_keys"] = list(N6_SUB_KEYS)
        ctx["n_rows"] = _total
    elif _issue_key in llm_detected_keys:
        _n_flags = sum(
            1 for k in get_flags()
            if k[0] == sqlite_path and get_flags()[k].get("issue") == _issue_key
        )
        _ever_ran = (sqlite_path, _issue_key) in get_sweep_ran()
        if _n_flags > 0:
            ctx["mode"] = "llm_with_flags"
            ctx["n_rows"] = _n_flags
        else:
            # Sweep may not have run, or it ran and flagged nothing. Either
            # way, the drill-in offers the sweep controls (`_ever_ran` may
            # be inspected by future UX to distinguish these).
            _ = _ever_ran
            ctx["mode"] = "llm_pending"
    else:
        # Rule-based single-key issue.
        _n = _rule_row_count(_issue_key)
        ctx["mode"] = "rule" if _n > 0 else "rule_empty"
        ctx["sub_keys"] = [_issue_key]
        ctx["n_rows"] = _n
    return (ctx,)


# ── LLM detection panel (only when mode == "llm_pending") ────────────────

@app.cell
def drill_llm_controls(
    ctx,
    get_picked_type,
    object_type_tables,
    connect_sqlite,
    llm_enabled,
    mo,
    set_banner_key,
    set_picked_type,
    sqlite_path,
):
    """Object-type picker + Run button. Available in ``llm_pending`` AND
    ``llm_with_flags`` — the mode transitions to ``llm_with_flags`` after
    the first Confirm, but the user may still want to run sweeps on other
    object types.

    The type picker's value is mirrored in `get_picked_type`/`set_picked_type`
    so mode transitions (which cause this cell to re-run and rebuild the
    widget) don't wipe the user's pick. Picking a different type also
    clears the sweep banner via `set_banner_key(None)`."""
    if ctx["mode"] not in ("llm_pending", "llm_with_flags"):
        # Widgets must still exist so downstream cells can read them, but
        # they don't need to be usable.
        type_picker = mo.ui.dropdown(options=[], label="Object type")
        detect_btn = mo.ui.run_button(label="Run detection", disabled=True)
    else:
        with connect_sqlite(sqlite_path) as _conn:
            _types = [t for t, _ in object_type_tables(_conn)]
        _persisted_type = get_picked_type()
        _initial_type = _persisted_type if _persisted_type in _types else None

        def _on_type_change(v):
            set_picked_type(v)
            set_banner_key(None)   # any user interaction hides the sweep banner

        type_picker = mo.ui.dropdown(
            options=_types,
            label="Object type",
            searchable=True,
            value=_initial_type,
            on_change=_on_type_change,
        )
        detect_btn = mo.ui.run_button(
            label="Run detection on selected type",
            disabled=not llm_enabled or not _types,
        )
    return detect_btn, type_picker


@app.cell
def drill_shell_top(
    DRILL_LABELS,
    ctx,
    detect_btn,
    mo,
    swept_types,
    type_picker,
):
    """Top half of the drill subsection: header + LLM controls (when in
    LLM mode) + a "Swept so far" affordance summarising which object
    types have already been checked. Rendered above ``drill_llm_sweep``
    in source order so the sweep cell's progress-bar slot lands
    immediately below this cell's output — inside the subsection, not
    between the overview and the header."""
    if ctx["mode"] == "empty":
        _top = mo.md("")   # empty mode is handled by drill_shell_bottom's callout
    else:
        _label = DRILL_LABELS.get(ctx["issue_key"], ctx["issue_key"])
        _header = mo.Html(
            '<div style="margin:24px 0 12px 0; padding-bottom:8px; '
            'border-bottom:1px solid #d0d7de;">'
            f'<div style="font-size:20px; font-weight:700; color:#1f2328; '
            f'letter-spacing:-0.01em;">{_label}</div>'
            f'<div style="margin-top:4px; color:#57606a; font-size:13px;">'
            f'Detected rows (treated as ground truth), followed by the fix '
            f'area.</div>'
            '</div>'
        )
        _parts: list = [_header]

        if ctx["mode"] in ("llm_pending", "llm_with_flags"):
            _parts.append(mo.hstack([type_picker, detect_btn], justify="start", gap=1))
            if swept_types:
                _bits = ", ".join(
                    f"`{_t}` ({_flagged}/{_total} flagged)"
                    for _t, _total, _flagged, _summary in swept_types
                )
                _parts.append(mo.md(
                    f"**Swept so far:** {_bits}. Pick another type to sweep, "
                    f"or re-select a swept type to re-run it."
                ))
        _top = mo.vstack(_parts, gap=0.5)
    _top
    return


@app.cell
def drill_llm_sweep(
    connect_sqlite,
    ctx,
    detect_all_with_llm,
    detect_btn,
    get_sweep_ordinal,
    get_sweep_ran,
    get_sweep_result,
    mo,
    set_banner_key,
    set_sweep_ordinal,
    set_sweep_ran,
    set_sweep_result,
    sqlite_path,
    type_picker,
):
    """Run the LLM sweep for the selected type. Writes the resulting
    entry into ``get_sweep_result`` keyed by (path, issue_key, type) so
    the panel below can render it on subsequent reactive cycles — after
    ``detect_btn.value`` has flipped back to falsy.

    The progress bar is opened here so its live output slot renders in
    this cell's position. Cell source order is arranged so this slot
    lands between the drill subsection header/controls (drill_shell_top)
    and the proposal cards / rule table / fix stack (drill_shell_bottom)."""
    _issue_key = ctx["issue_key"]
    if (
        ctx["mode"] in ("llm_pending", "llm_with_flags")
        and detect_btn.value
        and type_picker.value
        and _issue_key is not None
    ):
        chosen_type = type_picker.value
        with connect_sqlite(sqlite_path) as _conn:
            _ids = _conn.execute(
                "SELECT ocel_id FROM object "
                "WHERE ocel_type = ? AND ocel_id IS NOT NULL "
                "ORDER BY ocel_id",
                (chosen_type,),
            ).fetchall()
        candidates = [
            {"ocel_id": _oid, "ocel_type": chosen_type, "issue": _issue_key}
            for (_oid,) in _ids
        ]
        total = len(candidates)

        _proposals: list = []
        if total == 0:
            _summary_text = f"No objects found for type `{chosen_type}`."
            _verdict_count = 0
        else:
            flagged_count = [0]

            def _on_progress(i, _total, _row, verdict, bar):
                if verdict.flagged:
                    flagged_count[0] += 1
                bar.update(
                    increment=1,
                    subtitle=f"{i}/{_total} judged · {flagged_count[0]} flagged so far",
                )

            with mo.status.progress_bar(
                total=total,
                title=f"Judging {total} `{chosen_type}` object(s)…",
                subtitle=f"0/{total} judged · 0 flagged so far",
            ) as _bar:
                verdicts = detect_all_with_llm(
                    _issue_key,
                    candidates,
                    sqlite_path,
                    on_progress=lambda i, t, r, v: _on_progress(i, t, r, v, _bar),
                )
            for _row, _verdict in verdicts:
                if _verdict.flagged:
                    _proposals.append({"row": dict(_row), "verdict": _verdict})
            _verdict_count = len(verdicts)
            _summary_text = (
                f"Judged **{_verdict_count}** `{chosen_type}` object(s) → "
                f"**{len(_proposals)}** proposed flag(s)."
            )

        _key = (sqlite_path, _issue_key, chosen_type)
        _all = dict(get_sweep_result())
        # Preserve first-sweep vertical position on re-runs of the same type;
        # fresh types get the next ordinal.
        _existing = _all.get(_key)
        if _existing is not None:
            _order = _existing["order"]
        else:
            _order = get_sweep_ordinal() + 1
            set_sweep_ordinal(_order)
        _all[_key] = {
            "proposals":     _proposals,
            "summary_text":  _summary_text,
            "dismissed":     set(),   # re-sweep resets dismissals (per plan)
            "total_judged":  _verdict_count,
            "flagged_count": len(_proposals),
            "chosen_type":   chosen_type,
            "order":         _order,
        }
        set_sweep_result(_all)
        # Show the "Judged N … → K proposed flag(s)" banner for exactly
        # this sweep; cleared by any subsequent user interaction.
        set_banner_key((sqlite_path, _issue_key, chosen_type))

        _marker = (sqlite_path, _issue_key)
        _ran = set(get_sweep_ran())
        if _marker not in _ran:
            _ran.add(_marker)
            set_sweep_ran(_ran)
    return


@app.cell
def drill_llm_proposals(ctx, get_sweep_result, sqlite_path):
    """Aggregate persisted sweep entries for the current
    (sqlite_path, issue_key) across all swept types.

    ``proposals_all`` is a flat list — each entry gets a denormalized
    ``ocel_type`` field so downstream cells (buttons, renderer, observers)
    can trace it back to the right sweep entry. Order is by first-sweep
    ordinal, then by ocel_id within a type, for stable card positions.

    ``swept_types`` is a list of ``(ocel_type, total_judged, flagged_count,
    summary_text)`` in first-sweep order, driving the summary line and the
    swept-so-far affordance."""
    proposals_all: list = []
    swept_types: list = []
    _issue_key = ctx["issue_key"]
    if _issue_key is not None:
        _matches = [
            (k, v) for k, v in get_sweep_result().items()
            if k[0] == sqlite_path and k[1] == _issue_key
        ]
        _matches.sort(key=lambda kv: kv[1]["order"])
        for (_p, _ik, _t), _entry in _matches:
            swept_types.append(
                (_t, _entry["total_judged"], _entry["flagged_count"],
                 _entry["summary_text"])
            )
            _sorted_props = sorted(
                _entry["proposals"],
                key=lambda p: p["row"].get("ocel_id", "") or "",
            )
            for _p in _sorted_props:
                proposals_all.append({**_p, "ocel_type": _t})
    return proposals_all, swept_types


@app.cell
def drill_llm_confirm_buttons(mo, proposals_all):
    """Per-proposal Confirm/Dismiss buttons — mirrors the old
    expert_detection_buttons split (widgets here, values read downstream)."""
    if proposals_all:
        _n = len(proposals_all)
        agree_array = mo.ui.array([
            mo.ui.button(value=0, on_click=lambda v: v + 1, label="Confirm")
            for _ in range(_n)
        ])
        reject_array = mo.ui.array([
            mo.ui.button(value=0, on_click=lambda v: v + 1, label="Dismiss")
            for _ in range(_n)
        ])
    else:
        agree_array = None
        reject_array = None
    return agree_array, reject_array


@app.cell
def drill_llm_confirm_render(
    agree_array,
    ctx,
    get_banner_key,
    get_flags,
    get_sweep_result,
    mo,
    proposals_all,
    reject_array,
    sqlite_path,
    swept_types,
):
    """Render one summary line per swept type followed by any un-actioned
    proposal cards for that type. Confirmed proposals (present in
    ``get_flags``) are dropped entirely; dismissed proposals (present in
    the sweep entry's ``dismissed`` set) are also dropped.

    The per-type "Judged N … → K proposed flag(s)" callout is only
    emitted for the sweep whose key matches ``get_banner_key()`` — that
    key is set by ``drill_llm_sweep`` at completion and cleared by any
    subsequent user interaction (Confirm, Dismiss, type-picker change,
    file switch)."""
    llm_view = None
    if ctx["mode"] in ("llm_pending", "llm_with_flags") and swept_types:
        current_flags = get_flags()
        _sweep_all = get_sweep_result()
        _issue_key = ctx["issue_key"]
        _banner_key = get_banner_key()

        # Group proposals by ocel_type so each type's cards render under
        # its own summary line.
        _by_type: dict[str, list[tuple[int, dict]]] = {}
        for _i, _prop in enumerate(proposals_all):
            _by_type.setdefault(_prop["ocel_type"], []).append((_i, _prop))

        _parts: list = []
        for _t, _total, _flagged, _summary_text in swept_types:
            if (sqlite_path, _issue_key, _t) == _banner_key:
                _kind = "info" if _flagged else "success"
                _parts.append(mo.md(_summary_text).callout(kind=_kind))
            _entry = _sweep_all.get((sqlite_path, _issue_key, _t)) or {}
            _dismissed = _entry.get("dismissed", set())
            _cards: list = []
            for _i, _prop in _by_type.get(_t, []):
                _row = _prop["row"]
                _v = _prop["verdict"]
                _ocel_id = _row["ocel_id"]
                if (sqlite_path, _ocel_id) in current_flags:
                    continue   # confirmed → removed entirely
                if _ocel_id in _dismissed:
                    continue   # dismissed → hidden
                _bar_pct = int(round(_v.confidence * 100))
                _header = mo.Html(
                    "<div style='font-family:system-ui; padding:6px 0;'>"
                    f"<div><b>ocel_id:</b> <code>{_ocel_id}</code></div>"
                    f"<div style='margin:4px 0'>"
                    f"<b>Current:</b> <span style='background:#fde2e2; color:#b00020; "
                    f"padding:1px 6px; border-radius:3px;'>{_row.get('ocel_type')}</span>"
                    f" &nbsp;→&nbsp; "
                    f"<b>Suggested:</b> <span style='background:#e6ffec; color:#1a7f37; "
                    f"padding:1px 6px; border-radius:3px;'>{_v.suggested_value}</span>"
                    f"</div>"
                    f"<div><b>Rationale:</b> {_v.rationale}</div>"
                    f"<div style='margin-top:4px'><b>Confidence:</b> {_v.confidence:.2f}"
                    f" <span style='display:inline-block; background:#eee; border-radius:4px; "
                    f"width:160px; height:8px; vertical-align:middle; margin-left:6px;'>"
                    f"<span style='display:block; background:#0969da; width:{_bar_pct}%; "
                    f"height:8px; border-radius:4px;'></span></span></div>"
                    "</div>"
                )
                _controls = mo.hstack(
                    [agree_array[_i], reject_array[_i]],
                    justify="start",
                    gap=0.5,
                )
                _cards.append(mo.vstack([_header, _controls], gap=0.25))
                _cards.append(mo.md("---"))
            _parts.extend(_cards)
        llm_view = mo.vstack(_parts, gap=0.5)
    elif ctx["mode"] == "llm_pending":
        llm_view = mo.md(
            "_Pick an object type and click **Run detection** to start a sweep._"
        )
    return (llm_view,)


@app.cell
def drill_llm_agree_observer(
    agree_array, ctx, get_flags, proposals_all, set_banner_key, set_flags, sqlite_path,
):
    """When a Confirm button is clicked, add the proposal to get_flags()."""
    if (
        ctx["mode"] in ("llm_pending", "llm_with_flags")
        and proposals_all
        and agree_array is not None
    ):
        current = dict(get_flags())
        changed = False
        for _i, _prop in enumerate(proposals_all):
            _row = _prop["row"]
            _v = _prop["verdict"]
            _key = (sqlite_path, _row["ocel_id"])
            if agree_array[_i].value and _key not in current:
                current[_key] = {
                    "ocel_id": _row["ocel_id"],
                    "ocel_type": _row.get("ocel_type"),
                    "issue": ctx["issue_key"],
                    "_detected_suggestion": _v.suggested_value,
                    "_detected_rationale": _v.rationale,
                    "_detected_confidence": _v.confidence,
                }
                changed = True
        if changed:
            set_flags(current)
            set_banner_key(None)   # any user interaction hides the sweep banner
    return


@app.cell
def drill_llm_reject_observer(
    ctx,
    get_sweep_result,
    proposals_all,
    reject_array,
    set_banner_key,
    set_sweep_result,
    sqlite_path,
):
    """When a Dismiss button is clicked, add the proposal's ocel_id to
    the sweep entry's ``dismissed`` set so it stays hidden across
    subsequent reactive cycles (``reject_array[i].value`` is ephemeral
    and unusable as a source of truth on its own)."""
    if (
        ctx["mode"] in ("llm_pending", "llm_with_flags")
        and proposals_all
        and reject_array is not None
    ):
        _issue_key = ctx["issue_key"]
        _all = dict(get_sweep_result())
        _changed = False
        for _i, _prop in enumerate(proposals_all):
            if not reject_array[_i].value:
                continue
            _ocel_id = _prop["row"]["ocel_id"]
            _t = _prop["ocel_type"]
            _key = (sqlite_path, _issue_key, _t)
            _entry = _all.get(_key)
            if _entry is None:
                continue
            if _ocel_id in _entry["dismissed"]:
                continue
            _new_dismissed = set(_entry["dismissed"]) | {_ocel_id}
            _all[_key] = {**_entry, "dismissed": _new_dismissed}
            _changed = True
        if _changed:
            set_sweep_result(_all)
            set_banner_key(None)   # any user interaction hides the sweep banner
    return


# ── Rule-based drill-in path ─────────────────────────────────────────────

@app.cell
def drill_rule_source(ctx, get_flags, results, sqlite_path):
    """Assemble the (source_issue_key, row_dict) list for the drill-in
    table, honoring the merged N6 case and skipping unknown keys."""
    source_rows: list[tuple[str, dict]] = []
    if ctx["mode"] == "rule":
        for _k in ctx["sub_keys"]:
            if _k not in results:
                continue
            for _row in results[_k].iter_rows(named=True):
                source_rows.append((_k, dict(_row)))
    elif ctx["mode"] == "llm_with_flags":
        _issue = ctx["issue_key"]
        for (_path, _oid), _entry in get_flags().items():
            if _path == sqlite_path and _entry.get("issue") == _issue:
                source_rows.append((_issue, dict(_entry)))
    return (source_rows,)


@app.cell
def drill_rule_render(
    ISSUE_LABELS,
    N6_MERGED_KEY,
    PAGE_SIZE,
    ctx,
    dr,
    mo,
    pager_buttons,
    source_rows,
):
    """Render the detection table for the currently drilled-in issue.

    Every entry in `source_rows` is treated as ground truth — rule-based
    detectors are deterministic by construction, and LLM-detected rows
    are only in `source_rows` after the user confirmed them in the LLM
    proposal panel above. So this table has no per-row controls: every
    visible row is fix-eligible, and row selection happens in the
    searchable "Row" dropdown below. For the merged N6 case two stacked
    panels are shown, one per sub-detector.
    """
    if ctx["mode"] == "empty":
        rule_view = None
    elif ctx["mode"] == "rule_empty":
        rule_view = mo.md("_No violations found for this issue._").callout(kind="success")
    elif ctx["mode"] not in ("rule", "llm_with_flags"):
        rule_view = None
    elif not source_rows:
        # LLM mode with no confirmed proposals yet. The proposals panel
        # above (llm_view) already tells the user what to do; render nothing.
        rule_view = None
    else:
        # Group rows by source_issue_key so merged N6 renders as two
        # stacked panels.
        _by_source: dict[str, list[dict]] = {}
        for _src_key, _row in source_rows:
            _by_source.setdefault(_src_key, []).append(_row)

        _panels = []
        for _src_key, _entries in _by_source.items():
            _n_all = len(_entries)
            _prev_v = (pager_buttons[f"{_src_key}__prev"].value
                       if f"{_src_key}__prev" in pager_buttons.value else 0)
            _next_v = (pager_buttons[f"{_src_key}__next"].value
                       if f"{_src_key}__next" in pager_buttons.value else 0)
            _page, _max_page, _start, _stop = dr.page_bounds(
                _n_all, PAGE_SIZE, _prev_v or 0, _next_v or 0
            )
            _slice_entries = _entries[_start:_stop]

            # Column set derives from the first row (all detector rows in
            # the same DataFrame share columns by construction). Strip
            # underscore-prefixed metadata keys.
            if _slice_entries:
                _cols = [
                    _c for _c in _slice_entries[0].keys()
                    if not _c.startswith("_")
                ]
            else:
                _cols = []
            _is_bad = dr.IS_BAD_FOR_ISSUE.get(_src_key, lambda _r, _c: False)

            # Header row + one HTML row per detected violation. All in a
            # single mo.Html blob so vertical alignment is exact.
            _header_html = dr.render_detector_header_html(_cols)
            _row_htmls = [
                dr.render_detector_row_html(
                    _row, _cols, _is_bad, zebra=(_i % 2 == 1),
                )
                for _i, _row in enumerate(_slice_entries)
            ]
            _table_view = mo.Html(_header_html + "".join(_row_htmls))

            # Optional pager below.
            if _n_all > PAGE_SIZE:
                _pager = mo.hstack(
                    [
                        pager_buttons[f"{_src_key}__prev"],
                        mo.Html(
                            f'<div style="font-size:12px; color:#57606a; padding:0 8px;">'
                            f"Page {_page + 1} / {_max_page + 1} &nbsp;·&nbsp; "
                            f"rows {_start + 1:,}–{_stop:,} of {_n_all:,}</div>"
                        ),
                        pager_buttons[f"{_src_key}__next"],
                    ],
                    justify="start", gap=0.5,
                )
            else:
                _pager = None

            _panel_body_parts = [_table_view]
            if _pager is not None:
                _panel_body_parts.append(_pager)
            _panel_body = mo.vstack(_panel_body_parts, gap=0.25)

            if ctx["issue_key"] == N6_MERGED_KEY:
                _sub_label = ISSUE_LABELS.get(_src_key, _src_key)
                _panel = mo.vstack([
                    mo.Html(
                        f'<div style="margin:8px 0 4px 0; font-size:13px; font-weight:600; '
                        f'color:#57606a;">{_sub_label} '
                        f'{dr.badge_html(_n_all)}</div>'
                    ),
                    _panel_body,
                ], gap=0.25)
            else:
                _panel = _panel_body
            _panels.append(_panel)

        rule_view = mo.vstack(_panels, gap=0.75)
    return (rule_view,)


# ── Fix sub-section ──────────────────────────────────────────────────────

@app.cell
def drill_fix_rows(ctx, source_rows):
    """Collect the fix-eligible rows for the fix picker.

    The detection table is the ground truth: every row shown in it is
    fix-eligible. `source_rows` already handles the routing —
    rule-based issues get every row from `results[key]`, and
    LLM-detected issues only get rows the user confirmed in the
    proposals panel. So this cell just tags each row with its
    originating detector key and hands the list off.
    """
    fix_rows: list[dict] = []
    if ctx["mode"] in ("rule", "llm_with_flags"):
        for _src_key, _row in source_rows:
            _clean = {_k: _v for _k, _v in _row.items() if not _k.startswith("_")}
            _clean["_source_issue_key"] = _src_key
            fix_rows.append(_clean)
    return (fix_rows,)


@app.cell
def drill_fix_picker(ctx, dr, fix_rows, get_fix_row_idx, mo):
    """Searchable, full-width dropdown for choosing which detected row
    to fix. The user's pick is mirrored into `set_fix_row_idx` by the
    observer below so it survives cell reruns; when the drilled-in
    issue changes, `fix_row_reset_on_issue` clears the state back to 0.

    Two outputs:
      - `row_picker` — the widget (or a fallback `mo.md(...)`), used by
        downstream cells that read `.value`.
      - `row_picker_view` — the rendered layout for the shell (label +
        dropdown on the same row, with the dropdown taking the remaining
        horizontal space).
    """
    if ctx["mode"] == "empty":
        row_picker = mo.md("")
        row_picker_view = row_picker
    elif not fix_rows:
        # Only reachable in llm_with_flags mode where no proposals have
        # been confirmed yet — rule mode always has ≥1 row (else the
        # router would have chosen rule_empty).
        row_picker = mo.md(
            "_Confirm an LLM proposal above to enable the fix area._"
        ).callout(kind="neutral")
        row_picker_view = row_picker
    else:
        labels = [dr.row_preview_label(_row, _i) for _i, _row in enumerate(fix_rows)]
        _current_idx = get_fix_row_idx()
        # Clamp to a valid label — the state may still point at an index
        # from a previous (larger) issue we drilled into.
        if _current_idx is None or _current_idx < 0 or _current_idx >= len(labels):
            _current_idx = 0
        row_picker = mo.ui.dropdown(
            options=dict(zip(labels, range(len(labels)))),
            value=labels[_current_idx],
            searchable=True,
            full_width=True,
        )
        # Label sits inline to the left, dropdown fills the remaining
        # horizontal space (widths=[0, 1] gives the dropdown all the flex).
        row_picker_view = mo.hstack(
            [
                mo.Html(
                    '<div style="font-size:13px; font-weight:600; '
                    'color:#1f2328; min-width:48px;">Row</div>'
                ),
                row_picker,
            ],
            justify="start",
            align="center",
            gap=0.75,
            widths=[0, 1],
        )
    return row_picker, row_picker_view


@app.cell
def drill_fix_picker_observer(
    get_fix_row_idx, row_picker, set_fix_row_idx,
):
    """Mirror the dropdown's `.value` into shared state so the pick
    survives cell reruns (otherwise `drill_fix_picker` would rebuild the
    widget with `value=labels[0]` and overwrite the user's selection).
    Guarded so we only write when the widget's value diverges from state
    — otherwise the writeback would loop with the state-driven rebuild.
    """
    _widget_idx = getattr(row_picker, "value", None)
    if _widget_idx is None:
        pass
    elif _widget_idx != get_fix_row_idx():
        set_fix_row_idx(_widget_idx)
    return


@app.cell
def drill_fix_buttons(ctx, fix_rows, llm_enabled, mo):
    ask_btn = mo.ui.run_button(label="Ask domain expert", disabled=not llm_enabled)
    dry_run_toggle = mo.ui.switch(value=True, label="Dry run")
    apply_btn = mo.ui.run_button(label="Apply", kind="danger")
    show_buttons = (
        ctx["mode"] in ("rule", "llm_with_flags")
        and bool(fix_rows)
    )
    fix_buttons_view = (
        mo.hstack([ask_btn, dry_run_toggle, apply_btn], justify="start", gap=0.75)
        if show_buttons
        else mo.md("")
    )
    return apply_btn, ask_btn, dry_run_toggle, fix_buttons_view


@app.cell
def drill_fix_suggest(
    ask_btn,
    ctx,
    dr,
    fix_rows,
    llm_enabled,
    mo,
    row_picker,
    sqlite_path,
    suggest_repair,
):
    suggestion = None
    override_input = None
    suggest_view = None
    if ctx["mode"] not in ("rule", "llm_with_flags"):
        pass
    elif not llm_enabled:
        suggest_view = mo.md("_Enable Ollama to use the domain expert._")
    elif (
        ask_btn.value
        and fix_rows
        and getattr(row_picker, "value", None) is not None
    ):
        idx = row_picker.value
        if idx is None or idx >= len(fix_rows):
            suggest_view = mo.md("_Pick a row first._")
        else:
            _row = fix_rows[idx]
            _src_key = _row.get("_source_issue_key") or ctx["issue_key"]
            # Strip the tag before handing the row to the LLM layer.
            _clean = {k: v for k, v in _row.items() if not k.startswith("_")}
            try:
                suggestion = suggest_repair(_src_key, _clean, sqlite_path)
                action_view = dr.render_action(mo, suggestion)
                if dr.is_routable(suggestion):
                    override_input = mo.ui.text(
                        value="",
                        label="Override (empty = use suggestion; JSON or raw text)",
                        full_width=True,
                    )
                    suggest_view = mo.vstack(
                        [action_view, override_input],
                        gap=0.5,
                    )
                else:
                    suggest_view = action_view
            except Exception as e:
                suggest_view = mo.md(f"❌ LLM call failed: `{e}`").callout(kind="danger")
    return override_input, suggest_view, suggestion


@app.cell
def drill_fix_apply(
    apply_btn,
    apply_repair,
    ctx,
    dry_run_toggle,
    fix_rows,
    get_flags,
    get_repair_tick,
    mo,
    override_input,
    row_picker,
    set_flags,
    set_repair_tick,
    sqlite_path,
    suggestion,
):
    def _drop_row_from_state():
        """After a real (non-dry) apply, remove the fixed row from state
        so the drill-in refreshes with it gone. Rule rows disappear
        automatically once `set_repair_tick` re-runs `detect_all`, so we
        only need to prune LLM flags manually — they don't get re-derived."""
        _idx = getattr(row_picker, "value", None)
        if _idx is None or _idx >= len(fix_rows):
            return
        if ctx["mode"] != "llm_with_flags":
            return
        _row = fix_rows[_idx]
        _oid = _row.get("ocel_id")
        if _oid is None:
            return
        _current = dict(get_flags())
        _key = (sqlite_path, _oid)
        if _key in _current:
            del _current[_key]
            set_flags(_current)

    apply_view = None
    if (
        ctx["mode"] in ("rule", "llm_with_flags")
        and apply_btn.value
    ):
        if suggestion is None:
            apply_view = mo.md("_Run the domain expert first._")
        else:
            _raw = override_input.value if override_input is not None else ""
            _dry = dry_run_toggle.value
            _kwargs = {"dry_run": _dry}
            if _raw.strip() != "":
                _kwargs["override_value"] = dr.parse_override(_raw)
            try:
                _msg = apply_repair(sqlite_path, suggestion, **_kwargs)
                _kind = "info" if _dry else "success"
                _prefix = "" if _dry else "✅\n\n"
                _suffix = (
                    ""
                    if _dry
                    else "\n\nDetection has been re-run automatically."
                )
                apply_view = mo.md(
                    f"{_prefix}```sql\n{_msg}\n```{_suffix}"
                ).callout(kind=_kind)
                if not _dry:
                    _drop_row_from_state()
                    set_repair_tick(get_repair_tick() + 1)
            except Exception as e:
                apply_view = mo.md(f"❌ Apply failed: `{e}`").callout(kind="danger")
    return (apply_view,)


# ── Drill-in shell (the single visible container below the overview) ────

@app.cell
def drill_shell_bottom(
    apply_view,
    ctx,
    fix_buttons_view,
    llm_view,
    mo,
    row_picker_view,
    rule_view,
    suggest_view,
):
    """Bottom half of the drill subsection: LLM proposal cards +
    rule/LLM-with-flags detection table + fix area. Sits below
    ``drill_llm_sweep`` in source order so the progress bar renders
    between this and the header/controls above."""
    if ctx["mode"] == "empty":
        _bottom = mo.md(
            "_Pick a category and issue in the dropdowns above to drill in._"
        ).callout(kind="neutral")
    else:
        _parts: list = []

        # LLM sweep summary + proposal cards (only rendered in LLM modes).
        if llm_view is not None:
            _parts.append(llm_view)

        # Rule-based / LLM-with-flags: detection table + per-row buttons.
        if rule_view is not None:
            _parts.append(rule_view)

        # Fix area — picker + action buttons + suggestion view + apply view.
        _fix_stack: list = []
        _fix_stack.append(mo.md("### Fix a detected issue"))
        _fix_stack.append(row_picker_view if row_picker_view is not None else mo.md(""))
        _fix_stack.append(fix_buttons_view if fix_buttons_view is not None else mo.md(""))
        if suggest_view is not None:
            _fix_stack.append(suggest_view)
        if apply_view is not None:
            _fix_stack.append(apply_view)
        _parts.append(mo.vstack(_fix_stack, gap=0.5))

        _bottom = mo.vstack(_parts, gap=0.5)
    _bottom
    return


if __name__ == "__main__":
    app.run()
