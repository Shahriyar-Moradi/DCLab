"""Inline SVG charts for the v6 Plain demo. Each function returns an SVG string.

Fragments reference them as {{chart:name}}; build.py substitutes the output.
All numbers are invented and match the story in BRIEF.md.
"""
from __future__ import annotations

import math


def _pts(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def churn_by_month() -> str:
    # 18 months Jan 2025 - Jun 2026; churn rate rises 17.8% -> 24.5%; last 3 months = final test set.
    vals = [17.8 + i * (24.5 - 17.8) / 17 for i in range(18)]
    x0, x1, y_lo, y_hi, v_lo, v_hi = 50, 446, 150, 20, 14.0, 28.0

    def X(i):
        return x0 + i * (x1 - x0) / 17

    def Y(v):
        return y_lo - (v - v_lo) / (v_hi - v_lo) * (y_lo - y_hi)

    pts = [(X(i), Y(v)) for i, v in enumerate(vals)]
    grid = "".join(
        f'<line class="grid-l" x1="{x0}" y1="{Y(v):.1f}" x2="{x1 + 6}" y2="{Y(v):.1f}"/>'
        f'<text class="axis" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v}%</text>'
        for v in (15, 20, 25)
    )
    ticks = "".join(
        f'<text class="axis" x="{X(i):.1f}" y="172" text-anchor="middle">{t}</text>'
        for i, t in ((0, "Jan 25"), (6, "Jul 25"), (12, "Jan 26"), (17, "Jun 26"))
    )
    area = f"M{pts[0][0]:.1f},{y_lo} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f},{y_lo} Z"
    return (
        '<svg class="chart" viewBox="0 0 470 184" role="img" aria-label="Share of customers who cancelled, by month. '
        'It rises from about 18 percent in January 2025 to about 24 percent in June 2026. The last three months are the final test set.">'
        f'<rect class="tst" x="{X(15) - 8:.1f}" y="14" width="{X(17) - X(15) + 16:.1f}" height="{y_lo - 14}"/>'
        f"{grid}"
        f'<line class="ln-c" x1="{x0}" y1="{Y(21.3):.1f}" x2="{x1 + 6}" y2="{Y(21.3):.1f}"/>'
        f'<text class="axis" x="{x0 + 4}" y="{Y(21.3) - 5:.1f}">overall 21.3%</text>'
        f'<path class="area" d="{area}"/><polyline class="ln-a" points="{_pts(pts)}"/>'
        f'<circle class="pt-a" cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4.5"/>'
        f'<text class="axis" x="{X(16):.1f}" y="30" text-anchor="middle">final test</text>'
        f"{ticks}</svg>"
    )


def cv_timeline() -> str:
    # 18 months; first 15 are training time (5 time-ordered folds, expanding window), last 3 are the final test set.
    x0, m = 96, 36.0  # px per month
    rows = []
    for k in range(5):
        y = 34 + k * 28
        train_m = 5 + 2 * k
        rows.append(f'<text class="lbl" x="{x0 - 10}" y="{y + 14}" text-anchor="end">Fold {k + 1}</text>')
        rows.append(f'<rect class="tl-train" x="{x0}" y="{y}" width="{train_m * m}" height="20" rx="4"/>')
        rows.append(f'<rect class="tl-val" x="{x0 + train_m * m}" y="{y}" width="{2 * m}" height="20" rx="4"/>')
        rows.append(f'<text class="tl-t" x="{x0 + 8}" y="{y + 14}">learn</text>')
        rows.append(f'<text class="tl-t tl-tv" x="{x0 + train_m * m + m}" y="{y + 14}" text-anchor="middle">check</text>')
    y = 34 + 5 * 28 + 14
    rows.append(f'<text class="lbl" x="{x0 - 10}" y="{y + 14}" text-anchor="end">Final test</text>')
    rows.append(f'<rect class="tl-test" x="{x0 + 15 * m}" y="{y}" width="{3 * m}" height="20" rx="4"/>')
    rows.append(f'<text class="tl-t tl-tt" x="{x0 + 16.5 * m}" y="{y + 14}" text-anchor="middle">used once</text>')
    ticks = "".join(
        f'<text class="axis" x="{x0 + i * m:.1f}" y="226" text-anchor="middle">{t}</text>'
        for i, t in ((0, "Jan 25"), (6, "Jul 25"), (12, "Jan 26"), (15, "Apr 26"), (18, "Jun 26"))
    )
    return (
        '<svg class="chart" viewBox="0 0 760 236" role="img" aria-label="Test design on a time line. Five cross-validation folds each learn on earlier months '
        'and check on the next two months, between January 2025 and March 2026. The last three months, April to June 2026, are the final test set, used once.">'
        f'<line class="lim-w" x1="{x0 + 15 * m}" y1="22" x2="{x0 + 15 * m}" y2="214"/>'
        f'<text class="axis" x="{x0 + 7.5 * m}" y="16" text-anchor="middle">training period · Jan 2025 – Mar 2026 · 39,306 rows</text>'
        f'<text class="axis" x="{x0 + 16.5 * m}" y="16" text-anchor="middle">last 3 months · 8,904 rows</text>'
        + "".join(rows)
        + ticks
        + "</svg>"
    )


def pr_by_threshold() -> str:
    x0, x1, y0, y1 = 54, 504, 190, 20  # threshold 0.10..0.70, value 0..1

    def X(t):
        return x0 + (t - 0.10) / 0.60 * (x1 - x0)

    def Y(v):
        return y0 - v * (y0 - y1)

    def sig(z):
        return 1 / (1 + math.exp(-z))

    ts = [0.10 + i * 0.02 for i in range(31)]
    rec = [(X(t), Y(sig(-(t - 0.484) / 0.12))) for t in ts]
    pre = [(X(t), Y(0.21 + 0.79 * sig((t - 0.29) / 0.17))) for t in ts]
    grid = "".join(
        f'<line class="grid-l" x1="{x0}" y1="{Y(v):.1f}" x2="{x1}" y2="{Y(v):.1f}"/>'
        f'<text class="axis" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:g}</text>'
        for v in (0, 0.25, 0.5, 0.75, 1)
    )
    xt = "".join(
        f'<text class="axis" x="{X(t):.1f}" y="208" text-anchor="middle">{t:.1f}</text>' for t in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)
    )
    cx = X(0.31)
    return (
        '<svg class="chart" viewBox="0 0 520 226" role="img" aria-label="Precision and recall for each threshold, from cross-validation. '
        'Recall stays above 0.80 up to threshold 0.31, the chosen point.">'
        f"{grid}"
        f'<line class="lim" x1="{x0}" y1="{Y(0.8):.1f}" x2="{x1}" y2="{Y(0.8):.1f}"/>'
        f'<text class="axis" x="{x1}" y="{Y(0.8) - 5:.1f}" text-anchor="end">recall 0.80 (our rule)</text>'
        f'<line class="ln-c" x1="{cx:.1f}" y1="{y1}" x2="{cx:.1f}" y2="{y0}"/>'
        f'<polyline class="ln-a" points="{_pts(rec)}"/><polyline class="ln-d" points="{_pts(pre)}"/>'
        f'<circle class="pt-a" cx="{cx:.1f}" cy="{Y(sig(-(0.31 - 0.484) / 0.12)):.1f}" r="5"/>'
        f'<circle class="pt-d" cx="{cx:.1f}" cy="{Y(0.21 + 0.79 * sig((0.31 - 0.29) / 0.17)):.1f}" r="5"/>'
        f'<text class="axis" x="{cx + 8:.1f}" y="{y0 - 6}">chosen: 0.31</text>'
        f'<text class="axis" x="{x0 + 6}" y="14"><tspan class="k-a">■</tspan> recall   <tspan class="k-d">■</tspan> precision</text>'
        f'<text class="axis" x="{(x0 + x1) / 2:.0f}" y="222" text-anchor="middle">threshold</text>'
        f"{xt}</svg>"
    )


