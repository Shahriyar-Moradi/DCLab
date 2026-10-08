// DCLab prototype v5 · guidance layer (flow bar, guide, terms, glossary, next step).
// Adds to every screen, without changing its content:
//   - a flow bar on project screens: the state graph, one node per versioned step
//   - a guide under the title: what the screen is for, how to use it, what you get
//   - explanations for ML and AI terms (hover or focus), plus a Glossary dialog
//   - a "Next step" card at the end
(function () {
  'use strict';

  var page = document.body.getAttribute('data-page') || location.pathname.split('/').pop();
  var content = document.querySelector('.content');
  if (!content) return;

  // ---------------------------------------------------------------- workflow
  // The state graph of the sample project, one node per versioned step. Each node opens the screen that owns it.
  var FLOW = [
    { page: 'data.html', label: '1 DatasetVersion', value: 'DS v1 ★', note: '48,210 rows · v3 new', state: 'done' },
    { page: 'lab.html', label: '2 ProblemSpec', value: 'Spec v1 ★', note: 'recall ≥ 0.80 · PR-AUC', state: 'done' },
    { page: 'pipeline.html', label: '3 SplitPlan', value: 'SP-1 ★', note: 'temporal · 5 folds', state: 'done' },
    { page: 'graph.html', label: '4 FeatureRecipe', value: 'FR v3 ★', note: '29 features · 2 excluded', state: 'done' },
    { page: 'experiments.html', label: '5 Experiment', value: 'E9 running', note: 'E7 best · CV 0.71', state: 'run' },
    { page: 'improve.html', label: '6 Improve loop', value: 'IL-2 done', note: 'goal met', state: 'done', phase: 'Phase 5' },
    { page: 'models.html', label: '7 ModelVersion', value: 'MV v4 ★', note: 'champion ref', state: 'done' },
    { page: 'models.html#releases', label: '8 Release', value: 'R-15', note: 'waits for you', state: 'wait', phase: 'Phase 7' },
    { page: 'monitoring.html', label: '9 MonitoringWindow', value: 'W-9 drift', note: 'retrain started', state: 'alert', phase: 'Phase 7' }
  ];

  // Every screen → its Studio route, the phase and prompt that build it, and where that stands (docs/mvp STATUS.md, 2026-10-08).
  var PAGE_META = {
    'home.html': { route: '/home', phase: 'Phase 4 · Stage 5', prompts: 'P4.15-A, P4.15-UI', status: 'done' },
    'inbox.html': { route: '/inbox', phase: 'Phase 4 · Stage 5', prompts: 'P4.16-A, P4.16-UI', status: 'done' },
    'lab.html': { route: '/projects/[id]/lab', phase: 'Phase 4 · Stage 4 (after checkpoint G6)', prompts: 'A3-UI, A4-A · backend P6.3-B done', status: 'planned' },
    'data.html': { route: '/projects/[id]/data', phase: 'Phase 4 · Stage 2', prompts: 'P4.1-C, P4.10-UI', status: 'done' },
    'pipeline.html': { route: '/projects/[id]/pipeline/[experimentId]', phase: 'Phase 4 · Stage 3', prompts: 'P4.17-UI', status: 'done' },
    'experiments.html': { route: '/projects/[id]/experiments[/[eid]]', phase: 'Phase 4 · Stage 2', prompts: 'P4.1-A, P4.3-A, P4.4-A · P5.2-UI planned', status: 'done' },
    'improve.html': { route: '/projects/[id]/improve', phase: 'Phase 5', prompts: 'P5.3-A, P5.4-A, P5.5-A, P6.5-A', status: 'progress' },
    'graph.html': { route: '/projects/[id]/graph', phase: 'Phase 4 · Stage 2', prompts: 'P4.2-A, P4.3-A', status: 'done' },
    'models.html': { route: '/projects/[id]/models[/[mvId]]', phase: 'Phase 4 · Stage 3 (+ Phase 7)', prompts: 'P4.9-UI, P4.11-UI done · P7.2-A, P7.5-A planned', status: 'done' },
    'monitoring.html': { route: '/projects/[id]/monitoring', phase: 'Phase 7', prompts: 'P7.4-A, P7.5-A, P7.8-A', status: 'planned' },
    'decisions.html': { route: '/projects/[id]/decisions', phase: 'Phase 4 · Stage 2', prompts: 'P4.4-A (service P2.5-A)', status: 'done' },
    'governance.html': { route: '/governance', phase: 'Phase 4 · Stage 4', prompts: 'P6.11-UI · API P6.11-A done', status: 'planned' },
    'agents.html': { route: '/agents', phase: 'Phase 4 · Stage 2 (+ Stage 4)', prompts: 'P4.8-UI done · A2-UI, P6.8-UI planned', status: 'done' },
    'settings.html': { route: '/settings', phase: 'grows per phase', prompts: 'members exist · P8.4-A, P8.7-A, P8.8-A planned', status: 'done' },
    'client.html': { route: '/outcomes', phase: 'Phase 7', prompts: 'P7.6-A, P7.7-A', status: 'planned' },
    'operator.html': { route: '/operator', phase: 'Phase 8', prompts: 'P8.5-UI, P8.7-A', status: 'planned' },
    'map.html': { route: '— (demo device)', phase: 'docs/mvp', prompts: 'ROADMAP, STATUS, STUDIO_DESIGN', status: 'done' }
  };
  var STATUS_WORD = { done: 'built', progress: 'in progress', planned: 'planned' };

  // ---------------------------------------------------------------- guides
  var GUIDES = {
    'home.html': {
      what: 'Everything that needs you across all projects: decisions, running jobs, what the AI did on its own and what it cost.',
      how: ['Start with the four numbers at the top.', 'Open a project from <b>Projects</b> to continue where it stands.', 'Check <b>Activity</b> for what agents, rules and people did since your last visit.'],
      get: 'A short list of what to do today.',
      now: '7 decisions are waiting. The most important one: approve release R-15 for Northwind churn.',
      next: ['inbox.html', 'Review the 7 waiting decisions', 'Approve, reject or answer each one with its evidence in front of you.']
    },
    'inbox.html': {
      what: 'Every decision that needs a person, in one place.',
      how: ['<b>Needs a decision</b>: read the summary and evidence, then approve, reject or answer.', '<b>Applied automatically</b>: see what the AI did on its own (L2) and undo anything you disagree with.', '<b>Done</b>: look back at what was decided and by whom.'],
      get: 'Each choice is saved as a decision record with your name, the evidence and a way to undo it.',
      next: ['decisions.html', 'See the full decision history', 'Every choice by a rule, the AI or a person, with its evidence.']
    },
    'data.html': {
      what: 'Understand your dataset before training: which version is current, how each column is used, and anything that could make results wrong.',
      how: ['<b>Versions</b>: confirm which upload is current (★).', '<b>Columns &amp; roles</b>: check how each column is used. The rule\'s answer and the AI\'s answer are side by side.', '<b>Leakage audit</b> and <b>Findings</b>: resolve every flag and answer open questions.'],
      get: 'A dataset you can trust, with every column decision recorded.',
      next: ['lab.html', 'Give the lab agent your goal', 'It proposes a plan for this data; you approve it.']
    },
    'lab.html': {
      what: 'Tell the lab agent what you want to predict. It prepares the data, proposes a plan, trains and reports back. You make the important calls.',
      how: ['Attach data and describe the goal in one sentence, for example "catch at least 80% of churners".', 'Review the plan. <b>L1</b> items wait for your OK; <b>L2</b> items were applied and can be undone.', 'Accept and run, then read the result and answer the agent\'s questions.'],
      get: 'A trained model with a plain-language report, tested once on data it never saw.',
      next: ['pipeline.html', 'See how run E1 was built', 'Every step, its check and the files it produced.']
    },
    'pipeline.html': {
      what: 'Proof that a run was done correctly: every step, what the rule found, what the AI added, and the files produced.',
      how: ['Read the stages top to bottom. Green dot: passed. Orange: needs a look.', 'Inside each stage, <b>Rule</b> notes come from fixed code and <b>AI</b> notes from an agent.', 'Use <b>Run details</b> at the end for decision points, cost and provenance.'],
      get: 'Evidence you can show anyone: each step, its check and its file fingerprint.',
      next: ['experiments.html', 'Compare this run with the others', 'Runs on the same split plan can be compared fairly.']
    },
    'experiments.html': {
      what: 'Every run, compared fairly, plus tools to branch a new one.',
      how: ['<b>All experiments</b>: runs that share a split plan (SP-1) can be compared directly.', '<b>Detail</b>: candidates, folds, the threshold and the Critic review for one run.', '<b>Compare</b> two runs, or <b>Branch</b> a new run with one typed change.'],
      get: 'The best run, chosen by a fixed rule, with code that reproduces it.',
      next: ['improve.html', 'Let the improve loop reach your goal', 'It tries one change at a time and stops by rule.']
    },
    'improve.html': {
      what: 'Reach a goal automatically, for example "keep recall at 0.80 or more and raise precision".',
      how: ['Set the goal, the constraint and a budget under <b>Loop setup</b>.', 'Each iteration tries one change. The agent and the rule table both propose; the table shows which one was used.', 'The loop stops by rule. The holdout is scored once, on the result you accept.'],
      get: 'A better run, with a record of every change tried and why.',
      next: ['models.html', 'Put the best run into use', 'Promote it to champion and schedule scoring.']
    },
    'models.html': {
      what: 'What is in production: model versions, releases and scoring runs.',
      how: ['<b>Versions</b>: the champion (★) is the model in use.', '<b>Releases</b>: approve or roll back. <b>Batch predictions</b>: each scoring run and its result.', '<b>Feature contract</b>: what an input file must contain to be scored.'],
      get: 'Predictions delivered on schedule from an approved model.',
      now: 'Model v5 is waiting for approval as release R-15.',
      next: ['monitoring.html', 'Check the model is still right', 'Drift and real performance, week by week.']
    },
    'monitoring.html': {
      what: 'Check that the model still sees the kind of data it learned from, and that it is still right.',
      how: ['<b>Signals</b>: drift per weekly window (PSI) and real performance once outcomes arrive.', '<b>Ops agent chain</b>: what happened after an alert, and who acted.', '<b>Thresholds</b>: the levels that trigger alerts and rollbacks.'],
      get: 'Early warning, and a retrained model proposed for your approval.',
      now: 'Drift alert this week. A retrained model (release R-15) is waiting in your Inbox.',
      next: ['inbox.html', 'Approve or reject release R-15', 'The evidence and a one-click rollback are attached.']
    },
    'decisions.html': {
      what: 'The project\'s full history: every choice by a rule, the AI or a person, with its evidence and a way back.',
      how: ['Filter by who decided (rule, agent, Jev, human) or by type.', 'Open a record to see its evidence and what it changed.', 'Revert or correct: this adds a new record; nothing is ever edited.'],
      get: 'An audit trail you can export.',
      next: ['graph.html', 'See how everything connects', 'The lineage graph links data, runs, models and releases.']
    },
    'graph.html': {
      what: 'How data, splits, runs, models and releases connect.',
      how: ['★ marks what is current. Orange nodes are out of date because something upstream moved.', 'Click any node; its details open on the right.', 'Use <b>Preview ref move</b> to see what would go out of date before you change anything.'],
      get: 'Lineage for any model in use, back to the exact data it was built on.',
      next: ['models.html', 'Go to the model in use', 'Versions, releases and scoring runs.']
    },
    'governance.html': {
      what: 'Decide what the AI may do here, what data it may see and how much it may spend; then check what it did.',
      how: ['<b>Policy</b>: models, data allowed to leave, autonomy and approvals.', '<b>Decision points &amp; trust levels</b>: how far each AI decision is trusted (L0–L3) and the evidence for it.', '<b>Budgets</b>, <b>Kill switches</b>, <b>Incidents</b>, <b>Audit</b>: spend, off switches and replay.'],
      get: 'AI you can limit, switch off and audit.',
      next: ['agents.html', 'See the agents and their tools', 'What each agent does and what it is allowed to call.']
    },
    'agents.html': {
      what: 'The AI agents DCLab runs, the tools they may use, their recorded runs, and how your own tools connect.',
      how: ['<b>Agent catalog</b>: what each agent does, its model and trust level.', '<b>Tool registry</b>: every action an agent or MCP client can take, and what checks it.', '<b>Connect</b>: MCP, Python SDK, CLI or REST, with a scoped token.'],
      get: 'Your own tools (Claude Code, Cursor, scripts) driving DCLab within the same limits.',
      next: ['settings.html', 'Manage members and usage', 'Who can do what, and what you have used.']
    },
    'settings.html': {
      what: 'Members, plan and usage, integrations, data safety and notifications.',
      how: ['<b>Members &amp; roles</b>: who can do what.', '<b>Plan &amp; usage</b>: compute, AI spend and storage.', '<b>Integrations</b>, <b>Data safety</b> and <b>Notifications</b>.'],
      get: 'A workspace set up the way your team works.',
      next: ['home.html', 'Back to Home', 'What needs you today.']
    },
    'client.html': {
      what: 'This week\'s churn predictions, how far to trust them, and answers to your questions.',
      how: ['Read the update and the four numbers at the top.', 'Download this week\'s list, or open it below with the reasons for each customer.', 'Answer the open question and ask your own.'],
      get: 'A list of customers likely to leave, with reasons, from a model your data team approved.'
    },
    'operator.html': {
      what: 'Platform health across all workspaces, for the DCLab team. Metadata only, never customer data.',
      how: ['<b>Workspaces</b>: usage and limits per customer.', '<b>Jobs</b> and <b>Quarantine</b>: what is running and which uploads are held.', '<b>Platform caps</b> and <b>Benchmarks</b>: the limits no workspace can change, and the quality gates.'],
      get: 'A platform that stays healthy, safe and within limits.'
    },
    'map.html': {
      what: 'For the product team: the journeys, what each screen covers, and what the design still leaves open.',
      how: ['Follow a journey to walk the product in order.', 'Check coverage against the roadmap.', 'Read the gaps and questions before planning the next build.'],
      get: 'A shared picture of what to build and what to leave out.'
    }
  };

  // ---------------------------------------------------------------- glossary
  var GLOSSARY = [
    { group: 'Testing a model', items: [
      ['Holdout', /\bhold-?out\b/i, 'Rows set aside before training and scored only once, at the very end. It shows how the model will do on data it has never seen.'],
      ['Cross-validation (CV)', /\bCV\b|\bcross-validation\b/i, 'Training and validating several times on different slices (folds) of the training rows, to get a stable score without touching the holdout.'],
      ['Fold', /\bfolds?\b/i, 'One slice of the training rows used for validation in cross-validation. With time data, folds follow time order.'],
      ['Split plan', /\bsplit plan\b|\bSP-\d\b/i, 'The fixed record of which rows are holdout and which fold each training row is in. Runs that share a split plan can be compared fairly.'],
      ['Baseline (dummy)', /\bdummy baseline\b|\bdummy\b/i, 'A model that just predicts the average. Every real model must beat it, and it can never be chosen as the winner.'],
      ['PR-AUC', /\bPR-AUC\b/, 'How well the model ranks positives above negatives, focused on the rare class. 1.0 is perfect; the baseline scores about the positive rate.'],
      ['Recall', /\brecall\b/i, 'Of all customers who really churn, the share the model catches.'],
      ['Precision', /\bprecision\b/i, 'Of all customers the model flags, the share who really churn.'],
      ['Threshold', /\bthreshold\b/i, 'The risk score above which a customer is flagged. Chosen on validation data to meet your goal, then locked.'],
      ['Out-of-fold (OOF)', /\bOOF\b|\bout-of-fold\b/i, 'Predictions each row got from a model that did not train on it. Used to choose the threshold without touching the holdout.'],
      ['Leakage', /\bleakage\b|\bleak\b/i, 'A column that gives away the answer, for example a cancellation date when predicting cancellation. It makes results look better than they will be.'],
      ['Verifier', /\bverifier\b/i, 'Automatic checks run after every training run, such as "the holdout was never used for training".']
    ]},
    { group: 'Decisions and trust', items: [
      ['L0 · Shadow', /\bL0\b/, 'The AI answers but only a rule decides. Used to measure the AI before trusting it.'],
      ['L1 · Ask first', /\bL1\b/, 'The AI suggests; a person approves before anything changes.'],
      ['L2 · Automatic, can undo', /\bL2\b/, 'Applied automatically when checks pass. You are shown it and can undo it.'],
      ['L3 · Automatic, you are told', /\bL3\b/, 'Done automatically within your policy limits; you are notified.'],
      ['Decision point', /\bdecision points?\b/i, 'A place in the pipeline where a choice is made, such as a column\'s role or the split strategy. Both a rule and the AI answer; the trust level decides which answer is used.'],
      ['Decision record', /\bdecision records?\b/i, 'A permanent entry for every choice: who made it, why, the evidence and how to undo it. Records are never edited.'],
      ['Rule', /\brule\b/i, 'Fixed, tested code that makes a decision the same way every time. Every AI decision is checked against one.'],
      ['Proposal', /\bproposals?\b/i, 'A change suggested by an agent that waits for approval or for the checks that allow it.']
    ]},
    { group: 'AI agents', items: [
      ['Lab agent', /\blab agent\b|\blead agent\b/i, 'The assistant you talk to in the Lab. It runs the project through the same checked tools a person would use.'],
      ['Critic', /\bCritic\b/, 'An agent that reviews every finished run and points to the numbers behind each remark. It never changes anything itself.'],
      ['Dataset investigator', /\bDatasetInvestigator\b|\bdataset investigator\b/i, 'An agent that explains what the data checks found and asks you questions.'],
      ['Planner', /\bPlanner\b/, 'An agent that suggests the goal, the split and which model types to try.'],
      ['Improve agent', /\bImprovementHypothesis\b/, 'An agent that suggests the next change to try in the improve loop.'],
      ['Ops agent', /\bOps agent\b/i, 'An agent that watches monitoring, retrains after drift and proposes releases within your policy.'],
      ['Jev', /\bJev\b/, 'A fast service for small yes/no or multiple-choice judgments, such as "is this column an ID?". Used under fixed rules.'],
      ['Replay', /\breplay\b/i, 'Re-running a recorded AI run offline to prove it produced the same result.']
    ]},
    { group: 'Production', items: [
      ['Champion', /\bchampion\b/i, 'The model version currently in use. It changes only through an approved decision.'],
      ['Release', /\brelease\b/i, 'Putting a model version into use for scoring, on a schedule or on demand.'],
      ['Batch prediction', /\bbatch predictions?\b|\bbatch scoring\b/i, 'Scoring a whole file of customers at once, for example every night.'],
      ['Feature contract', /\bfeature contract\b/i, 'The columns, types and allowed values an input file must have. Files that break it are refused, not scored.'],
      ['Drift', /\bdrift\b/i, 'New data looking different from the data the model learned from.'],
      ['PSI', /\bPSI\b/, 'Population stability index: a drift score per column. Under 0.1 is stable; 0.2 or more triggers an alert.']
    ]},
    { group: 'Lineage', items: [
      ['Ref (★)', /\brefs?\b/i, 'A pointer to the current version of something: the current dataset, split plan or champion.'],
      ['Stale', /\bstale\b/i, 'Out of date because something it was built on has been replaced.'],
      ['Branch', /\bbranch(?:es|ed)?\b/i, 'A new run made from an existing one with one recorded change. It reuses the same split plan.'],
      ['Change set', /\bchange sets?\b/i, 'The exact list of changes a branch makes compared with its parent run.']
    ]},
    { group: 'Developer tools', items: [
      ['MCP', /\bMCP\b/, 'Model Context Protocol. Lets coding agents such as Claude Code or Cursor use DCLab\'s tools.'],
      ['Service token', /\bservice tokens?\b/i, 'A key for scripts and agents, limited to one workspace and a list of allowed actions, with an expiry date.']
    ]}
  ];
  var TERMS = [];
  GLOSSARY.forEach(function (g) { g.items.forEach(function (t) { TERMS.push({ name: t[0], re: t[1], def: t[2] }); }); });
  var LEVEL_TIPS = { L0: TERMS[12].def, L1: TERMS[13].def, L2: TERMS[14].def, L3: TERMS[15].def };

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (_) { return null; }
  }

  var head = content.querySelector('.page-head');
  var guide = GUIDES[page];

  // ---------------------------------------------------------------- flow bar
  var flowPages = FLOW.map(function (n) { return n.page.split('#')[0]; });
  var flowIndex = flowPages.indexOf(page);
  if (flowIndex > -1 && head) {
    var wantHash = location.hash.slice(1);
    FLOW.forEach(function (n, i) { if (n.page === page + '#' + wantHash && wantHash) flowIndex = i; });
    var bar = document.createElement('nav');
    bar.className = 'flowbar';
    bar.setAttribute('aria-label', 'Project steps: where this screen sits in the state graph');
    bar.innerHTML = '<div class="fb-title"><b>Northwind churn</b><small>state graph · 9 nodes · ★ = current ref</small></div><ol>' + FLOW.map(function (n, i) {
      return '<li class="fb-' + n.state + (i === flowIndex ? ' fb-current' : '') + '"><a href="' + n.page + '"' + (i === flowIndex ? ' aria-current="step"' : '') + ' title="' + n.label.replace(/^\d+ /, '') + ': ' + n.note + '">' +
        '<span class="fb-k">' + n.label + (n.phase ? ' <em>' + n.phase + '</em>' : '') + '</span><span class="fb-v">' + n.value + '</span><span class="fb-s">' + n.note + '</span></a></li>';
    }).join('') + '</ol>';
    content.insertBefore(bar, head);
  }

  // ---------------------------------------------------------------- guide
  if (guide && head) {
    var box = document.createElement('section');
    box.className = 'guide';
    box.setAttribute('aria-label', 'How to use this screen');
    var nextCol = guide.next
      ? '<a class="g-next" href="' + guide.next[0] + '"><b>' + guide.next[1] + ' →</b><small>' + guide.next[2] + '</small></a>'
      : '<p>' + guide.get + '</p>';
    var meta = PAGE_META[page];
    var metaLine = meta ? '<p class="g-meta"><span class="mono">' + meta.route + '</span> · ' + meta.phase + ' · ' + meta.prompts + ' · <b class="st-' + meta.status + '">' + STATUS_WORD[meta.status] + '</b></p>' : '';
    box.innerHTML =
      '<div class="g-col"><h2 class="g-h">What this screen is</h2><p>' + guide.what + '</p>' + metaLine +
        (guide.now ? '<p class="g-now"><span aria-hidden="true">●</span> ' + guide.now + '</p>' : '') + '</div>' +
      '<div class="g-col"><h2 class="g-h">How to use it</h2><ol>' + guide.how.map(function (h) { return '<li>' + h + '</li>'; }).join('') + '</ol>' +
        '<p class="g-legend"><span><i class="lg-rule"></i>Rule</span><span><i class="lg-ai"></i>AI</span><span><i class="lg-you"></i>You</span></p></div>' +
      '<div class="g-col"><h2 class="g-h">' + (guide.next ? 'Next step' : 'What you get') + '</h2>' + nextCol + (guide.next ? '<p class="small">' + guide.get + '</p>' : '') + '</div>' +
      '<button class="g-close" type="button" aria-label="Hide the guide">Hide</button>';
    head.insertAdjacentElement('afterend', box);
    var toggle = document.querySelector('[data-guide-toggle]');
    function setGuide(open) {
      box.hidden = !open;
      if (toggle) { toggle.setAttribute('aria-pressed', open ? 'true' : 'false'); toggle.textContent = open ? 'Hide guide' : 'Show guide'; }
      store('dclab-v5-guide', open ? 'open' : 'closed');
    }
    box.querySelector('.g-close').addEventListener('click', function () { setGuide(false); if (toggle) toggle.focus(); });
    if (toggle) toggle.addEventListener('click', function () { setGuide(box.hidden); });
    setGuide(store('dclab-v5-guide') !== 'closed');
  } else {
    var t = document.querySelector('[data-guide-toggle]');
    if (t) t.hidden = true;
  }

  // ---------------------------------------------------------------- next step
  if (guide && guide.next) {
    var next = document.createElement('a');
    next.className = 'next-step';
    next.href = guide.next[0];
    next.innerHTML = '<span class="ns-label">Next step</span><b>' + guide.next[1] + ' <span aria-hidden="true">→</span></b><small>' + guide.next[2] + '</small>';
    var review = content.querySelector('details.review');
    if (review) content.insertBefore(next, review); else content.appendChild(next);
  }

  // ---------------------------------------------------------------- plan layer: route chip, built-today view, AI on/off
  var metaNow = PAGE_META[page];
  var crumbs = document.querySelector('.topbar .crumbs');
  if (metaNow && crumbs) {
    var chip = document.createElement('span');
    chip.className = 'route mono';
    chip.title = 'Studio route for this screen (STUDIO_DESIGN.md §4)';
    chip.textContent = metaNow.route;
    crumbs.insertAdjacentElement('afterend', chip);
  }
  // elements tagged data-arrives="Phase 7 · P7.5-A" get a small chip; in the built-today view they are dimmed
  document.querySelectorAll('[data-arrives]').forEach(function (el) {
    if (el.querySelector(':scope > .arrives')) return;
    var tag = document.createElement('span');
    tag.className = 'arrives';
    tag.textContent = el.getAttribute('data-arrives');
    tag.title = 'Not built yet; this prompt ships it (docs/mvp). The sync rule says: no backend, no element.';
    if (el.matches('button, a')) el.appendChild(tag);
    else { var h = el.querySelector(':scope > h2, :scope > h3, :scope > .sect-head h2, :scope > .sect-head h3, :scope > .page-head h1'); (h || el).insertAdjacentElement(h ? 'beforeend' : 'afterbegin', tag); }
  });
  // section tabs: copy the chip onto the tab button
  document.querySelectorAll('.sect > section[data-tab][data-arrives]').forEach(function (sec) {
    var card = sec.parentElement, i = Array.prototype.indexOf.call(card.querySelectorAll(':scope > section[data-tab]'), sec);
    var btn = card.querySelectorAll('.sect-tabs button')[i];
    if (btn && !btn.querySelector('.arrives')) { var t = document.createElement('span'); t.className = 'arrives'; t.textContent = sec.getAttribute('data-arrives'); btn.appendChild(t); }
  });
  var viewSel = document.querySelector('[data-plan-view]');
  function setView(v) {
    document.body.setAttribute('data-view', v);
    store('dclab-plan-view', v);
    if (viewSel) viewSel.value = v;
    var old = document.getElementById('built-banner');
    if (old) old.remove();
    if (v === 'built' && metaNow && metaNow.status !== 'done' && head) {
      var bn = document.createElement('div');
      bn.id = 'built-banner';
      bn.className = 'banner warn';
      bn.innerHTML = '<b>Not built yet.</b> This screen arrives in ' + metaNow.phase + ' (' + metaNow.prompts + '). It is shown as the design target; the sync rule (STUDIO_DESIGN.md §1) means Studio draws nothing whose backend does not exist. <span class="sp"></span><a class="btn sm" href="map.html">See the plan</a>';
      content.insertBefore(bn, head);
    }
  }
  if (viewSel) viewSel.addEventListener('change', function () { setView(viewSel.value); });
  setView(store('dclab-plan-view') || 'plan');

  var aiBtn = document.querySelector('[data-ai-toggle]');
  function setAi(on) {
    document.body.classList.toggle('ai-off', !on);
    store('dclab-ai', on ? 'on' : 'off');
    if (aiBtn) { aiBtn.setAttribute('aria-pressed', on ? 'true' : 'false'); aiBtn.lastChild.nodeValue = on ? 'AI on' : 'AI off'; }
    var old = document.getElementById('ai-banner');
    if (old) old.remove();
    if (!on && head) {
      var ab = document.createElement('div');
      ab.id = 'ai-banner';
      ab.className = 'banner';
      ab.innerHTML = '<b>AI is off.</b> Every screen still works (ROADMAP § Hybrid AI model, rule 9). AI notes, proposals and agent runs are dimmed; at every decision point the rule\'s answer is the value used, and the Lab offers the forms instead of the chat. <span class="sp"></span><button class="btn sm" type="button" data-ai-on>Turn AI on</button>';
      content.insertBefore(ab, head);
      ab.querySelector('[data-ai-on]').addEventListener('click', function () { setAi(true); });
    }
  }
  if (aiBtn) aiBtn.addEventListener('click', function () { setAi(document.body.classList.contains('ai-off')); });
  setAi(store('dclab-ai') !== 'off');

  // ---------------------------------------------------------------- explained terms
  var SKIP = 'a,button,code,pre,script,style,svg,textarea,input,select,option,h1,th,label,kbd,.term,.lvl,.pill,.guide,.journey-bar,.next-step,.from,.when,.mono,.cite,.tabs,.sect-tabs,.nav,.topbar,.dcv';
  // each term is explained once per tab (or once per screen when there are no tabs)
  function scopeOf(node) { return node.parentElement.closest('section[data-tab], [data-tabpanel]') || content; }
  var used = new Map();
  var walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, {
    acceptNode: function (n) {
      if (!n.nodeValue.trim() || !n.parentElement || n.parentElement.closest(SKIP)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  var nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(function (node) {
    var scope = scopeOf(node);
    if (!used.has(scope)) used.set(scope, {});
    var seen = used.get(scope);
    var text = node.nodeValue, hits = [];
    TERMS.forEach(function (term, ti) {
      if (seen[ti] || /^L[0-3] /.test(term.name)) return;
      var m = term.re.exec(text);
      if (m) hits.push({ start: m.index, end: m.index + m[0].length, ti: ti });
    });
    if (!hits.length) return;
    hits.sort(function (a, b) { return a.start - b.start; });
    var frag = document.createDocumentFragment(), pos = 0;
    hits.forEach(function (h) {
      if (h.start < pos) return;
      frag.appendChild(document.createTextNode(text.slice(pos, h.start)));
      var span = document.createElement('span');
      span.className = 'term';
      span.tabIndex = 0;
      span.setAttribute('data-tip', TERMS[h.ti].name + ' — ' + TERMS[h.ti].def);
      span.textContent = text.slice(h.start, h.end);
      frag.appendChild(span);
      seen[h.ti] = true;
      pos = h.end;
    });
    frag.appendChild(document.createTextNode(text.slice(pos)));
    node.parentNode.replaceChild(frag, node);
  });
  content.querySelectorAll('.lvl').forEach(function (el) {
    var key = (el.textContent.match(/L[0-3]/) || [])[0];
    if (key) { el.setAttribute('data-tip', key + ' — ' + LEVEL_TIPS[key]); el.tabIndex = 0; }
  });

  // one floating tooltip, positioned in the viewport so tables never clip it
  var tip = document.createElement('div');
  tip.className = 'tip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.appendChild(tip);
  function showTip(el) {
    tip.innerHTML = esc(el.getAttribute('data-tip')).replace(/^(.*?) — /, '<b>$1</b>');
    tip.hidden = false;
    var r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    var left = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), window.innerWidth - w - 12);
    var top = r.top - h - 10 < 8 ? r.bottom + 10 : r.top - h - 10;
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  }
  function hideTip() { tip.hidden = true; }
  document.addEventListener('mouseover', function (e) { var el = e.target.closest && e.target.closest('[data-tip]'); if (el) showTip(el); });
  document.addEventListener('mouseout', function (e) { if (e.target.closest && e.target.closest('[data-tip]')) hideTip(); });
  document.addEventListener('focusin', function (e) { if (e.target.matches && e.target.matches('[data-tip]')) showTip(e.target); });
  document.addEventListener('focusout', hideTip);
  window.addEventListener('scroll', hideTip, true);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hideTip(); });

  // ---------------------------------------------------------------- glossary dialog
  var gButton = document.querySelector('[data-glossary]');
  if (gButton) {
    gButton.addEventListener('click', function () {
      var d = document.getElementById('glossary-dialog');
      if (!d) {
        d = document.createElement('dialog');
        d.id = 'glossary-dialog';
        d.className = 'prototype-dialog glossary-dialog';
        d.innerHTML = '<form method="dialog" class="dialog-card"><button class="dialog-x" value="close" aria-label="Close">×</button>' +
          '<h2>Glossary</h2><p class="dialog-lead">The terms used across DCLab, in plain words. Underlined words on any screen show the same explanation when you point at them.</p>' +
          '<input class="input g-search" type="search" placeholder="Find a term" aria-label="Find a term">' +
          GLOSSARY.map(function (g) {
            return '<section class="g-group"><h3>' + g.group + '</h3><dl>' + g.items.map(function (t) {
              return '<div class="g-item"><dt>' + esc(t[0]) + '</dt><dd>' + esc(t[2]) + '</dd></div>';
            }).join('') + '</dl></section>';
          }).join('') + '</form>';
        d.addEventListener('click', function (e) { if (e.target === d) d.close(); });
        document.body.appendChild(d);
        var search = d.querySelector('.g-search');
        search.addEventListener('input', function () {
          var q = search.value.trim().toLowerCase();
          d.querySelectorAll('.g-item').forEach(function (item) { item.hidden = q && item.textContent.toLowerCase().indexOf(q) === -1; });
          d.querySelectorAll('.g-group').forEach(function (group) { group.hidden = !group.querySelector('.g-item:not([hidden])'); });
        });
      }
      d.showModal();
    });
  }

  // numbered workflow steps in the sidebar
  document.querySelectorAll('.nav a .ic').forEach(function (ic) { if (/^\d$/.test(ic.textContent.trim())) ic.classList.add('step'); });
})();
