"""Altair charts for the Data page. Single-series charts: one hue, no legend,
hairline grid, thin bars with 4px rounded data-ends, tooltips on every mark.
Colours switch with the Streamlit light/dark theme."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

LIGHT = {"series": "#2a78d6", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7"}
DARK = {"series": "#3987e5", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835"}
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


def _tokens() -> dict:
    try:
        return DARK if st.context.theme.type == "dark" else LIGHT
    except Exception:
        return LIGHT


def _finish(chart: alt.Chart, t: dict) -> alt.Chart:
    return (chart.configure(background="transparent", font=FONT)
            .configure_view(stroke=None)
            .configure_axis(gridColor=t["grid"], gridWidth=1, domainColor=t["axis"], tickColor=t["axis"],
                            labelColor=t["muted"], titleColor=t["ink2"], labelFontSize=11,
                            titleFontSize=12, titleFontWeight="normal"))


def show(chart: alt.Chart):
    st.altair_chart(chart, width="stretch", theme=None)


def hbar(df: pd.DataFrame, cat: str, val: str, val_title: str, fmt: str = ",d", sort="-x") -> alt.Chart:
    """Horizontal bars for long category names (labels, fields)."""
    t = _tokens()
    chart = alt.Chart(df).mark_bar(color=t["series"], size=16, cornerRadiusTopRight=4,
                                   cornerRadiusBottomRight=4).encode(
        y=alt.Y(f"{cat}:N", sort=sort, title=None, axis=alt.Axis(labelLimit=340, ticks=False, domain=False)),
        x=alt.X(f"{val}:Q", title=val_title,  # whole-number axes for counts: no "0 0 1 1" ticks
                axis=alt.Axis(format=fmt, tickCount=5, grid=True, **({"tickMinStep": 1} if fmt == ",d" else {}))),
        tooltip=[alt.Tooltip(f"{cat}:N", title=cat.replace("_", " ").capitalize()),
                 alt.Tooltip(f"{val}:Q", title=val_title, format=fmt)],
    ).properties(height=max(110, 26 * len(df)))
    return _finish(chart, t)


def histogram(values: pd.Series, title: str, step: float | None = None, extent=None, fmt: str = ",.2f") -> alt.Chart:
    t = _tokens()
    df = pd.DataFrame({"v": pd.to_numeric(values, errors="coerce").dropna()})
    b = alt.Bin(step=step, extent=extent) if step else alt.Bin(maxbins=24)
    chart = alt.Chart(df).mark_bar(color=t["series"], binSpacing=2, cornerRadiusTopLeft=4,
                                   cornerRadiusTopRight=4).encode(
        x=alt.X("v:Q", bin=b, title=title, axis=alt.Axis(format=fmt, grid=False)),
        y=alt.Y("count():Q", title="Products", axis=alt.Axis(tickMinStep=1, format=",d")),
        tooltip=[alt.Tooltip("v:Q", bin=b, title=title, format=fmt),  # shown as a "from – to" range
                 alt.Tooltip("count():Q", title="Products", format=",d")],
    ).properties(height=240)
    return _finish(chart, t)