def importance_bars() -> str:
    items = [
        ("last_login_days", 0.19),
        ("monthly_fee_change_3m", 0.15),
        ("contract_end (days left)", 0.13),
        ("support_tickets_90d", 0.10),
        ("tenure_months", 0.09),
        ("plan_type", 0.08),
        ("late_payments_6m", 0.06),
        ("data_usage_change", 0.05),
    ]
    rows = []
    for i, (name, v) in enumerate(items):
        y = 12 + i * 28
        w = v / 0.20 * 190
        rows.append(f'<text class="lbl" x="170" y="{y + 14}" text-anchor="end">{name}</text>')
        rows.append(f'<rect class="bar" x="180" y="{y}" width="{w:.0f}" height="18" rx="4"/>')
        rows.append(f'<text class="axis" x="{186 + w:.0f}" y="{y + 14}">{int(v * 100)}%</text>')
    return (
        '<svg class="chart" viewBox="0 0 460 244" role="img" aria-label="The eight columns that matter most to the model, with their share of importance. '
        'Days since last login is first at 19 percent.">' + "".join(rows) + "</svg>"
    )


WEEKS = ["7 Jul", "14 Jul", "21 Jul", "28 Jul", "4 Aug", "11 Aug", "18 Aug", "25 Aug", "1 Sep"]


