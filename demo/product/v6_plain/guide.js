// DCLab demo v6 "Plain" · guidance layer: flow bar, guide strip, term tooltips, glossary, next step.
(function () {
  'use strict';

  var page = document.body.getAttribute('data-page') || '';
  var content = document.querySelector('.content');
  if (!content) return;

  // ---------------------------------------------------------------- the six steps
  var FLOW = [
    { page: 'data.html', label: '1 Data', value: 'Jun 2026 file', note: '48,210 rows', state: 'done' },
    { page: 'goal.html', label: '2 Goal & test', value: 'Catch ≥ 80%', note: 'last 3 months kept back', state: 'done' },
    { page: 'experiments.html', label: '3 Experiments', value: '4 runs', note: 'Run 3 is best', state: 'done' },
    { page: 'model.html', label: '4 Model', value: 'LightGBM', note: 'threshold 0.31', state: 'done' },
    { page: 'predictions.html', label: '5 Predictions', value: '6,412 flagged', note: 'every Monday', state: 'done' },
    { page: 'monitoring.html', label: '6 Monitoring', value: 'Drift found', note: 'needs your decision', state: 'alert' }
  ];

  var GUIDES = {
    'data.html': {
      what: 'Look at the file before you train: how big it is, which columns will be used, and anything that could make results wrong.',
      how: ['Read the summary at the top.', 'Check the <b>Used?</b> column: each column left out has a reason.', 'Open <b>Data checks</b> for duplicates, missing values and leakage.'],
      get: 'A file you can trust, with every column decision visible.',
      next: ['goal.html', 'Set the goal and test design', 'Say what to predict and how it will be tested.']
    },
    'goal.html': {
      what: 'Say what to predict, how to rank models, and how the model will be tested so the result can be believed.',
      how: ['Check what we predict and the business rule.', 'Look at the time-line: where the model learns and where it is tested.', 'Change nothing if it looks right.'],
      get: 'A fixed test design, so every run is compared fairly.',
      next: ['experiments.html', 'Run the experiments', 'Compare a baseline with three models.']
    },
    'experiments.html': {
      what: 'Try several models on the same test design and see which one is best, using cross-validation only.',
      how: ['Read the runs table from top to bottom: each run changes one thing.', 'Look for <b>Best on cross-validation</b>.', 'Open <b>Trust checks</b> or <b>Compare two runs</b> for detail.'],
      get: 'One chosen run, with a reason you can explain.',
      next: ['model.html', 'Look at the chosen model', 'Pick a threshold and see the final test result.']
    },
    'model.html': {
      what: 'The chosen model, the threshold that turns scores into yes/no, and the one final test on the last 3 months.',
      how: ['Read the one-sentence summary.', 'See how precision and recall change with the threshold.', 'Check the final test result, then use the model.'],
      get: 'A model you can use for predictions, with its limits written down.',
      next: ['predictions.html', 'Score the new customer file', 'See who is likely to cancel next.']
    },
    'predictions.html': {
      what: 'Every Monday the model scores the newest customer file. This screen shows who is likely to cancel, and why.',
      how: ['Check when the list was last scored.', 'Read the top reasons for each customer.', 'Download the CSV for your retention team.'],
      get: 'A ranked list of customers with reasons you can act on.',
      next: ['monitoring.html', 'Check how the model is doing', 'See if the data or the results have changed.']
    },
    'monitoring.html': {
      what: 'Watch for changes in the incoming data and, once real outcomes arrive, in how good the predictions are.',
      how: ['Look at the two charts: drift and real precision.', 'Read what changed in plain words.', 'Compare the retrained model, then choose.'],
      get: 'A clear choice: switch to the retrained model, or keep the current one.',
      now: 'Drift on <code>plan_type</code> is above the alert line, and real precision has fallen to 0.48.'
    }
  };

  // ---------------------------------------------------------------- explained terms
  var GLOSSARY = [
    ['cross-validation', /cross-validation/i, 'Training and checking the model several times on different slices of the data. Here the slices follow time order.'],
    ['PR-AUC', /PR-AUC/, 'One number (0 to 1) for how well the model ranks churners above others. Good for rare outcomes.'],
    ['recall', /\brecall\b/i, 'Of all customers who really cancel, the share the model catches.'],
    ['precision', /\bprecision\b/i, 'Of all customers the model flags, the share who really cancel.'],
    ['threshold', /\bthreshold\b/i, 'The score above which a customer is flagged as “will cancel”.'],
    ['class weights', /class weights/i, 'Makes mistakes on the rare group (churners) count more while training.'],
    ['baseline', /\bbaseline\b/i, 'A deliberately simple model. Real models must beat it to be worth using.'],
    ['leakage', /\bleakage\b|\bleak\b/i, 'A column that contains the answer, or is filled in only after the outcome. It makes results look better than they are.'],
    ['drift', /\bdrift\b/i, 'The incoming data no longer looks like the data the model learned from.'],
    ['PSI', /\bPSI\b/, 'Population Stability Index: how far a column’s distribution has moved. 0.10 is a watch, 0.20 is an alert.'],
    ['LightGBM', /LightGBM/, 'A fast tree-based model that works well on tables like this one.'],
    ['CatBoost', /CatBoost/, 'A tree-based model that handles category columns directly.'],
    ['final test set', /final test set/i, 'The last 3 months, kept aside. It is looked at once, at the end, and never used to choose anything.']
  ];

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (_) { return null; }
  }

  var head = content.querySelector('.page-head');
  var guide = GUIDES[page];

  // ---------------------------------------------------------------- flow bar
  var idx = FLOW.map(function (n) { return n.page; }).indexOf(page);
  if (idx > -1 && head) {
    var bar = document.createElement('nav');
    bar.className = 'flowbar';
    bar.setAttribute('aria-label', 'Project steps');
    bar.innerHTML = '<div class="fb-title"><b>Northwind churn</b><small>catch ≥ 80% of churners · 6 steps</small></div><ol>' + FLOW.map(function (n, i) {
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
    head.insertAdjacentElement('afterend', box);
    var setGuide = function (open) {
      box.hidden = !open;
      if (toggle) { toggle.setAttribute('aria-pressed', open ? 'true' : 'false'); toggle.textContent = open ? 'Hide guide' : 'Show guide'; }
      store('dclab-v6-guide', open ? 'open' : 'closed');
    };
    box.querySelector('.g-close').addEventListener('click', function () { setGuide(false); if (toggle) toggle.focus(); });
    if (toggle) toggle.addEventListener('click', function () { setGuide(box.hidden); });
    setGuide(store('dclab-v6-guide') !== 'closed');
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
  var SKIP = 'a,button,code,pre,script,style,svg,textarea,input,select,option,h1,th,label,.term,.pill,.guide,.next-step,.tabs,.sect-tabs,.flowbar,.banner b';
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
