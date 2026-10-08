// DCLab demo v6 "Plain". Everything here is browser-local demo state; nothing calls a service.
(function () {
  'use strict';

  var page = document.body.getAttribute('data-page') || 'index.html';
  var KEY = 'dclab-demo-v6-plain';
  var state = read();
  var toastTimer;

  function read() {
    try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (_) { return {}; }
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (_) { /* storage can be blocked */ }
  }
  function esc(v) {
    return String(v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }
  function toast(message) {
    var t = document.getElementById('demo-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'demo-toast';
      t.className = 'demo-toast';
      t.setAttribute('role', 'status');
      t.setAttribute('aria-live', 'polite');
      document.body.appendChild(t);
    }
    t.textContent = message;
    t.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove('visible'); }, 3200);
  }
  window.dclabToast = toast;

  // ------------------------------------------------------------ sidebar
  var STEPS = [
    ['data.html', 'Data', '1'],
    ['goal.html', 'Goal & test design', '2'],
    ['experiments.html', 'Experiments', '3'],
    ['model.html', 'Model', '4'],
    ['predictions.html', 'Predictions', '5'],
    ['monitoring.html', 'Monitoring', '6']
  ];
  var nav = document.getElementById('nav');
  if (nav) {
    var h = '<a class="brand" href="index.html" aria-label="DCLab demo home"><span class="logo">DC</span><b>DCLab</b><span class="brand-sub">DEMO</span></a>' +
      '<div class="ws"><b>Northwind Telecom</b><small>sample workspace</small></div>' +
      '<div class="nav-group"><div class="nav-label">Start</div>' +
      '<a href="index.html"' + (page === 'index.html' ? ' aria-current="page"' : '') + '><span class="ic" aria-hidden="true">⌂</span><span>Start</span></a></div>' +
      '<div class="nav-group"><div class="nav-label">Northwind churn</div>';
    STEPS.forEach(function (s) {
      h += '<a href="' + s[0] + '"' + (page === s[0] ? ' aria-current="page"' : '') + '><span class="ic" aria-hidden="true">' + s[2] + '</span><span>' + esc(s[1]) + '</span></a>';
    });
    h += '</div><div class="about-note"><b>About this demo</b>All names and numbers are invented. Nothing here calls a service.</div>';
    nav.innerHTML = h;
  }

  // ------------------------------------------------------------ tabs
  document.querySelectorAll('[data-tabs]').forEach(function (bar) {
    var scope = bar.getAttribute('data-tabs');
    var buttons = Array.prototype.slice.call(bar.querySelectorAll('button[data-tab]'));
    bar.setAttribute('role', 'tablist');
    function activate(selected) {
      buttons.forEach(function (b) {
        var on = b === selected;
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.setAttribute('tabindex', on ? '0' : '-1');
        var panel = document.querySelector('[data-tabpanel="' + scope + ':' + b.getAttribute('data-tab') + '"]');
        if (panel) { panel.classList.toggle('on', on); panel.hidden = !on; }
      });
    }
    buttons.forEach(function (b, i) {
      b.setAttribute('role', 'tab');
      b.addEventListener('click', function () { activate(b); });
      b.addEventListener('keydown', function (e) {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        e.preventDefault();
        var n = buttons[(i + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length];
        n.focus(); activate(n);
      });
    });
    activate(buttons.filter(function (b) { return b.getAttribute('aria-selected') === 'true'; })[0] || buttons[0]);
  });

  // Section tabs inside a card: <div class="card sect"><section data-tab="Label">…</section></div>
  document.querySelectorAll('.card.sect').forEach(function (card, ci) {
    var sections = Array.prototype.slice.call(card.querySelectorAll(':scope > section[data-tab]'));
    if (!sections.length) return;
    var bar = document.createElement('div');
    bar.className = 'sect-tabs';
    bar.setAttribute('role', 'tablist');
    var buttons = sections.map(function (s, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.id = page + '-sect-' + ci + '-' + i;
      b.textContent = s.getAttribute('data-tab');
      s.setAttribute('role', 'tabpanel');
      s.setAttribute('aria-labelledby', b.id);
      bar.appendChild(b);
      return b;
    });
    function show(i) {
      buttons.forEach(function (b, j) {
        b.setAttribute('aria-selected', j === i ? 'true' : 'false');
        b.setAttribute('tabindex', j === i ? '0' : '-1');
        sections[j].hidden = j !== i;
      });
    }
    buttons.forEach(function (b, i) { b.addEventListener('click', function () { show(i); }); });
    var head = card.querySelector(':scope > .sect-head');
    if (head) head.insertAdjacentElement('afterend', bar); else card.insertAdjacentElement('afterbegin', bar);
    show(0);
  });

  // ------------------------------------------------------------ simple demo buttons
  var MOCK = {
    upload: 'Demo only: no file is uploaded. In the real product the file would be checked and profiled first.',
    download: 'A small sample CSV was saved. It contains invented data only.',
    edit: 'Demo only: editing is not wired up in this click-through.',
    other: 'Demo only: nothing was changed.'
  };
  function sampleCsv(name) {
    var csv = 'customer_id,churn_probability,flagged,reason_1,reason_2,reason_3\n' +
      'C-48112,0.91,yes,no login for 61 days,monthly fee rose 20%,contract ends this month\n' +
      'C-10377,0.87,yes,contract ends this month,3 support tickets in 90 days,no login for 45 days\n' +
      'C-22930,0.84,yes,3 support tickets in 90 days,2 late payments,monthly fee rose 20%\n';
    var url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    var a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  document.querySelectorAll('[data-mock]').forEach(function (b) {
    b.addEventListener('click', function (e) {
      e.preventDefault();
      var kind = b.getAttribute('data-mock');
      if (kind === 'download') sampleCsv('this_week_predictions_sample.csv');
      toast(MOCK[kind] || MOCK.other);
    });
  });
  document.querySelectorAll('input[type=file]').forEach(function (f) {
    f.addEventListener('change', function () {
      if (f.files && f.files[0]) toast('Selected ' + f.files[0].name + '. Demo only: the file stays in your browser and is not read.');
    });
  });

  // "Use this model for predictions" and the two monitoring decisions
  var use = document.querySelector('[data-use-model]');
  if (use) {
    function showUse() {
      use.textContent = 'In use for predictions ✓';
      use.disabled = true;
    }
    if (state.useModel) showUse();
    use.addEventListener('click', function () {
      state.useModel = true; save(); showUse();
      toast('Churn model (in use) will score the new customer file every Monday.');
    });
  }
  var result = document.getElementById('decision-result');
  if (result) {
    var historyEl = document.getElementById('history');
    function render() {
      var s = state.decision;
      var box = document.getElementById('decision-buttons');
      if (!s) return;
      if (box) box.hidden = true;
      result.hidden = false;
      result.className = 'banner ' + (s === 'switch' ? '' : 'warn');
      result.innerHTML = s === 'switch'
        ? '<div class="sp"><b>You switched to the retrained model.</b> Next Monday it scores the new customer file. The old model is kept, so you can switch back.</div><button class="btn sm" type="button" id="undo-decision">Undo</button>'
        : '<div class="sp"><b>You kept the current model.</b> Precision is still 0.48. We will remind you again next Monday.</div><button class="btn sm" type="button" id="undo-decision">Undo</button>';
      if (historyEl) {
        historyEl.innerHTML = '<li>' + (s === 'switch' ? 'You switched to the retrained model · today' : 'You kept the current model · today') + '</li>' +
          '<li>The model flagged a drop in precision to 0.48 · Mon</li>' +
          '<li>Retrained model was trained on data up to Sep 2026 · Mon</li>';
      }
      var undo = document.getElementById('undo-decision');
      if (undo) undo.addEventListener('click', function () {
        delete state.decision; save();
        result.hidden = true; if (box) box.hidden = false;
        if (historyEl) historyEl.innerHTML = historyDefault;
      });
    }
    var historyDefault = historyEl ? historyEl.innerHTML : '';
    document.querySelectorAll('[data-decide]').forEach(function (b) {
      b.addEventListener('click', function () {
        state.decision = b.getAttribute('data-decide'); save(); render();
        toast(state.decision === 'switch' ? 'Switched to the retrained model (demo).' : 'Kept the current model (demo).');
      });
    });
    render();
  }

  // ------------------------------------------------------------ AI suggestions
  var SUGGESTIONS = {
    'index.html': [],
    'data.html': [
      ['<code>customer_id</code> looks like an ID; leave it out', 'Every value is different, so it cannot help predict anything.'],
      ['<code>churned_at</code> is filled in only after the customer cancelled; leave it out', 'It would give away the answer.'],
      ['<code>contract_end</code> is empty for 38% of rows; fill with “no contract”', 'These customers simply have no fixed contract.']
    ],
    'goal.html': [
      ['Use time-ordered cross-validation', 'The churn rate rises over time, so each check should look forward in time.'],
      ['Rank models by PR-AUC, not accuracy', 'Only about 1 in 5 customers churn, so accuracy would look good even for a model that predicts “no” every time.']
    ],
    'experiments.html': [
      ['Try balanced class weights', 'Churners are 21% of rows, so the model may under-weight them.'],
      ['Try CatBoost next', 'Several columns are categories, which CatBoost handles directly.'],
      ['Look at fold 5 of Run 4', 'It scored lowest; the newest months may behave differently.']
    ],
    'model.html': [
      ['Keep threshold 0.31', 'It is the highest threshold that still catches at least 80% of churners.'],
      ['Mention plan types added after training in the known limits', 'The model has never seen them, so scores for those customers may be off.']
    ],
    'predictions.html': [
      ['Start with the 200 customers with the highest probability', 'This week’s list is long; the top of it is the most likely to cancel.'],
      ['Check the 412 flagged customers whose contract ends this month', 'They share the same top reason, so one offer could reach them all.']
    ],
    'monitoring.html': [
      ['Switch to the retrained model', 'Precision is 0.59 on the same test design, against 0.48 for the current model.'],
      ['Include <code>family_5g</code> customers in the next training data', 'The new plan type is 6% of customers and the current model has barely seen it.']
    ]
  };
  var shell = document.getElementById('shell');
  var panel = document.getElementById('ai-panel');
  var sw = document.getElementById('ai-switch');
  function renderPanel() {
    if (!panel) return;
    var list = SUGGESTIONS[page] || [];
    state.sug = state.sug || {};
    var h = '<h2>AI suggestions</h2><p class="ai-sub">Ideas for this screen. Nothing changes unless you press Accept.</p>';
    if (!list.length) h += '<div class="empty">Nothing to suggest on this screen.</div>';
    list.slice(0, 3).forEach(function (s, i) {
      var id = page + ':' + i, st = state.sug[id];
      h += '<div class="sug' + (st ? ' done ' + st : '') + '" data-sug="' + id + '"><b>' + s[0] + '</b><span class="why">' + esc(s[1]) + '</span>' +
        (st ? '<span class="state">' + (st === 'accepted' ? 'Accepted ✓' : 'Ignored') + '</span>'
          : '<span class="row"><button class="btn sm primary" type="button" data-act="accepted">Accept</button><button class="btn sm" type="button" data-act="ignored">Ignore</button></span>') + '</div>';
    });
    panel.innerHTML = h;
    panel.querySelectorAll('[data-act]').forEach(function (b) {
      b.addEventListener('click', function () {
        var id = b.closest('[data-sug]').getAttribute('data-sug');
        state.sug[id] = b.getAttribute('data-act'); save(); renderPanel();
        toast(b.getAttribute('data-act') === 'accepted' ? 'Accepted (demo): nothing else on the screen changes.' : 'Ignored.');
      });
    });
  }
  function setAi(on) {
    if (!shell || !sw) return;
    shell.classList.toggle('ai-on', on);
    panel.hidden = !on;
    sw.setAttribute('aria-checked', on ? 'true' : 'false');
    sw.querySelector('b').textContent = on ? 'on' : 'off';
    if (on) renderPanel();
  }
  if (sw) sw.addEventListener('click', function () {
    var on = sw.getAttribute('aria-checked') !== 'true';
    state.ai = on; save(); setAi(on);
  });
  setAi(false); // always starts off; it only turns on when you press the switch
  if (state.ai) { state.ai = false; save(); }

  // Column filter on the Data screen
  document.querySelectorAll('input[placeholder^="filter"]').forEach(function (input) {
    var rows = document.querySelectorAll('[data-tabpanel] tbody tr:not(.dim)');
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      rows.forEach(function (r) { r.hidden = !!q && r.textContent.toLowerCase().indexOf(q) === -1; });
    });
  });

  // ------------------------------------------------------------ compare two runs (Experiments)
  var cmp = document.querySelector('[data-compare]');
  if (cmp) {
    var RUNS = {
      '1': { name: 'Run 1 · Baseline', model: 'Always predicts “no”', folds: [0.21, 0.22, 0.22, 0.23, 0.22], mean: '0.22', std: '0.01', checks: '8 ✓ · 1 ⚠' },
      '2': { name: 'Run 2 · LightGBM', model: 'LightGBM', folds: [0.65, 0.67, 0.64, 0.68, 0.66], mean: '0.66', std: '0.02', checks: '7 ✓ · 2 ⚠' },
      '3': { name: 'Run 3 · LightGBM + balanced class weights', model: 'LightGBM, balanced class weights', folds: [0.69, 0.71, 0.68, 0.72, 0.70], mean: '0.70', std: '0.02', checks: '8 ✓ · 1 ⚠' },
      '4': { name: 'Run 4 · CatBoost', model: 'CatBoost', folds: [0.66, 0.70, 0.65, 0.71, 0.68], mean: '0.68', std: '0.03', checks: '7 ✓ · 2 ⚠' }
    };
    var a = cmp.querySelector('[data-cmp-a]'), b = cmp.querySelector('[data-cmp-b]'), out = cmp.querySelector('[data-cmp-out]');
    function draw() {
      var ra = RUNS[a.value], rb = RUNS[b.value];
      var rows = '<tr><td>Model</td><td>' + esc(ra.model) + '</td><td>' + esc(rb.model) + '</td></tr>';
      for (var i = 0; i < 5; i++) {
        var better = ra.folds[i] > rb.folds[i] ? 'a' : ra.folds[i] < rb.folds[i] ? 'b' : '';
        rows += '<tr><td>Fold ' + (i + 1) + ' PR-AUC</td><td class="r"' + (better === 'a' ? ' style="font-weight:700"' : '') + '>' + ra.folds[i].toFixed(2) + '</td><td class="r"' + (better === 'b' ? ' style="font-weight:700"' : '') + '>' + rb.folds[i].toFixed(2) + '</td></tr>';
      }
      rows += '<tr><td><b>Average ± std</b></td><td class="r"><b>' + ra.mean + ' ± ' + ra.std + '</b></td><td class="r"><b>' + rb.mean + ' ± ' + rb.std + '</b></td></tr>' +
        '<tr><td>Trust checks</td><td class="r">' + ra.checks + '</td><td class="r">' + rb.checks + '</td></tr>';
      var d = (parseFloat(rb.mean) - parseFloat(ra.mean)).toFixed(2);
      out.innerHTML = '<div class="tbl"><table><thead><tr><th></th><th class="r">' + esc(ra.name) + '</th><th class="r">' + esc(rb.name) + '</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
        '<p class="small muted">' + (a.value === b.value ? 'Pick two different runs to compare.' : 'Both runs use the same test design, so the folds are directly comparable. Difference in average PR-AUC: ' + (d > 0 ? '+' : '') + d + '.') + '</p>';
    }
    a.addEventListener('change', draw); b.addEventListener('change', draw); draw();
  }
})();
