// DCLab demo v8 "Real product" · guidance layer: flow bar, guide strip, term tooltips, glossary, next step.
(function () {
  'use strict';

  var page = document.body.getAttribute('data-page') || '';
  var content = document.querySelector('.content');
  if (!content || document.body.getAttribute('data-gated')) return;

  // ---------------------------------------------------------------- the six steps
  var FLOW = [
    { page: 'data.html', label: '1 Data', value: 'Jun 2026 file', note: 'a newer file exists', state: 'done' },
    { page: 'goal.html', label: '2 Goal & test', value: 'Recall ≥ 0.80', note: 'time-ordered folds', state: 'done' },
    { page: 'experiments.html', label: '3 Experiments', value: '5 runs', note: 'model from Run 1', state: 'done' },
    { page: 'improve.html', label: '4 Improve', value: '1 guided try', note: 'no gain shown', state: 'done' },
    { page: 'model.html', label: '5 Model', value: 'v1 in use', note: 'v5 waiting for you', state: 'alert' },
    { page: 'predictions.html', label: '6 Predictions', value: '6,412 flagged', note: 'every Monday', state: 'done' },
    { page: 'monitoring.html', label: '7 Monitoring', value: 'Alert', note: 'drift and a floor', state: 'alert' },
    { page: 'history.html', label: '8 History', value: 'Every decision', note: 'who, what, why', state: 'done' }
  ];

  var GUIDES = {
    'index.html': {
      what: 'Your starting point: what needs a decision from you, where each project stands, and what is running.',
      how: ['Read <b>3 things need you</b>: who raised each one and where.', 'Follow the project steps from left to right.', 'Open the Inbox to decide.'],
      get: 'One place that tells you what to do next.',
      next: ['inbox.html', 'Open the Inbox', 'See the three things that need you.']
    },
    'inbox.html': {
      what: 'Everything that waits for a person: switching models, alerts, an improvement loop waiting for you. Each card says exactly what Approve does.',
      how: ['Open <b>Needs your decision</b>.', 'Read what raised it and the evidence link.', 'Approve or reject, with your reason.'],
      get: 'Decisions you can make in a minute, each with its reason.',
      next: ['projects.html', 'See all projects', 'Start a new one or open an existing one.']
    },
    'projects.html': {
      what: 'All projects in this workspace, with their goal, where each one is, and the cross-validation score of the model in use.',
      how: ['Find your project in the table.', 'Open it to see its steps.', 'Press <b>New project</b>: three steps, and training starts only after you confirm the test design.'],
      get: 'A clear list, and a guided way to start a new project.',
      next: ['data.html', 'Open Northwind churn', 'Start with the data.']
    },
    'data.html': {
      what: 'Look at the file before you train: how big it is, which columns will be used, and anything that could make results wrong.',
      how: ['Read the summary at the top (training rows only).', 'Check the <b>Used?</b> column: each column left out has a reason.', 'Open <b>Versions</b> for the upload log and <b>Data checks</b> for what was checked on upload and inside each run.'],
      get: 'A file you can trust, with every column decision visible.',
      next: ['goal.html', 'Set the goal and test design', 'Say what to predict and how it will be tested.']
    },
    'goal.html': {
      what: 'Say what to predict, how to rank models, and how the model will be tested so the result can be believed.',
      how: ['Check what we predict and the business rule.', 'Look at the test design, drawn to scale by rows.', 'See who suggested and who confirmed each part.'],
      get: 'A fixed test design, so every run is compared fairly.',
      next: ['experiments.html', 'Run the experiments', 'Each run compares a baseline with several models.']
    },
    'experiments.html': {
      what: 'Each run trains a baseline and several models on the same test design and picks the best on cross-validation only. A change to try is a new run that starts from a finished one.',
      how: ['Read the runs table: what each run changed and its score ± spread.', 'Open <b>Run detail</b> for the steps, times and tries.', 'Open <b>Trust checks</b> for the 15 checks in the order they run.'],
      get: 'One chosen run, with a reason you can explain.',
      next: ['improve.html', 'Try to improve it', 'One change yourself, or a loop within a budget where the test design allows it.']
    },
    'improve.html': {
      what: 'Two ways to look for a better model: try one change yourself, or start a loop within a budget whose tries are sealed (they never look at the final test set). Keeping the current model is a normal result.',
      how: ['Read why the loop cannot run on this project’s time-ordered test design.', 'See how a loop looks on a project with shuffled folds.', 'Try one change yourself here, and read its result.'],
      get: 'An honest answer, including “no change beyond noise” and “cannot be compared”.',
      next: ['model.html', 'Look at the chosen model', 'Pick a threshold and see the final test result.']
    },
    'model.html': {
      what: 'The model in use, the threshold that turns scores into yes/no, its one final test, its versions and its approval for scoring.',
      how: ['See how precision and recall change with the threshold.', 'Check the final test result (used once).', 'Open <b>Versions</b>: a retrained version waits for you.'],
      get: 'A model you can use for predictions, with its limits written down.',
      next: ['predictions.html', 'Score the new customer file', 'See who is likely to cancel next.']
    },
    'predictions.html': {
      what: 'Score a customer file, or let the model in use score the newest file every Monday. This screen lists the rows with the highest scores and what the model used for each.',
      how: ['Check when the list was last scored.', 'Read the top reasons: what the model used, not causes.', 'Download the CSV for your retention team.'],
      get: 'A ranked list of customers, with the inputs that moved each score.',
      next: ['monitoring.html', 'Check how the model is doing', 'See if the data or the results have changed.']
    },
    'monitoring.html': {
      what: 'Watch for changes in the incoming data and, once real outcomes arrive, in how good the predictions are.',
      how: ['Look at the two charts: drift and real precision.', 'Read what changed, in numbers.', 'Compare the retrained model with the model in use, then switch or keep.'],
      get: 'A clear choice: switch to the retrained model, or keep the model in use. Nothing switches by itself.',
      now: 'Drift on <code>plan_type</code> is above the alert line, real precision (0.48) is below its floor, and a retrained model waits for you.',
      next: ['history.html', 'See the History', 'Every decision in this project, in plain sentences.']
    },
    'history.html': {
      what: 'A plain log of every decision and event in the project: who, what, the evidence, and where a correction is allowed.',
      how: ['Read the log from the top.', 'Press <b>Correct</b> where it is allowed (only for decisions a person recorded).', 'Open <b>Lineage</b> to see how the pieces connect.'],
      get: 'Nothing is hidden and nothing is deleted.',
      next: ['settings.html', 'Look at the settings', 'Who can do what, and what it costs.']
    },
    'settings.html': {
      what: 'Members and what each role can do, usage and cost, integrations and notifications.',
      how: ['Check the people and what each role can do.', 'Open <b>Usage &amp; cost</b>.', 'Notifications and integrations work on the server; their screens are marked.'],
      get: 'Control over who can see and change what.',
      next: ['ai-settings.html', 'Look at the AI settings', 'What the assistant may do on its own.']
    },
    'ai-settings.html': {
      what: 'Turn the assistant beta on or off, see the AI model, spend and problem reports, and what the assistant may do. Today it may only suggest; acting on its own stays locked until earned.',
      how: ['Check the on/off switch.', 'Read the table, kind of decision by kind of decision.', 'See why every “Automatic” is locked: not tested yet.'],
      get: 'AI that is optional, limited and accountable.',
      next: ['connect.html', 'See how to connect', 'Use DCLab from code.']
    },
    'connect.html': {
      what: 'Use DCLab from your own code, from a terminal, or from a coding assistant, with access tokens you control.',
      how: ['Copy a snippet.', 'Create an access token with only what it needs.', 'Revoke it when done.'],
      get: 'The same project, reachable from code.',
      next: ['business.html', 'See what a client sees', 'The business view.']
    },
    'business.html': {
      what: 'What a client or business user sees: outcomes in plain words, this week’s list, and how the model was built.',
      how: ['Read the outcomes.', 'Download the latest list.', 'Answer the data team’s questions.'],
      get: 'A page you can share without explaining machine learning.',
      next: ['console.html', 'See the platform console', 'What DCLab staff see.']
    },
    'console.html': {
      what: 'The view DCLab staff use to keep the service healthy. It never shows customer rows.',
      how: ['Look at the overview and the workspaces.', 'Check failed jobs and their reasons.', 'Backups are not configured yet.'],
      get: 'Confidence that the service is looked after.',
      next: ['real.html', 'See what is real today', 'Which screens work now and which are planned.']
    },
    'real.html': {
      what: 'An honest list of every screen: what works today, what works partly, what is built but switched off, and what is planned.',
      how: ['Read the summary sentence.', 'Scan the status of each screen.', 'Read what is missing.'],
      get: 'No surprises about what you can use now.',
      next: ['index.html', 'Back to Home', 'Start the tour again.']
    }
  };

  // ---------------------------------------------------------------- explained terms
  var GLOSSARY = [
    ['cross-validation', /cross-validation/i, 'Training and checking the model several times on different slices of the training rows. In Northwind churn the slices follow time order.'],
    ['PR-AUC', /PR-AUC/, 'One number (0 to 1) for how well the model ranks churners above others. Good for rare outcomes.'],
    ['recall', /\brecall\b/i, 'Of all customers who really cancel, the share the model catches.'],
    ['precision', /\bprecision\b/i, 'Of all customers the model flags, the share who really cancel.'],
    ['threshold', /\bthreshold\b/i, 'The score above which a customer is flagged as “will cancel”.'],
    ['class weights', /class weights/i, 'Makes mistakes on the rare group (churners) count more while training. Each run tries balanced versions of some models when the rare group is small.'],
    ['baseline', /\bbaseline\b/i, 'A deliberately simple model. Real models must beat it to be worth using.'],
    ['leakage', /\bleakage\b|\bleak\b/i, 'A column that contains the answer, or is filled in only after the outcome. It makes results look better than they are.'],
    ['drift', /\bdrift\b/i, 'The incoming data no longer looks like the data the model learned from.'],
    ['PSI', /\bPSI\b/, 'Population Stability Index: how far a column’s distribution has moved. 0.10 is a watch, 0.20 is an alert.'],
    ['LightGBM', /LightGBM/, 'A fast tree-based model that works well on tables like this one.'],
    ['CatBoost', /CatBoost/, 'A tree-based model that handles category columns directly.'],
    ['fold', /\bfolds?\b/i, 'One slice of the cross-validation. In a time-ordered design each fold learns from earlier rows and is checked on the rows right after.'],
    ['trust checks', /trust checks?/i, '15 automatic checks inside each run: leakage, overfitting, duplicates, stability and more. Each one passes, warns, fails or was not checked.'],
    ['access token', /access token/i, 'A secret string that lets code use DCLab with limited rights. You can revoke it at any time.'],
    ['final test set', /final test set/i, 'The latest rows in time, kept aside. Each run scores it once, at the end, and it is never used to choose anything. A sealed try never scores it.'],
    ['noise', /\bnoise\b/i, 'How much a score moves from fold to fold by chance. A try must beat the current model by more than this margin to count.']
  ];

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (_) { return null; }
  }

  var head = content.querySelector('.page-head');
  var anchor = content.querySelector('.statusrow') || head;
  var guide = GUIDES[page];

  // ---------------------------------------------------------------- flow bar
  var idx = FLOW.map(function (n) { return n.page; }).indexOf(page);
  if (idx > -1 && head) {
    var bar = document.createElement('nav');
    bar.className = 'flowbar';
    bar.setAttribute('aria-label', 'Project steps');
    bar.innerHTML = '<div class="fb-title"><b>Northwind churn</b><small>catch ≥ 80% of churners · 8 steps</small></div><ol>' + FLOW.map(function (n, i) {
      return '<li class="fb-' + n.state + (i === idx ? ' fb-current' : '') + '"><a href="' + n.page + '"' + (i === idx ? ' aria-current="step"' : '') + '>' +
        '<span class="fb-k">' + n.label + '</span><span class="fb-v">' + n.value + '</span><span class="fb-s">' + n.note + '</span></a></li>';
    }).join('') + '</ol>';
    content.insertBefore(bar, head);
  }

  // ---------------------------------------------------------------- guide strip (collapsible)
  var toggle = document.querySelector('[data-guide-toggle]');
  if (guide && head) {
    var box = document.createElement('section');
    box.className = 'guide';
    box.setAttribute('aria-label', 'How to use this screen');
    box.innerHTML =
      '<div class="g-col"><h2 class="g-h">What this is</h2><p>' + guide.what + '</p>' + (guide.now ? '<p class="g-now"><span aria-hidden="true">●</span> ' + guide.now + '</p>' : '') + '</div>' +
      '<div class="g-col"><h2 class="g-h">What to do</h2><ol>' + guide.how.map(function (h) { return '<li>' + h + '</li>'; }).join('') + '</ol></div>' +
      '<div class="g-col"><h2 class="g-h">What you get</h2><p>' + guide.get + '</p></div>' +
      '<button class="g-close" type="button" aria-label="Hide the guide">Hide</button>';
    anchor.insertAdjacentElement('afterend', box);
    var setGuide = function (open) {
      box.hidden = !open;
      if (toggle) { toggle.setAttribute('aria-pressed', open ? 'true' : 'false'); toggle.textContent = open ? 'Hide guide' : 'Show guide'; }
      store('dclab-v8-guide', open ? 'open' : 'closed');
    };
    box.querySelector('.g-close').addEventListener('click', function () { setGuide(false); if (toggle) toggle.focus(); });
    if (toggle) toggle.addEventListener('click', function () { setGuide(box.hidden); });
    setGuide(store('dclab-v8-guide') !== 'closed');
  } else if (toggle) {
    toggle.hidden = true;
  }

  // ---------------------------------------------------------------- next step (the page's one forward button)
  if (guide && guide.next) {
    var next = document.createElement('a');
    next.className = 'next-step';
    next.href = guide.next[0];
    next.innerHTML = '<span class="ns-label">Next step</span><b>' + guide.next[1] + ' <span aria-hidden="true">→</span></b><small>' + guide.next[2] + '</small>';
    content.appendChild(next);
  }

  // ---------------------------------------------------------------- tooltips: first mention of a term per tab / screen
  var SKIP = 'a,button,code,pre,script,style,svg,textarea,input,select,option,h1,th,label,.term,.pill,.guide,.next-step,.tabs,.sect-tabs,.flowbar,.statusrow,.banner b';
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
    var seen = used.get(scope), text = node.nodeValue, hits = [];
    GLOSSARY.forEach(function (t, ti) {
      if (seen[ti]) return;
      var m = t[1].exec(text);
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
      span.setAttribute('data-tip', GLOSSARY[h.ti][0] + ' — ' + GLOSSARY[h.ti][2]);
      span.textContent = text.slice(h.start, h.end);
      frag.appendChild(span);
      seen[h.ti] = true;
      pos = h.end;
    });
    frag.appendChild(document.createTextNode(text.slice(pos)));
    node.parentNode.replaceChild(frag, node);
  });

  var tip = document.createElement('div');
  tip.className = 'tip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.appendChild(tip);
  function showTip(el) {
    tip.innerHTML = esc(el.getAttribute('data-tip')).replace(/^(.*?) — /, '<b>$1</b>');
    tip.hidden = false;
    var r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), window.innerWidth - w - 12) + 'px';
    tip.style.top = (r.top - h - 10 < 8 ? r.bottom + 10 : r.top - h - 10) + 'px';
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
          '<h2>Glossary</h2><p class="dialog-lead">The few technical words used in this demo. Dotted words on a screen show the same explanation when you point at them.</p>' +
          '<dl>' + GLOSSARY.map(function (t) { return '<div class="g-item"><dt>' + esc(t[0]) + '</dt><dd>' + esc(t[2]) + '</dd></div>'; }).join('') + '</dl>' +
          '<div class="dialog-actions"><button class="btn primary" value="close">Done</button></div></form>';
        d.addEventListener('click', function (e) { if (e.target === d) d.close(); });
        document.body.appendChild(d);
      }
      if (!d.open) d.showModal();
    });
  }
})();
