import marimo

app = marimo.App(width="medium")


@app.cell
def imports():
    import os
    import marimo as mo
    from pathlib import Path
    from src.detection.error_detection import detect_all

    # Resolve the project root once so the dashboard works from any cwd.
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
    return mo, os, detect_all, DATA_DIR


@app.cell
def header(mo):
    mo.md(
        """
        # OCEL Error Detection Dashboard
        Inspect rule-based data-quality violations in an object-centric event log.
        Pick a SQLite log and browse the violations per detector.
        """
    )
    return


@app.cell
def file_picker(mo, os, DATA_DIR):
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".sqlite"))
    default = "new.sqlite" if "new.sqlite" in files else (files[0] if files else None)
    file_picker = mo.ui.dropdown(options=files, value=default, label="OCEL file")
    file_picker
    return (file_picker,)


@app.cell
def load_results(detect_all, file_picker, DATA_DIR):
    results = detect_all(str(DATA_DIR / file_picker.value))
    return (results,)


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
        # mirrors _bad_o2o: highlight the id whose referenced entity is absent
        side = row["missing_side"]
        return (
            (c == "ocel_event_id"  and side in ("event",  "both"))
            or (c == "ocel_object_id" and side in ("object", "both"))
        )

    def _bad_dup_id(_row, c):
        # the ocel_ids column lists every id in the duplicate group
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


if __name__ == "__main__":
    app.run()
