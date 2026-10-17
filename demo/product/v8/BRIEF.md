# Brief — DCLab demo v8 "Real product"

**Goal:** a truth pass on v7 (`../v7_final/`, frozen). Same look (Manrope / Inter / JetBrains Mono, teal +
violet, guided detail, flow bar, guide strip, glossary, Next step), same Northwind churn story, but every
screen now shows what the real product does on `dev` at 819d629 (STATUS rows V7-W1-* to V7-D5C). What is
not built yet is shown the way its brief says it will ship, tagged **Partly working** or **Planned**, never
**Working today**. Status tags no longer carry a phase number (the real tags do not either).

Truth sources: `docs/mvp/STATUS.md`, the v7 parity plan and the D3–D7 briefs, the five v7 gap reports, and
the real web wording (`apps/web/lib/application/*.ts`: screen-status, studio-improve-loop, studio-retrain,
studio-monitoring, studio-data-goal, studio-findings, role-labels, studio-agents, coming-next).

## خلاصه به فارسی

نسخه‌ی ۸ همان طراحی نسخه‌ی ۷ است، ولی هر صفحه حالا همان کاری را نشان می‌دهد که محصول واقعی امروز انجام می‌دهد.
مهم‌ترین تغییرها: حلقه‌ی بهبود با تلاش‌های «مهروموم‌شده» (تست نهایی استفاده نمی‌شود) و فقط یک عدد تست نهایی بعد
از تأیید یک انسان؛ این حلقه و حکم «بهتر از نویز» روی طراحی تست زمان‌دار (مثل پروژه‌ی Northwind) اجرا نمی‌شوند، پس
نمونه‌ی حلقه از پروژه‌ی دیگری نشان داده شده؛ بازآموزی با سه حالت خاموش / از من بپرس / خودکار (خودکار فقط
بازآموزی را شروع می‌کند، جابه‌جایی مدل همیشه با انسان است)؛ مقایسه با عبارت «همان نوع تست، آخرین ماه‌ها»؛ آستانه‌های
پایش قابل ویرایش برای مدیران؛ ۱۵ بررسی واقعی؛ نقش‌های واقعی و «مشاهده به‌عنوان» فقط برای مدیر؛ دستیار به‌صورت
«بتا»، پیش‌فرض خاموش؛ پرسش‌های تیم داده در نمای کسب‌وکار «برنامه‌ریزی‌شده».

## Change list (screen → what changed → why)

