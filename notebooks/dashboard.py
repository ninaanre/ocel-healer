import marimo

app = marimo.App(width="medium")


@app.cell
def imports():
    import os
    import sys

    import marimo as mo

    sys.path.insert(0, "..")
    from src.detection.error_detection import detect_all

    return mo, os, detect_all


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
def file_picker(mo, os):
    files = sorted(f for f in os.listdir("../data") if f.endswith(".sqlite"))
    default = "new.sqlite" if "new.sqlite" in files else (files[0] if files else None)
    file_picker = mo.ui.dropdown(options=files, value=default, label="OCEL file")
    file_picker
    return (file_picker,)


@app.cell
def load_results(detect_all, file_picker):
    results = detect_all(f"../data/{file_picker.value}")
    return (results,)


@app.cell
def summary_stats(mo, results):
    def _card(label, value):
        stat = getattr(mo, "stat", None)
        if stat is not None:
            return stat(label=label, value=value, bordered=True)
        return mo.md(f"**{label}**\n\n# {value}")

    summary = mo.hstack(
        [
            _card("Missing attribute values", results["missing_attributes"].height),
            _card("Wrong attribute datatypes", results["incorrect_datatypes"].height),
            _card("Dangling object-to-object relationships", results["dangling_o2o_relations"].height),
        ],
        justify="start",
        gap=2,
    )
    summary
    return


@app.cell
def tabs(mo, results):
    bad_style = "background-color:#fde2e2; color:#b00020; font-weight:600;"
    table_style = (
        "border-collapse:collapse; font-family:system-ui,-apple-system,sans-serif; "
        "font-size:13px; border:1px solid #d0d7de; "
        "width:100%; table-layout:fixed;"
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
            return mo.md("_no violations_")
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

    def _bad_value_col(col_name):
        return lambda _row, c: c == col_name

    def _bad_o2o(row, c):
        side = row["missing_side"]
        return (
            (c == "ocel_source_id" and side in ("source", "both"))
            or (c == "ocel_target_id" and side in ("target", "both"))
        )

    main_tabs = mo.ui.tabs(
        {
            "Missing attribute values": _render(
                results["missing_attributes"], _bad_value_col("actual_value")
            ),
            "Wrong attribute datatypes": _render(
                results["incorrect_datatypes"], _bad_value_col("actual_value")
            ),
            "Dangling object-to-object relationships": _render(results["dangling_o2o_relations"], _bad_o2o),
        }
    )
    main_tabs
    return


if __name__ == "__main__":
    app.run()
