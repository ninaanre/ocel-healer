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
        ollama_ready,
        suggest_repair,
    )

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
        mo,
        object_type_tables,
        ollama_ready,
        os,
        suggest_repair,
    )


@app.cell
def header(mo):
    mo.md("""
    # OCEL Error Detection Dashboard
    Inspect rule-based data-quality violations in an object-centric event log,
    and ask a local LLM domain expert to suggest a repair for one.
    Pick a SQLite log and browse the violations per detector.
    """)
    return


@app.cell
def llm_status(MODEL, mo, ollama_ready):
    reachable, models = ollama_ready()
    llm_enabled = reachable and MODEL in models
    if not reachable:
        status = mo.md(
            f"⚠️ Ollama not reachable. The domain-expert features are disabled. "
            f"Run `ollama serve` and `ollama pull {MODEL}` to enable them."
        ).callout(kind="warn")
    elif MODEL not in models:
        status = mo.md(
            f"⚠️ Ollama is up but model `{MODEL}` is not pulled. "
            f"Run `ollama pull {MODEL}`. Available: {models or 'none'}."
        ).callout(kind="warn")
    else:
        status = mo.md(f"✅ LLM ready: model `{MODEL}`.").callout(kind="success")
    status
    return (llm_enabled,)


@app.cell
def file_picker(DATA_DIR, mo, os):
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".sqlite"))
    default = "new.sqlite" if "new.sqlite" in files else (files[0] if files else None)
    file_picker = mo.ui.dropdown(options=files, value=default, label="OCEL file")
    file_picker
    return (file_picker,)


@app.cell
def refresh_btn(mo):
    # Click after applying a repair to re-run detection.
    refresh = mo.ui.refresh(label="Re-run detection", default_interval=None)
    refresh
    return (refresh,)


@app.cell
def load_results(DATA_DIR, detect_all, file_picker, refresh):
    _ = refresh.value  # subscribe so this cell re-runs on refresh.
    sqlite_path = str(DATA_DIR / file_picker.value)
    results = detect_all(sqlite_path)
    return results, sqlite_path