| Screen | What changed in v8 | Why (STATUS row) |
| --- | --- | --- |
| Shell | Role switcher removed. "View as" is an admin-only preview (Admin (your own view) / Data scientist / Analyst (view only) / Business user) with a "Preview: you still have admin rights; nothing is changed" banner and Stop the preview. Assistant button labelled Beta. Sidebar: no "planned" tags on Improve, Monitoring, Business view; groups Other views and Help. | V7-D1, V7-W1-UI-A |
| Status tags | No "· Phase N" in any tag; tags follow the real registry. | V7-W1-UI-A |
| Home | Needs-you rows say who raised each item and where (no evidence numbers): retrained model ready, drift alert, improvement loop waiting. Running meter "Model being trained: fold 3 of 5". Activity uses the real event kinds (upload, run finished with CV score, drift alert, weekly scoring, retrain started / compared). Score column = CV score of the model in use. Removed: calibration-warning item and rejected-upload item (not real inbox kinds). | V7-W2, V7-W1-A, V7-D5A, V7-D4E |
| Projects | Columns as built (Where it is, CV score of the model in use); New project is the 3-step dialog with the test-design confirm ("Training starts only after you confirm"). | V7-W2, V7-D2B |
| Inbox | Real kinds only: challenger ready (Switch / Keep with the real approval notes), drift alert, improvement loop waiting. "Applied automatically" is empty with AI off. Done tab with who-resolved pills; no agreement pills while AI is off. | V7-W2, V7-D5C, V7-D4E |
| Data | Summary counts training rows only (20.7%, not 21.3%). "Target over time": the newer file shows the final-test months as a sealed band with no line; the file in use shows them "Disclosed after the final test" (the model in use has had its final test). Versions: newer file uploaded by a person, upload log (accepted + rejected, "The spreadsheet could not be read", kept 90 days, metadata only). Data checks split into "Checked on upload" (5 checks) and "Checked inside each run" (leakage only there). Access: the real key-value card; removed "raw rows never leave the workspace" and the role matrix. | V7-W1-C, V7-D3A, V7-D3B, V7-A2 |
| Goal | No "Edit goal" (not built). Test design drawn to scale by rows, no dates; why-sentence from row counts. "Who decided" with Suggested by / Confirmed by / Why. New note: a time-ordered design rules out the noise verdict and the loop. | V7-W2, V7-D3B |
| Experiments | A run holds many models (Run 1 tried 9; changes are new runs from a finished one). Real 15 trust checks in engine order with "not checked" kept apart. Real step order (final test set aside right after reading the file); no score in the final-test step. Run 3 tries: "retried automatically" only, no "smaller batch". "Add a feature" removed (no feature engineering); the real 8 change kinds. Compare gives the difference but no noise verdict on time-ordered folds. | V7-A3, V7-D2A, V7-D2B; gap exp U9 |
| Improve | Tag Working today. Loop tab: Northwind's time-ordered design cannot start a loop (real refusal), so the loop is shown on another project (Late payment risk, shuffled folds): goal + budget, threshold step, sealed tries "final test not used", verdicts beats noise / within noise, the best try so far, spend line "AI off — $0", stop reason, Accept (a person only) → one final run, one final-test number; or Keep. "Try one change yourself" tab: standing banner, threshold step, another family, result "Can't compare" with look count. | V7-D4A–D4E, V7-B4 |
| Model | v1 from Run 1; the chosen threshold is a recorded choice only (scoring keeps the locked 0.31). Final test without dates. Versions with CV scores and statuses (v5 waiting for approval). "Approved for scoring" is Working today. Feature contract with roles. | V7-W1-B, V7-W2, V7-B2-UI; gap model U16 |
| Predictions | Tag Working today. Reasons in the engine's words ("…: high (top 20%), raised the score"), "what the model used, not causes", score not "chance". Weekly scoring card with what it never does. | V7-D2A, V7-W2, V7-B2b-UI |
| Monitoring | Tag Working today. Charts mark "within chance" and "not judged"; precision floor line. "What changed" built from numbers (no causal story). Steps: find drift and floors → retrain on newest data (policy Ask me) → show beside the model in use → switch or keep (a person). Policy control Off / Ask me / Automatic (Automatic only starts a retrain). Side-by-side "Same kind of test, latest months" with the real facts; real outcomes beside it labelled "Not the same data". Thresholds form with the server's bounds, reason, applies to future weeks. | V7-D5A, V7-D5B, V7-D5C, V7-W2 |
| History | Real event kinds incl. uploads, thresholds and policy changes, outcomes link; newer-file banner; Correct only for person-recorded decisions; approvals point to Model › Approved for scoring. AI items removed (AI off). | V7-W2, V7-A5, V7-D5B |
| Assistant panel | Labelled Beta, Partly working ("panel being built"), off by default, provider note (Anthropic), cost per conversation with the $1.00 limit, real model name with "price not confirmed", real quick actions; no "What did Improve find?" (no such assistant tool); no test-design card (no such tool). | D6 brief (not built yet) |
| AI suggestions panel | Planned: lists existing suggestions for the screen, never acts; examples marked. | D6 brief item 21 |
| Settings | Real role names and the 4 real abilities; no "Invite" and no "Last active"; Usage & cost Working today (AI spend $0.00, AI off); Notifications (6 real kinds, email off) and Integrations (webhooks card: 5 addresses, 6 events, signed) tagged Partly working · no screen yet. | V7-B6, V7-D1; gap ai_admin U12 |
| AI settings | Planned screen with Beta: on/off, spend, Claude Sonnet 5.5, data exposure incl. chat text to the provider, problem reports, Turn AI off now. Per-kind table read-only; "Automatic" locked and "Not tested yet"; ID-like columns and next run can never be automatic. Coming-next card watermarked "Example, not your data". | D6 brief, V7-D2B; gap ai_admin U12 |
| Connect | The 5 real token abilities, expiry 7/30/90 days, password confirm, token prefix/status/expiry; what a token cannot do. | V7-A6; gap ai_admin U12 |
| Business view | Working today; "Questions from the data team" shown as the D7 brief ships it (person to person, replies, open first) and tagged Planned. | P7.6-A, D7 brief (not built yet) |
| Platform console | Partly working: Overview / Workspaces / Failed jobs as built (no plan/usage columns); backups as the "Not configured" Coming-next card; planned tabs listed. | V7-D2B, operator console |
| What's real today | Rewritten from the real state above. | V7-W1-UI-A |

## Known limits of this demo

- Northwind churn is time-ordered, so the real product cannot run the improvement loop or a noise verdict on
  it; the loop is shown on Late payment risk instead. That is the product's real behaviour, not a demo gap.
- Assistant, AI settings screen, per-screen AI suggestions, business-view questions, notifications and
  webhook screens are not built yet; they are drawn from their briefs and tagged honestly.

## Build notes

Self-contained folder; edit `src/*.html`, `app.js`, `guide.js`, `charts.py`, then run
`python3 demo/product/versions.py build`. Open: `python3 -m http.server 8787 --directory demo/product` →
`http://localhost:8787/v8/`. Words table: as v7 ("final test set (used once)", never "holdout").
