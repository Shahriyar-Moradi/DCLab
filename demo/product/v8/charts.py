"""Inline SVG charts for the v8 "Real product" demo. Each function returns an SVG string.

Fragments reference them as {{chart:name}}; build.py substitutes the output.
All numbers are invented and match the story in BRIEF.md. v8 draws what the real product draws:
target rate of TRAINING months only (final-test months sealed until the model in use had its one
final test), the test design to scale by ROWS (no dates), per-try differences against the noise margin.
"""
from __future__ import annotations

import math

MONTHS = ["Jan 25", "Feb 25", "Mar 25", "Apr 25", "May 25", "Jun 25", "Jul 25", "Aug 25", "Sep 25", "Oct 25", "Nov 25",
          "Dec 25", "Jan 26", "Feb 26", "Mar 26", "Apr 26", "May 26", "Jun 26", "Jul 26", "Aug 26", "Sep 26"]


def _pts(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _target_chart(n_months: int, n_train: int, disclosed, train_rate: float, label: str) -> str:
    """Monthly target rate. Training months: a line. Final-test months: a sealed band with nothing inside,
    or (after the model in use had its one final test) points drawn apart and labelled."""
    vals = [17.8 + i * (24.5 - 17.8) / 17 for i in range(n_train)]
    x0, x1, y_lo, y_hi, v_lo, v_hi = 50, 446, 150, 24, 14.0, 28.0
    last = n_months - 1

    def X(i):
        return x0 + i * (x1 - x0) / last

    def Y(v):
        return y_lo - (v - v_lo) / (v_hi - v_lo) * (y_lo - y_hi)

    pts = [(X(i), Y(v)) for i, v in enumerate(vals)]
    grid = "".join(
        f'<line class="grid-l" x1="{x0}" y1="{Y(v):.1f}" x2="{x1 + 6}" y2="{Y(v):.1f}"/>'
        f'<text class="axis" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v}%</text>'
        for v in (15, 20, 25)
    )
    tick_idx = sorted({0, n_train // 2, n_train - 1, last})
    ticks = "".join(f'<text class="axis" x="{X(i):.1f}" y="172" text-anchor="middle">{MONTHS[i]}</text>' for i in tick_idx)
    area = f"M{pts[0][0]:.1f},{y_lo} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f},{y_lo} Z"
    bx = X(n_train) - 9
    bw = X(last) - X(n_train) + 18
    if disclosed is None:
        test = (f'<rect class="sealed" x="{bx:.1f}" y="18" width="{bw:.1f}" height="{y_lo - 18}" rx="4"/>'
                f'<text class="axis" x="{bx + bw / 2:.1f}" y="34" text-anchor="middle">final test</text>'
                f'<text class="axis" x="{bx + bw / 2:.1f}" y="48" text-anchor="middle">sealed</text>')
        aria = "The final-test months are a shaded band with no line, point or rate: sealed until the final test runs."
    else:
        dp = [(X(n_train + i), Y(v)) for i, v in enumerate(disclosed)]
        test = (f'<rect class="tst" x="{bx:.1f}" y="18" width="{bw:.1f}" height="{y_lo - 18}" rx="4"/>'
                f'<polyline class="ln-disc" points="{_pts(dp)}"/>'
                + "".join(f'<circle class="pt-w" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>' for x, y in dp)
                + f'<text class="axis" x="{bx + bw / 2:.1f}" y="34" text-anchor="middle">disclosed after</text>'
                f'<text class="axis" x="{bx + bw / 2:.1f}" y="48" text-anchor="middle">the final test</text>')
        aria = "The final-test months are drawn apart and labelled disclosed after the final test."
    return (
        f'<svg class="chart" viewBox="0 0 470 184" role="img" aria-label="{label} Share of training rows whose answer is yes, by month, '
        f'rising from about 18 percent. Training rate {train_rate}%. {aria}">'
        f"{grid}"
        f'<line class="ln-c" x1="{x0}" y1="{Y(train_rate):.1f}" x2="{X(n_train - 1):.1f}" y2="{Y(train_rate):.1f}"/>'
        f'<text class="axis" x="{x0 + 4}" y="{Y(train_rate) - 5:.1f}">training rows {train_rate}%</text>'
        f'<path class="area" d="{area}"/><polyline class="ln-a" points="{_pts(pts)}"/>'
        f'<circle class="pt-a" cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4.5"/>'
        f"{test}{ticks}</svg>"
    )


def target_sealed() -> str:
    # Customer data · Sep 2026: 21 months; training Jan 2025 - Jun 2026; final test Jul - Sep 2026 sealed.
    return _target_chart(21, 18, None, 21.3, "Customer data, September 2026 file.")


def target_disclosed() -> str:
    # Customer data · Jun 2026 (in use): 18 months; training Jan 2025 - Mar 2026; final test Apr - Jun 2026 disclosed.
    return _target_chart(18, 15, [23.4, 24.0, 24.6], 20.7, "Customer data, June 2026 file, in use.")


def cv_timeline() -> str:
    # Drawn to scale by ROWS, no dates (the real Goal page): 39,306 training rows in 5 time-ordered folds
    # (scikit-learn TimeSeriesSplit: each check block 6,551 rows), then the latest 8,904 rows as the final test.
    x0, width, total = 96, 640, 48210
    s = width / total
    block = 6551
    rows = []
    for k in range(5):
        y = 34 + k * 28
        learn = block * (k + 1)
        rows.append(f'<text class="lbl" x="{x0 - 10}" y="{y + 14}" text-anchor="end">Fold {k + 1}</text>')
        rows.append(f'<rect class="tl-train" x="{x0}" y="{y}" width="{learn * s:.1f}" height="20" rx="4"/>')
        rows.append(f'<rect class="tl-val" x="{x0 + learn * s:.1f}" y="{y}" width="{block * s:.1f}" height="20" rx="4"/>')
        rows.append(f'<text class="tl-t" x="{x0 + 8}" y="{y + 14}">learn {learn:,}</text>')
        rows.append(f'<text class="tl-t tl-tv" x="{x0 + (learn + block / 2) * s:.1f}" y="{y + 14}" text-anchor="middle">check</text>')
    y = 34 + 5 * 28 + 14
    rows.append(f'<text class="lbl" x="{x0 - 10}" y="{y + 14}" text-anchor="end">Final test</text>')
    rows.append(f'<rect class="tl-test" x="{x0 + 39306 * s:.1f}" y="{y}" width="{8904 * s:.1f}" height="20" rx="4"/>')
    rows.append(f'<text class="tl-t tl-tt" x="{x0 + (39306 + 4452) * s:.1f}" y="{y + 14}" text-anchor="middle">used once</text>')
    return (
        '<svg class="chart" viewBox="0 0 760 236" role="img" aria-label="Test design drawn to scale by rows: 5 time-ordered cross-validation folds on 39,306 '
        'training rows. Fold 1 learns from 6,551 rows and checks on the next 6,551; fold 5 learns from 32,755 rows. Then a final test set of the latest 8,904 rows, used once per run at the end.">'
        f'<line class="lim-w" x1="{x0 + 39306 * s:.1f}" y1="22" x2="{x0 + 39306 * s:.1f}" y2="214"/>'
        f'<text class="axis" x="{x0 + 19653 * s:.1f}" y="16" text-anchor="middle">training rows · 39,306 · in time order</text>'
        f'<text class="axis" x="{x0 + (39306 + 4452) * s:.1f}" y="16" text-anchor="middle">latest 8,904 rows (18%)</text>'
        + "".join(rows)
        + f'<text class="axis" x="{x0}" y="228">drawn to scale by rows · earlier rows on the left, later rows on the right</text>'
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
        'Recall stays above 0.80 up to threshold 0.31, the threshold locked when the model was trained.">'
        f"{grid}"
        f'<line class="lim" x1="{x0}" y1="{Y(0.8):.1f}" x2="{x1}" y2="{Y(0.8):.1f}"/>'
        f'<text class="axis" x="{x1}" y="{Y(0.8) - 5:.1f}" text-anchor="end">recall 0.80 (our rule)</text>'
        f'<line class="ln-c" x1="{cx:.1f}" y1="{y1}" x2="{cx:.1f}" y2="{y0}"/>'
        f'<polyline class="ln-a" points="{_pts(rec)}"/><polyline class="ln-d" points="{_pts(pre)}"/>'
        f'<circle class="pt-a" cx="{cx:.1f}" cy="{Y(sig(-(0.31 - 0.484) / 0.12)):.1f}" r="5"/>'
        f'<circle class="pt-d" cx="{cx:.1f}" cy="{Y(0.21 + 0.79 * sig((0.31 - 0.29) / 0.17)):.1f}" r="5"/>'
        f'<text class="axis" x="{cx + 8:.1f}" y="{y0 - 6}">locked: 0.31</text>'
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


def _week_axis(X, weeks, y):
    return "".join(
        f'<text class="axis" x="{X(i):.1f}" y="{y}" text-anchor="middle">{w}</text>' for i, w in enumerate(weeks) if i in (0, 4, 8)
    )


DRIFT_WEEKS = ["3 Aug", "10 Aug", "17 Aug", "24 Aug", "31 Aug", "7 Sep", "14 Sep", "21 Sep", "28 Sep"]
OUTCOME_WEEKS = ["6 Jul", "13 Jul", "20 Jul", "27 Jul", "3 Aug", "10 Aug", "17 Aug", "24 Aug", "31 Aug"]


def psi_weekly() -> str:
    vals = [0.04, 0.05, 0.05, 0.07, 0.09, 0.12, 0.15, 0.19, 0.22]
    chance = {5}  # at or above the watch line, but the statistical test says it could be chance
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
    marks = "".join(
        (f'<circle class="pt-o" cx="{x:.1f}" cy="{y:.1f}" r="4"/><text class="axis" x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle">within chance</text>'
         if i in chance else f'<circle class="pt-a" cx="{x:.1f}" cy="{y:.1f}" r="3"/>')
        for i, (x, y) in enumerate(pts)
    )
    return (
        '<svg class="chart" viewBox="0 0 460 170" role="img" aria-label="Weekly drift (PSI) of plan_type, week of 3 August to week of 28 September. '
        'It rises from 0.04 to 0.22 and crosses the alert line of 0.20 in the week of 28 September. The week of 7 September is at the watch line but within chance.">'
        f"{grid}"
        f'<line class="lim" x1="{x0}" y1="{Y(0.2):.1f}" x2="{x1}" y2="{Y(0.2):.1f}"/><text class="axis" x="{x0 + 4}" y="{Y(0.2) - 5:.1f}">alert line 0.20</text>'
        f'<line class="lim-w" x1="{x0}" y1="{Y(0.1):.1f}" x2="{x1}" y2="{Y(0.1):.1f}"/><text class="axis" x="{x0 + 4}" y="{Y(0.1) - 5:.1f}">watch line 0.10</text>'
        f'<path class="area" d="{area}"/><polyline class="ln-a" points="{_pts(pts)}"/>{marks}'
        + _week_axis(X, DRIFT_WEEKS, 160)
        + "</svg>"
    )


def precision_weekly() -> str:
    prec = [0.62, 0.62, 0.61, 0.59, 0.57, 0.55, 0.52, 0.50, 0.48]
    rec = [0.81, 0.81, 0.80, 0.81, 0.80, 0.80, 0.81, 0.80, 0.80]
    not_judged = {1}  # too few rows with a real outcome to judge against a floor
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
    marks = "".join(
        f'<circle class="{"pt-o" if i in not_judged else "pt-d"}" cx="{x:.1f}" cy="{y:.1f}" r="{4 if i in not_judged else 3}"/>' for i, (x, y) in enumerate(p)
    )
    return (
        '<svg class="chart" viewBox="0 0 460 170" role="img" aria-label="Real precision and recall by week, from linked outcomes, week of 6 July to week of 31 August. '
        'Recall stays near 0.80 while precision falls from 0.62 to 0.48, below the precision floor of 0.55. The week of 13 July is not judged: too few rows with a real outcome.">'
        f"{grid}"
        f'<line class="lim" x1="{x0}" y1="{Y(0.55):.1f}" x2="{x1}" y2="{Y(0.55):.1f}"/><text class="axis" x="{x1}" y="{Y(0.55) + 13:.1f}" text-anchor="end">precision floor 0.55</text>'
        f'<polyline class="ln-a" points="{_pts(r)}"/><polyline class="ln-d" points="{_pts(p)}"/>{marks}'
        f'<text class="axis" x="{x0 + 6}" y="12"><tspan class="k-a">■</tspan> recall   <tspan class="k-d">■</tspan> precision   ○ not judged</text>'
        + _week_axis(X, OUTCOME_WEEKS, 160)
        + "</svg>"
    )


def run_steps() -> str:
    # The engine's real stage order: the final test set is set aside right after reading the file.
    steps = [
        ("Read the file", 8),
        ("Set the final test set aside", 3),
        ("Profile the training rows", 21),
        ("Build features", 46),
        ("Train: 5 folds × 9 models", 852),
        ("Choose the best on cross-validation", 1),
        ("Final test, once", 9),
        ("Save the model, run trust checks", 34),
    ]

    def fmt(sec):
        return f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"0:{sec:02d}"

    rows = []
    for i, (name, sec) in enumerate(steps):
        y = 8 + i * 27
        w = max(5.0, sec / 852 * 190)
        cls = "bar" if sec < 800 else "bar-hi"
        rows.append(f'<text class="lbl" x="218" y="{y + 14}" text-anchor="end">{name}</text>')
        rows.append(f'<rect class="{cls}" x="226" y="{y}" width="{w:.0f}" height="18" rx="4"/>')
        rows.append(f'<text class="axis" x="{232 + w:.0f}" y="{y + 14}">{fmt(sec)}</text>')
    return (
        '<svg class="chart" viewBox="0 0 470 232" role="img" aria-label="Time spent in each step of Run 1, in the order the engine runs them. '
        'Training takes 14 minutes 12 seconds of the 16 minutes 14 seconds in total.">' + "".join(rows) + "</svg>"
    )


def improve_tries() -> str:
    # Late payment risk loop: each try's mean fold-by-fold difference from the current model on the goal
    # (recall while precision >= 0.60), with the noise margin. Beats the noise only when the whole bar clears zero.
    tries = [("Try 1 · extra trees", 0.004, 0.018, "within noise"), ("Try 2 · gradient boosting", 0.031, 0.018, "beats noise")]
    x0, x1, y0, y1 = 60, 450, 150, 20

    def Y(v):
        return y0 - (v + 0.03) / 0.09 * (y0 - y1)

    grid = "".join(
        f'<line class="grid-l" x1="{x0}" y1="{Y(v):.1f}" x2="{x1}" y2="{Y(v):.1f}"/>'
        f'<text class="axis" x="{x0 - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:+.2f}</text>'
        for v in (-0.02, 0.0, 0.02, 0.04)
    )
    marks = []
    for i, (name, d, m, verdict) in enumerate(tries):
        x = x0 + 120 + i * 180
        cls = "pt-a" if verdict == "beats noise" else "pt-o"
        marks.append(f'<line class="ln-d" x1="{x}" y1="{Y(d + m):.1f}" x2="{x}" y2="{Y(d - m):.1f}"/>')
        marks.append(f'<line class="ln-d" x1="{x - 7}" y1="{Y(d + m):.1f}" x2="{x + 7}" y2="{Y(d + m):.1f}"/><line class="ln-d" x1="{x - 7}" y1="{Y(d - m):.1f}" x2="{x + 7}" y2="{Y(d - m):.1f}"/>')
        marks.append(f'<circle class="{cls}" cx="{x}" cy="{Y(d):.1f}" r="5"/>')
        marks.append(f'<text class="axis" x="{x}" y="{Y(d + m) - 8:.1f}" text-anchor="middle">{d:+.3f} ± {m:.3f} · {verdict}</text>')
        marks.append(f'<text class="axis" x="{x}" y="168" text-anchor="middle">{name}</text>')
    return (
        '<svg class="chart" viewBox="0 0 470 180" role="img" aria-label="Each try compared with the current model on the same folds: the mean difference in recall '
        'while precision is at least 0.60, with the noise margin. Try 1 adds 0.004, inside the margin: within noise. Try 2 adds 0.031, beyond the margin of 0.018: beats the noise.">'
        f"{grid}"
        f'<line class="ln-c" x1="{x0}" y1="{Y(0):.1f}" x2="{x1}" y2="{Y(0):.1f}"/><text class="axis" x="{x0 + 4}" y="{Y(0) + 14:.1f}">0 = same as the current model</text>'
        + "".join(marks) + "</svg>"
    )


def lineage() -> str:
    def box(x, y, w, t1, t2, cls="node"):
        return (
            f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="46" rx="10"/>'
            f'<text class="lbl" x="{x + w / 2}" y="{y + 20}" text-anchor="middle" font-weight="600">{t1}</text>'
            f'<text class="axis" x="{x + w / 2}" y="{y + 36}" text-anchor="middle">{t2}</text>'
        )

    def arrow(x1, y1, x2, y2, cls="arrow"):
        return f'<line class="{cls}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>'

    parts = [
        box(8, 28, 92, "Customer file", "Jun 2026"),
        box(112, 28, 88, "Test design", "time-ordered"),
        box(212, 28, 70, "Runs 1–4", "same folds"),
        box(294, 28, 92, "Churn model", "v1 · in use"),
        box(398, 28, 92, "Predictions", "every Monday"),
        box(8, 128, 92, "Customer file", "Sep 2026", "node node-new"),
        box(112, 128, 88, "Test design", "its own", "node node-new"),
        box(212, 128, 70, "Run 5", "retrain", "node node-new"),
        box(294, 128, 92, "Churn model", "v5 · waiting", "node node-new"),
        arrow(100, 51, 112, 51), arrow(200, 51, 212, 51), arrow(282, 51, 294, 51), arrow(386, 51, 398, 51),
        arrow(100, 151, 112, 151, "arrow arrow-dash"), arrow(200, 151, 212, 151, "arrow arrow-dash"), arrow(282, 151, 294, 151, "arrow arrow-dash"),
        '<text class="axis warn-t" x="54" y="100" text-anchor="middle">newer file</text>',
        '<text class="axis" x="8" y="14">Now in use</text><text class="axis" x="8" y="118">Newer file and the retrained model, waiting for your decision</text>',
    ]
    return (
        '<svg class="chart" viewBox="0 0 498 190" role="img" aria-label="Diagram: the June 2026 customer file leads to the test design, runs 1 to 4, the churn model version 1 in use and the weekly predictions. '
        'A newer September 2026 file has its own test design and Run 5, the retrain, which made model version 5; it waits for your decision.">'
        + "".join(parts) + "</svg>"
    )


CHARTS = {
    "target_sealed": target_sealed,
    "target_disclosed": target_disclosed,
    "cv_timeline": cv_timeline,
    "pr_by_threshold": pr_by_threshold,
    "importance_bars": importance_bars,
    "psi_weekly": psi_weekly,
    "precision_weekly": precision_weekly,
    "run_steps": run_steps,
    "improve_tries": improve_tries,
    "lineage": lineage,
}
