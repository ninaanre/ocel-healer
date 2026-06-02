import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def imports():
    import os
    import marimo as mo
    from pathlib import Path
    from src.detection.error_detection import detect_all
    from src.llm import apply_repair, ollama_ready, suggest_repair, MODEL

    # Resolve the project root once so the dashboard works from any cwd.
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
    return (
        DATA_DIR,
        MODEL,
        apply_repair,
        detect_all,
        mo,
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
def sections(mo, results):
    # ── table rendering ──────────────────────────────────────────────────────

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

    def _render(df, is_bad):
        if df.height == 0:
            return mo.md("_No violations found._")
        cols = df.columns
        head = "".join(f'<th style="{th_style}">{c}</th>' for c in cols)
        rows = "".join(
            "<tr>"
            + "".join(
                _cell_html(row, c, "background:#fafbfc;" if i % 2 else "", is_bad)
                for c in cols
            )
            + "</tr>"
            for i, row in enumerate(df.iter_rows(named=True))
        )
        return mo.Html(
            f'<table style="{table_style}">'
            f"<thead><tr>{head}</tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

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
            for label, df, _ in checks
        )
        heading = mo.Html(
            f'<div style="border-left:3px solid {ACCENT}; padding:6px 0 6px 12px; '
            f'margin:28px 0 10px 0;">'
            f'<span style="font-size:15px; font-weight:700; color:#1f2328;">{title}</span>'
            f'<div style="margin-top:4px;">{pills}</div>'
            f'</div>'
        )
        tab_group = mo.ui.tabs(
            {label: _render(df, fn) for label, df, fn in checks}
        )
        return mo.vstack([heading, tab_group], gap=0)

    # ── three sections ───────────────────────────────────────────────────────

    attr_section = _section("Attributes", [
        ("Missing values",   results["missing_attributes"],  _bad_col("actual_value")),
        ("Wrong datatypes",  results["incorrect_datatypes"], _bad_col("actual_value")),
    ])

    obj_section = _section("Objects", [
        ("Missing types",        results["missing_object_types"],        _bad_col("ocel_type")),
        ("Duplicate IDs",        results["duplicate_object_ids"],        _bad_dup_id),
        ("Duplicate attributes", results["duplicate_object_attributes"], _bad_dup_attrs),
    ])

    rel_section = _section("Relationships", [
        ("Object → Object", results["dangling_o2o_relations"], _bad_o2o),
        ("Event → Object",  results["dangling_e2o_relations"], _bad_e2o),
    ])

    mo.vstack([attr_section, obj_section, rel_section], gap=0)
    return


@app.cell
def expert_header(mo):
    mo.md("""
    ---
    ## Domain Expert (LLM)
    Pick a detector and a violation row, then ask the LLM to suggest a
    repair. Suggestions are dry-run by default — review the SQL before
    committing.
    """)
    return


@app.cell
def expert_pickers(mo, results):
    # Only offer detectors that actually have violations to act on.
    available = [k for k, df in results.items() if df.height > 0]
    detector = mo.ui.dropdown(
        options=available, value=(available[0] if available else None),
        label="Detector",
    )
    detector
    return (detector,)


@app.cell
def expert_row_picker(detector, mo, results):
    if detector.value is None:
        row_picker = mo.md("_No violations to pick from._")
    else:
        _df = results[detector.value]
        _labels = []
        for _i, _row in enumerate(_df.iter_rows(named=True)):
            _keys = [k for k in ("ocel_id", "ocel_event_id", "ocel_source_id", "ocel_ids") if k in _row]
            _preview = ", ".join(f"{k}={_row[k]}" for k in _keys[:2]) or f"row {_i}"
            _labels.append(f"#{_i}  {_preview}")
        row_picker = mo.ui.dropdown(
            options=dict(zip(_labels, range(len(_labels)))),
            value=_labels[0] if _labels else None,
            label="Violation",
        )
    row_picker
    return (row_picker,)


@app.cell
def expert_buttons(llm_enabled, mo):
    ask_btn = mo.ui.run_button(label="Ask domain expert", disabled=not llm_enabled)
    apply_dryrun_btn = mo.ui.run_button(label="Apply (dry-run)")
    apply_commit_btn = mo.ui.run_button(label="Apply (commit)", kind="danger")
    mo.hstack([ask_btn, apply_dryrun_btn, apply_commit_btn], justify="start")
    return apply_commit_btn, apply_dryrun_btn, ask_btn


@app.cell
def expert_suggest(
    ask_btn,
    detector,
    llm_enabled,
    mo,
    results,
    row_picker,
    sqlite_path,
    suggest_repair,
):
    def _render(action):
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

    suggestion = None
    suggest_view = None
    if not llm_enabled:
        suggest_view = mo.md("_Enable Ollama to use the domain expert._")
    elif ask_btn.value and detector.value is not None and getattr(row_picker, "value", None) is not None:
        _df = results[detector.value]
        _idx = row_picker.value
        if _idx is None or _idx >= _df.height:
            suggest_view = mo.md("_Pick a violation row first._")
        else:
            _row = dict(_df.row(_idx, named=True))
            try:
                suggestion = suggest_repair(detector.value, _row, sqlite_path)
                suggest_view = _render(suggestion)
            except Exception as e:
                suggest_view = mo.md(f"❌ LLM call failed: `{e}`").callout(kind="danger")
    suggest_view
    return (suggestion,)


@app.cell
def expert_apply(
    apply_commit_btn,
    apply_dryrun_btn,
    apply_repair,
    mo,
    sqlite_path,
    suggestion,
):
    apply_view = None
    if suggestion is None:
        if apply_dryrun_btn.value or apply_commit_btn.value:
            apply_view = mo.md("_Run the domain expert first._")
    elif apply_commit_btn.value:
        try:
            _msg = apply_repair(sqlite_path, suggestion, dry_run=False)
            apply_view = mo.md(f"✅\n\n```sql\n{_msg}\n```\n\nClick **Re-run detection** above to refresh the tables.").callout(kind="success")
        except Exception as e:
            apply_view = mo.md(f"❌ Apply failed: `{e}`").callout(kind="danger")
    elif apply_dryrun_btn.value:
        try:
            _msg = apply_repair(sqlite_path, suggestion, dry_run=True)
            apply_view = mo.md(f"```sql\n{_msg}\n```").callout(kind="info")
        except Exception as e:
            apply_view = mo.md(f"❌ Render failed: `{e}`").callout(kind="danger")
    apply_view
    return


if __name__ == "__main__":
    app.run()