@app.cell
def pagers(mo, results):
    # One Prev/Next button pair per detector. Each button accumulates clicks
    # via on_click; current page = next.value - prev.value, clamped to the
    # valid range. The buttons are bundled into mo.ui.dictionary so that
    # clicks in one cell flow back through marimo's reactivity graph and
    # re-run the renderer below — a plain dict would NOT propagate clicks.
    PAGE_SIZE = 10

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
def sections(PAGE_SIZE, mo, pager_buttons, results):
    # ── table rendering ──────────────────────────────────────────────────────
    # Inline styles per <td>: with the per-tab page cap (10 rows) the total
    # rendered HTML stays under a few hundred KB even on the dirty OCEL log.
    # We avoid a single <style> block because marimo doesn't guarantee
    # passing <style> tags through (see docs: "use mo.iframe for styles").

    bad_style   = "background-color:#fde2e2; color:#b00020; font-weight:600;"
    table_style = (
        "border-collapse:collapse; font-family:system-ui,-apple-system,sans-serif; "
        "font-size:13px; border:1px solid #d0d7de; width:100%; table-layout:fixed;"
    )
    th_style = (
        "background:#f6f8fa; border:1px solid #d0d7de; padding:6px 10px; "
        "text-align:left; font-weight:600; "
        "overflow:hidden; text-overflow:ellipsis; word-break:break-word;"
    )
    td_style = (
        "border:1px solid #d0d7de; padding:6px 10px; "
        "overflow:hidden; text-overflow:ellipsis; word-break:break-word;"
    )

    def _cell_html(row, col, zebra, is_bad):
        style = td_style + zebra + (bad_style if is_bad(row, col) else "")
        value = row[col]
        return f'<td style="{style}">{"null" if value is None else value}</td>'

    def _render(key, df, is_bad):
        if df.height == 0:
            return mo.md("_No violations found._")
        n = df.height
        max_page = max(0, (n - 1) // PAGE_SIZE)
        prev_btn = pager_buttons[f"{key}__prev"]
        next_btn = pager_buttons[f"{key}__next"]
        # Page index derives from cumulative click counts; clamp to range so
        # repeated Prev at page 0 (or Next at the end) is a no-op.
        raw_page = (next_btn.value or 0) - (prev_btn.value or 0)
        page = min(max(raw_page, 0), max_page)
        start = page * PAGE_SIZE
        stop = min(start + PAGE_SIZE, n)
        slice_df = df.slice(start, stop - start)

        cols = df.columns
        head = "".join(f'<th style="{th_style}">{c}</th>' for c in cols)
        rows = "".join(
            "<tr>"
            + "".join(
                _cell_html(row, c, "background:#fafbfc;" if i % 2 else "", is_bad)
                for c in cols
            )
            + "</tr>"
            for i, row in enumerate(slice_df.iter_rows(named=True))
        )
        table = mo.Html(
            f'<table style="{table_style}">'
            f"<thead><tr>{head}</tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
        if n <= PAGE_SIZE:
            return table
        status = mo.Html(
            f'<div style="font-size:12px; color:#57606a; padding:0 8px;">'
            f"Page {page + 1} / {max_page + 1} &nbsp;·&nbsp; "
            f"rows {start + 1:,}–{stop:,} of {n:,}</div>"
        )
        controls = mo.hstack([prev_btn, status, next_btn], justify="start", gap=0.5)
        return mo.vstack([table, controls], gap=0.25)

    # ── highlight helpers ────────────────────────────────────────────────────

    def _bad_col(col_name):
        return lambda _row, c: c == col_name

    def _bad_o2o(row, c):
        side = row["missing_side"]
        return (
            (c == "ocel_source_id" and side in ("source", "both"))
            or (c == "ocel_target_id" and side in ("target", "both"))
        )

    def _bad_e2o(row, c):
        side = row["missing_side"]
        return (
            (c == "ocel_event_id"  and side in ("event",  "both"))
            or (c == "ocel_object_id" and side in ("object", "both"))
        )

    def _bad_dup_id(_row, c):
        return c == "ocel_ids"

    def _bad_dup_attrs(_row, c):
        return c == "attribute_values"

    # ── section builder ──────────────────────────────────────────────────────

    ACCENT = "#0969da"
    MUTED  = "#57606a"

    def _badge(n):
        colour = "#cf222e" if n > 0 else "#57606a"
        bg     = "#fde2e2" if n > 0 else "#f0f0f0"
        return (
            f'<span style="display:inline-block; padding:1px 8px; border-radius:10px; '
            f'font-size:12px; font-weight:600; color:{colour}; background:{bg};">{n}</span>'
        )

    def _section(title, checks):
        pills = "".join(
            f'<span style="color:{MUTED}; font-size:13px; margin-right:14px;">'
            f'{label}&nbsp;{_badge(df.height)}</span>'
            for label, _key, df, _ in checks
        )
        heading = mo.Html(
            f'<div style="border-left:3px solid {ACCENT}; padding:6px 0 6px 12px; '
            f'margin:28px 0 10px 0;">'
            f'<span style="font-size:15px; font-weight:700; color:#1f2328;">{title}</span>'
            f'<div style="margin-top:4px;">{pills}</div>'
            f'</div>'
        )
        tab_group = mo.ui.tabs(
            {label: _render(key, df, fn) for label, key, df, fn in checks}
        )
        return mo.vstack([heading, tab_group], gap=0)

    # ── three sections ───────────────────────────────────────────────────────

    attr_section = _section("Attributes", [
        ("Missing values",   "missing_attribute_value",   results["missing_attribute_value"],   _bad_col("actual_value")),
        ("Incorrect datatypes",  "incorrect_attribute_datatype",  results["incorrect_attribute_datatype"],  _bad_col("actual_value")),
    ])

    obj_section = _section("Objects", [
        ("Missing types",        "missing_object_type",              results["missing_object_type"],              _bad_col("ocel_type")),
        ("Duplicate IDs",        "duplicate_objects_on_ids",         results["duplicate_objects_on_ids"],         _bad_dup_id),
        ("Duplicate attributes", "duplicate_objects_on_attributes",  results["duplicate_objects_on_attributes"],  _bad_dup_attrs),
    ])

    rel_section = _section("Relationships", [
        ("Object → Object", "dangling_o2o_relationship", results["dangling_o2o_relationship"], _bad_o2o),
        ("Event → Object",  "dangling_e2o_relationship", results["dangling_e2o_relationship"], _bad_e2o),
    ])

    mo.vstack([attr_section, obj_section, rel_section], gap=0)
    return


@app.cell
def expert_header_and_tabs(mo):
    # Render the header and the Detection/Resolution tab strip together so
    # the switcher sits immediately under the section title — downstream
    # cells gated on `expert_tab.value` only fill the active tab body.
    expert_tab = mo.ui.tabs(
        {
            "Detection": mo.md(""),  # bodies are filled in by downstream cells
            "Resolution": mo.md(""),
        },
        value="Detection",
    )
    mo.vstack([
        mo.md("""
        ---
        ## Domain Expert (LLM)
        _Detection flags suspicious rows; Resolution proposes and applies repairs._
        """),
        expert_tab,
    ], gap=0.5)
    return (expert_tab,)


@app.cell
def expert_state(mo):
    # Confirmed `incorrect_object_type` flags, scoped to (sqlite_path, ocel_id).
    # In-memory only -- lost when the dashboard restarts. Each value is the
    # row dict (ocel_id + ocel_type + issue) so the Resolution tab can hand
    # it back to suggest_repair without rebuilding it.
    get_flags, set_flags = mo.state({})
    return get_flags, set_flags


@app.cell
def expert_helpers(mo):
    # Shared helpers used by both tabs. Defined once here so both downstream
    # cells can render action cards / parse override input consistently.
    import json as _json

    def render_action(action):
        if action["kind"] == "noop":
            return mo.md(
                f"**No-op suggestion** (confidence {action['confidence']:.2f}).\n\n"
                f"Rationale: {action['rationale']}"
            ).callout(kind="info")
        bar_pct = int(round(action["confidence"] * 100))
        return mo.Html(
            f"<div style='font-family:system-ui'>"
            f"<div><b>Kind:</b> {action['kind']} &nbsp; <b>Table:</b> {action['target_table']} "
            f"&nbsp; <b>Column:</b> {action['column'] or '—'}</div>"
            f"<div style='margin:6px 0'><b>Old →</b> <code>{action['old_value']!r}</code> "
            f"&nbsp; <b>New →</b> <code>{action['new_value']!r}</code></div>"
            f"<div><b>Rationale:</b> {action['rationale']}</div>"
            f"<div style='margin-top:6px'><b>Confidence:</b> {action['confidence']:.2f}"
            f" <div style='display:inline-block; background:#eee; border-radius:4px; "
            f"width:200px; height:10px; vertical-align:middle; margin-left:8px;'>"
            f"<div style='background:#0969da; width:{bar_pct}%; height:10px; border-radius:4px;'></div>"
            f"</div></div></div>"
        )

    def is_routable(action):
        return (
            action.get("target_table")
            and action.get("column")
            and action.get("target_pk")
        )

    def parse_override(text):
        if text is None:
            return None
        stripped = text.strip()
        if stripped == "":
            return ""
        try:
            return _json.loads(stripped)
        except (ValueError, TypeError):
            return text

    return is_routable, parse_override, render_action


# ── Detection tab ────────────────────────────────────────────────────────

@app.cell
def expert_detection_controls(
    object_type_tables, connect_sqlite, expert_tab, llm_enabled, mo, sqlite_path,
):
    # One-type-at-a-time sweep: pick a type, judge every instance of it.
    # Loading the dropdown options is cheap (a single SELECT on object_map_type)
    # so we always do it; the button stays disabled until LLM + a type are ready.
    with connect_sqlite(sqlite_path) as _conn:
        _types = [t for t, _ in object_type_tables(_conn)]
    type_picker = mo.ui.dropdown(
        options=_types,
        label="Object type",
        searchable=True,
    )
    detect_btn = mo.ui.run_button(
        label="Run detection on selected type",
        disabled=not llm_enabled or not _types,
    )
    _controls_view = (
        mo.hstack([type_picker, detect_btn], justify="start", gap=1)
        if expert_tab.value == "Detection"
        else mo.md("")
    )
    _controls_view
    return detect_btn, type_picker


@app.cell
def expert_detection_sweep(
    connect_sqlite,
    detect_all_with_llm,
    detect_btn,
    expert_tab,
    mo,
    sqlite_path,
    type_picker,
):
    # Run the sweep when the button has been clicked. Only fires when the
    # Detection tab is active so switching to Resolution doesn't re-run.
    # Pulls every ocel_id of the chosen type and judges each one; progress
    # bar shows running flagged count so big sweeps stay legible.
    proposals: list = []
    sweep_summary = None
    if (
        expert_tab.value == "Detection"
        and detect_btn.value
        and type_picker.value
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
            {"ocel_id": _oid, "ocel_type": chosen_type, "issue": "incorrect_object_type"}
            for (_oid,) in _ids
        ]
        total = len(candidates)
        if total == 0:
            sweep_summary = mo.md(
                f"No objects found for type **`{chosen_type}`**."
            ).callout(kind="warn")
            verdicts: list = []
        else:
            flagged_count = [0]  # boxed so the progress callback can mutate it

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
                    "incorrect_object_type",
                    candidates,
                    sqlite_path,
                    on_progress=lambda i, t, r, v: _on_progress(i, t, r, v, _bar),
                )
            for _row, _verdict in verdicts:
                if _verdict.flagged:
                    proposals.append({
                        "row": dict(_row),
                        "verdict": _verdict,
                    })
            sweep_summary = mo.md(
                f"Judged **{len(verdicts)}** `{chosen_type}` object(s) → "
                f"**{len(proposals)}** proposed flag(s)."
            ).callout(kind="info" if proposals else "success")
    return proposals, sweep_summary


@app.cell
def expert_detection_buttons(mo, proposals):
    # Create the Agree/Reject button arrays in their own cell. The render
    # cell below needs to read `reject_array[i].value` to hide rejected
    # cards, and marimo forbids reading a UIElement's value in the cell
    # that created it — so creation lives here, reads live downstream.
    if proposals:
        n = len(proposals)
        agree_array = mo.ui.array([
            mo.ui.button(value=0, on_click=lambda v: v + 1, label="Agree")
            for _ in range(n)
        ])
        reject_array = mo.ui.array([
            mo.ui.button(value=0, on_click=lambda v: v + 1, label="Reject")
            for _ in range(n)
        ])
    else:
        agree_array = None
        reject_array = None
    return agree_array, reject_array


@app.cell
def expert_detection_render(
    agree_array,
    expert_tab,
    get_flags,
    mo,
    proposals,
    reject_array,
    sqlite_path,
    sweep_summary,
):
    detection_view = None
    if expert_tab.value == "Detection":
        if not proposals:
            detection_view = sweep_summary or mo.md(
                "_Click **Run detection** to start a sweep._"
            )
        else:
            cards = []
            current_flags = get_flags()
            for _i, _prop in enumerate(proposals):
                _row = _prop["row"]
                _v = _prop["verdict"]
                _ocel_id = _row["ocel_id"]
                _key = (sqlite_path, _ocel_id)
                # User clicked Reject on this card — hide it for this sweep.
                # (Reject is transient; rerunning the sweep can resurface it.)
                if reject_array[_i].value:
                    continue
                _already = _key in current_flags
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
                if _already:
                    _controls = mo.md("**Confirmed** — see Resolution tab.").callout(kind="success")
                else:
                    _controls = mo.hstack(
                        [agree_array[_i], reject_array[_i]],
                        justify="start",
                        gap=0.5,
                    )
                cards.append(mo.vstack([_header, _controls], gap=0.25))
                cards.append(mo.md("---"))
            detection_view = mo.vstack([sweep_summary, *cards], gap=0.5)

    detection_view
    return


@app.cell
def expert_detection_agree(
    agree_array, get_flags, proposals, set_flags, sqlite_path,
):
    if proposals and agree_array is not None:
        current = dict(get_flags())
        changed = False
        for _i, _prop in enumerate(proposals):
            _row = _prop["row"]
            _v = _prop["verdict"]
            _key = (sqlite_path, _row["ocel_id"])
            if agree_array[_i].value and _key not in current:
                current[_key] = {
                    "ocel_id": _row["ocel_id"],
                    "ocel_type": _row.get("ocel_type"),
                    "issue": "incorrect_object_type",
                    "_detected_suggestion": _v.suggested_value,
                    "_detected_rationale": _v.rationale,
                    "_detected_confidence": _v.confidence,
                }
                changed = True
        if changed:
            set_flags(current)
    return


# ── Resolution tab ───────────────────────────────────────────────────────

@app.cell
def expert_pickers(expert_tab, get_flags, mo, results, sqlite_path):
    # In Resolution mode, expose every detector that has rows to act on,
    # PLUS `incorrect_object_type` if the user has confirmed any flags.
    # Sorted alphabetically so the dropdown order is stable / predictable.
    available = sorted(
        k for k, df in results.items()
        if df.height > 0 and k != "incorrect_object_type"
    )
    n_confirmed = sum(1 for k in get_flags() if k[0] == sqlite_path)
    if n_confirmed > 0:
        available.append("incorrect_object_type")
        available.sort()
    detector = mo.ui.dropdown(
        options=available, value=(available[0] if available else None),
        label="Issue type",
    )
    if expert_tab.value != "Resolution":
        _pickers_view = mo.md("")
    elif not available:
        _pickers_view = mo.md(
            "_No violations to resolve. Run the deterministic "
            "detectors or the Detection tab first._"
        )
    else:
        _pickers_view = detector
    _pickers_view
    return (detector,)


@app.cell
def expert_resolution_rows(detector, get_flags, results, sqlite_path):
    # Resolution row source: confirmed flags for incorrect_object_type,
    # detector results otherwise. Returns a list of row-dicts so both
    # branches use the same downstream picker.
    res_rows: list = []
    if detector.value == "incorrect_object_type":
        for (path, _ocel_id), entry in get_flags().items():
            if path == sqlite_path:
                # Strip the underscore-prefixed display-only metadata before
                # handing the row to suggest_repair / apply_repair.
                res_rows.append({
                    k: v for k, v in entry.items() if not k.startswith("_")
                })
    elif detector.value is not None:
        df = results[detector.value]
        res_rows = [dict(r) for r in df.iter_rows(named=True)]
    return (res_rows,)


@app.cell
def expert_row_picker(expert_tab, mo, res_rows):
    if not res_rows:
        row_picker = mo.md("_No rows to pick from._")
    else:
        labels = []
        for _i, _row in enumerate(res_rows):
            _keys = [k for k in ("ocel_id", "ocel_event_id", "ocel_source_id", "ocel_ids") if k in _row]
            _preview = ", ".join(f"{k}={_row[k]}" for k in _keys[:2]) or f"row {_i}"
            labels.append(f"#{_i}  {_preview}")
        row_picker = mo.ui.dropdown(
            options=dict(zip(labels, range(len(labels)))),
            value=labels[0] if labels else None,
            label="Row",
        )
    _row_picker_view = row_picker if expert_tab.value == "Resolution" else mo.md("")
    _row_picker_view
    return (row_picker,)


@app.cell
def expert_buttons(expert_tab, llm_enabled, mo):
    ask_btn = mo.ui.run_button(label="Ask domain expert", disabled=not llm_enabled)
    dry_run_toggle = mo.ui.switch(value=True, label="Dry run")
    apply_btn = mo.ui.run_button(label="Apply", kind="danger")
    _buttons_view = (
        mo.hstack(
            [ask_btn, dry_run_toggle, apply_btn],
            justify="start",
            gap=0.75,
        )
        if expert_tab.value == "Resolution"
        else mo.md("")
    )
    _buttons_view
    return apply_btn, ask_btn, dry_run_toggle


@app.cell
def expert_suggest(
    ask_btn,
    detector,
    expert_tab,
    is_routable,
    llm_enabled,
    mo,
    render_action,
    res_rows,
    row_picker,
    sqlite_path,
    suggest_repair,
):
    suggestion = None
    override_input = None
    suggest_view = None
    if expert_tab.value != "Resolution":
        pass  # only Resolution tab drives this cell
    elif not llm_enabled:
        suggest_view = mo.md("_Enable Ollama to use the domain expert._")
    elif (
        ask_btn.value
        and detector.value is not None
        and getattr(row_picker, "value", None) is not None
    ):
        idx = row_picker.value
        if idx is None or idx >= len(res_rows):
            suggest_view = mo.md("_Pick a row first._")
        else:
            _row = res_rows[idx]
            try:
                suggestion = suggest_repair(detector.value, _row, sqlite_path)
                action_view = render_action(suggestion)
                if is_routable(suggestion):
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
    _suggest_outer = suggest_view if expert_tab.value == "Resolution" else mo.md("")
    _suggest_outer
    return override_input, suggestion


@app.cell
def expert_apply(
    apply_btn,
    apply_repair,
    detector,
    dry_run_toggle,
    expert_tab,
    get_flags,
    mo,
    override_input,
    parse_override,
    res_rows,
    row_picker,
    set_flags,
    sqlite_path,
    suggestion,
):
    def _drop_confirmed_flag_if_needed():
        """After committing a fix on an `incorrect_object_type` confirmed
        flag, remove it from the in-memory store so the Resolution tab
        refreshes. No-op for any other detector."""
        if detector.value != "incorrect_object_type":
            return
        _idx = getattr(row_picker, "value", None)
        if _idx is None or _idx >= len(res_rows):
            return
        _oid = res_rows[_idx].get("ocel_id")
        if _oid is None:
            return
        _current = dict(get_flags())
        if (sqlite_path, _oid) in _current:
            del _current[(sqlite_path, _oid)]
            set_flags(_current)

    apply_view = None
    if expert_tab.value == "Resolution" and apply_btn.value:
        if suggestion is None:
            apply_view = mo.md("_Run the domain expert first._")
        else:
            # Empty override input -> call apply_repair WITHOUT override_value
            # so it uses the LLM suggestion's new_value. Passing
            # override_value=None would be interpreted as "set the column to
            # NULL" because apply_repair uses a sentinel to detect "unset".
            _raw = override_input.value if override_input is not None else ""
            _dry = dry_run_toggle.value
            _kwargs = {"dry_run": _dry}
            if _raw.strip() != "":
                _kwargs["override_value"] = parse_override(_raw)
            try:
                _msg = apply_repair(sqlite_path, suggestion, **_kwargs)
                _kind = "info" if _dry else "success"
                _prefix = "" if _dry else "✅\n\n"
                _suffix = (
                    ""
                    if _dry
                    else "\n\nClick **Re-run detection** above to refresh the tables."
                )
                apply_view = mo.md(
                    f"{_prefix}```sql\n{_msg}\n```{_suffix}"
                ).callout(kind=_kind)
                if not _dry:
                    _drop_confirmed_flag_if_needed()
            except Exception as e:
                apply_view = mo.md(f"❌ Apply failed: `{e}`").callout(kind="danger")
    apply_view
    return


if __name__ == "__main__":
    app.run()