def _week_axis(X):
    return "".join(
        f'<text class="axis" x="{X(i):.1f}" y="{{y}}" text-anchor="middle">{w}</text>' for i, w in enumerate(WEEKS) if i in (0, 4, 8)
    )


def psi_weekly() -> str:
    vals = [0.04, 0.05, 0.05, 0.07, 0.09, 0.12, 0.15, 0.19, 0.22]
    x0, x1, y0, y1 = 44, 440, 140, 20

    def X(i):
        return x0 + 16 + i * (x1 - x0 - 32) / 8

    def Y(v):
        return y0 - v / 0.30 * (y0 - y1)

    pts = [(X(i), Y(v)) for i, v in enumerate(vals)]
    grid = "".join(
        f'<line class="grid-l" x1="{x0}" y1="{Y(v):.1f}" x2="{x1}" y2="{Y(v):.1f}"/>'
        f'<text class="axis" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:g}</text>'
        for v in (0, 0.1, 0.2, 0.3)
    )
    area = f"M{pts[0][0]:.1f},{y0} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f},{y0} Z"
    return (
        '<svg class="chart" viewBox="0 0 460 170" role="img" aria-label="Weekly drift (PSI) of plan_type. It rises from 0.04 to 0.22, crossing the alert line of 0.20 in the week of 1 September.">'
        f"{grid}"
        f'<line class="lim" x1="{x0}" y1="{Y(0.2):.1f}" x2="{x1}" y2="{Y(0.2):.1f}"/><text class="axis" x="{x0 + 4}" y="{Y(0.2) - 5:.1f}">alert 0.20</text>'
        f'<line class="lim-w" x1="{x0}" y1="{Y(0.1):.1f}" x2="{x1}" y2="{Y(0.1):.1f}"/><text class="axis" x="{x0 + 4}" y="{Y(0.1) - 5:.1f}">watch 0.10</text>'
        f'<path class="area" d="{area}"/><polyline class="ln-a" points="{_pts(pts)}"/>'
        f'<circle class="pt-a" cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4.5"/>'
        + _week_axis(X).replace("{y}", "160")
        + "</svg>"
    )


def precision_weekly() -> str:
    prec = [0.62, 0.62, 0.61, 0.59, 0.57, 0.55, 0.52, 0.50, 0.48]
    rec = [0.81, 0.81, 0.80, 0.81, 0.80, 0.80, 0.81, 0.80, 0.80]
    x0, x1, y0, y1 = 44, 440, 140, 20

    def X(i):
        return x0 + 16 + i * (x1 - x0 - 32) / 8

    def Y(v):
        return y0 - (v - 0.40) / 0.50 * (y0 - y1)

    p = [(X(i), Y(v)) for i, v in enumerate(prec)]
    r = [(X(i), Y(v)) for i, v in enumerate(rec)]
    grid = "".join(
        f'<line class="grid-l" x1="{x0}" y1="{Y(v):.1f}" x2="{x1}" y2="{Y(v):.1f}"/>'
        f'<text class="axis" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:g}</text>'
        for v in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    )
    return (
        '<svg class="chart" viewBox="0 0 460 170" role="img" aria-label="Real precision and recall by week once outcomes arrived. Recall stays near 0.80 while precision falls from 0.62 to 0.48.">'
        f"{grid}"
        f'<line class="ln-c" x1="{x0}" y1="{Y(0.63):.1f}" x2="{x1}" y2="{Y(0.63):.1f}"/><text class="axis" x="{x1}" y="{Y(0.63) - 5:.1f}" text-anchor="end">0.63 in the final test</text>'
        f'<polyline class="ln-a" points="{_pts(r)}"/><polyline class="ln-d" points="{_pts(p)}"/>'
        f'<circle class="pt-d" cx="{p[-1][0]:.1f}" cy="{p[-1][1]:.1f}" r="4.5"/>'
        f'<text class="axis" x="{x0 + 6}" y="12"><tspan class="k-a">■</tspan> recall   <tspan class="k-d">■</tspan> precision</text>'
        + _week_axis(X).replace("{y}", "160")
        + "</svg>"
    )


CHARTS = {
    "churn_by_month": churn_by_month,
    "cv_timeline": cv_timeline,
    "pr_by_threshold": pr_by_threshold,
    "importance_bars": importance_bars,
    "psi_weekly": psi_weekly,
    "precision_weekly": precision_weekly,
}
