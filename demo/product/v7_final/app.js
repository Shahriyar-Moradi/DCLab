// DCLab demo v7 "Final product". Everything here is browser-local demo state; nothing calls a service.
(function () {
  'use strict';

  var page = document.body.getAttribute('data-page') || 'index.html';
  var KEY = 'dclab-demo-v7-final';
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

  // ------------------------------------------------------------ roles and sidebar
  var ROLE_PAGES = {
    ds: ['index.html', 'inbox.html', 'projects.html', 'data.html', 'goal.html', 'experiments.html', 'improve.html', 'model.html', 'predictions.html', 'monitoring.html', 'history.html', 'connect.html', 'business.html', 'real.html'],
    admin: null, // everything
    biz: ['business.html', 'real.html']
  };
  var ROLE_NAMES = { ds: 'Data scientist', admin: 'Admin', biz: 'Business user' };
  function role() { return ROLE_NAMES[state.role] ? state.role : 'ds'; }
  function allowed(r, pg) { var l = ROLE_PAGES[r]; return !l || l.indexOf(pg) > -1; }

  function link(href, icon, label, extra) {
    return '<a href="' + href + '"' + (page === href ? ' aria-current="page"' : '') + '><span class="ic" aria-hidden="true">' + icon + '</span><span>' + esc(label) + '</span>' + (extra || '') + '</a>';
  }
  function renderNav() {
    var nav = document.getElementById('nav');
    if (!nav) return;
    var r = role();
    var h = '<a class="brand" href="index.html" aria-label="DCLab demo home"><span class="logo">DC</span><b>DCLab</b><span class="brand-sub">DEMO</span></a>' +
      '<div class="ws"><b>Northwind Telecom</b><small>sample workspace · ' + esc(ROLE_NAMES[r]) + '</small></div>';
    if (r === 'biz') {
      h += '<div class="nav-group"><div class="nav-label">Other views</div>' + link('business.html', '◧', 'Business view') + '</div>';
    } else {
      h += '<div class="nav-group"><div class="nav-label">Workspace</div>' + link('index.html', '⌂', 'Home') +
        link('inbox.html', '✉', 'Inbox', '<span class="cnt">3</span>') + link('projects.html', '▤', 'Projects') + '</div>' +
        '<div class="nav-group"><div class="nav-label">Project · Northwind churn</div>' +
        link('data.html', '1', 'Data') + link('goal.html', '2', 'Goal & test design') + link('experiments.html', '3', 'Experiments') +
        link('improve.html', '4', 'Improve', '<span class="tg">planned</span>') + link('model.html', '5', 'Model') + link('predictions.html', '6', 'Predictions') +
        link('monitoring.html', '7', 'Monitoring', '<span class="tg">planned</span>') + link('history.html', '8', 'History') + '</div>' +
        '<div class="nav-group"><div class="nav-label">Assistant</div><button class="nav-btn" type="button" id="asst-nav"><span class="ic" aria-hidden="true">✦</span><span>Open the assistant</span></button></div>' +
        '<div class="nav-group"><div class="nav-label">Admin</div>' +
        (r === 'admin' ? link('settings.html', '⚙', 'Settings') + link('ai-settings.html', '✦', 'AI settings') : '') +
        link('connect.html', '⇄', 'Connect (API, SDK, MCP)') + '</div>' +
        '<div class="nav-group"><div class="nav-label">Other views</div>' + link('business.html', '◧', 'Business view', '<span class="tg">planned</span>') +
        (r === 'admin' ? link('console.html', '▦', 'Platform console', '<span class="tg">staff</span>') : '') + '</div>';
    }
    h += '<div class="nav-group">' + link('real.html', '✓', 'What’s real today') + '</div>' +
      '<div class="about-note"><b>About this demo</b>All names and numbers are invented. Nothing here calls a service.</div>';
    nav.innerHTML = h;
    var nb = document.getElementById('asst-nav');
    if (nb) nb.addEventListener('click', function () { setAsst(true); });
  }
  renderNav();

  var roleSel = document.getElementById('role-switch');
  if (roleSel) {
    roleSel.value = role();
    roleSel.addEventListener('change', function () {
      state.role = roleSel.value; save();
      if (!allowed(role(), page)) { location.href = role() === 'biz' ? 'business.html' : 'index.html'; return; }
      location.reload();
    });
  }
  // A page the current role cannot open
  if (!allowed(role(), page)) {
    document.body.setAttribute('data-gated', '1');
    var c = document.querySelector('.content');
    var adminOnly = ROLE_PAGES.ds.indexOf(page) === -1;
    c.innerHTML = '<div class="card gate"><h2>This screen is not available for the ' + esc(ROLE_NAMES[role()]) + ' role</h2>' +
      '<p class="muted">' + (role() === 'biz' ? 'A business user sees only the Business view: plain outcomes and this week’s list.' : (adminOnly ? 'Only an admin can open this screen.' : 'This screen is not part of this role.')) + '</p>' +
      '<div class="toolbar"><button class="btn primary" type="button" data-set-role="' + (role() === 'biz' ? 'biz' : 'admin') + '">' + (role() === 'biz' ? 'Go to the Business view' : 'View as Admin') + '</button><button class="btn" type="button" data-set-role="ds">View as Data scientist</button></div></div>';
    c.querySelectorAll('[data-set-role]').forEach(function (b) {
      b.addEventListener('click', function () {
        var want = b.getAttribute('data-set-role');
        if (role() === 'biz' && want === 'biz') { location.href = 'business.html'; return; }
        state.role = want; save(); location.reload();
      });
    });
  }
  if (role() === 'biz') {
    ['asst-toggle', 'ai-switch'].forEach(function (id) { var e = document.getElementById(id); if (e) e.hidden = true; });
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
    undo: 'Undone (demo). A new entry was added to the History; nothing is deleted.',
    revoke: 'Access token revoked (demo).',
    create: 'Demo only: nothing was created.',
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
    'improve.html': [
      ['Try class weights between 1:1 and balanced', 'The calibration warning on Run 3 suggests balanced weights may push scores too high.'],
      ['Stop after 4 tries if nothing beats the noise', 'The spread between folds is ±0.02, so smaller gains cannot be told apart from luck.']
    ],
    'inbox.html': [
      ['Approve the retrained model', 'Precision goes from 0.48 to 0.59 on the same test design, and the rules agree.']
    ],
    'monitoring.html': [
      ['Switch to the retrained model', 'Precision is 0.59 on the same test design, against 0.48 for the current model.'],
      ['Include <code>family_5g</code> customers in the next training data', 'The new plan type is 6% of customers and the current model has barely seen it.']
    ]
  };
  var shell = document.getElementById('shell');
  var panel = document.getElementById('ai-panel');
  var sw = document.getElementById('ai-switch');
  // ------------------------------------------------------------ assistant chat panel
  var asstPanel = document.getElementById('asst-panel');
  var asstBtn = document.getElementById('asst-toggle');
  var chatLog = [];
  var cost = 0.19;
  function botCard(id, text, accepted) {
    return '<div class="sug' + (accepted ? ' done accepted' : '') + '" data-card="' + id + '"><b>' + text + '</b>' +
      (accepted ? '<span class="state">Accepted ✓ (demo)</span>' : '<span class="row"><button class="btn sm primary" type="button" data-card-act="ok">Accept</button><button class="btn sm" type="button" data-card-act="no">Ignore</button></span>') + '</div>';
  }
  function firstReply() {
    return '<div class="msg bot"><div>I looked at the Jun 2026 file and set this up as a draft. Nothing is saved until you accept each suggestion below.</div>' +
      '<ol><li>Goal: predict <code>churned_next_30d</code>, rank by PR-AUC, with the rule <b>recall ≥ 0.80</b>.</li><li>Test design: time-ordered, 5 folds, last 3 months kept as the final test set.</li><li>Runs: a baseline, LightGBM, LightGBM with class weights, CatBoost.</li></ol>' +
      botCard('goal', 'Set the goal: catch at least 80% of churners') + botCard('test', 'Use the time-ordered test design') +
      '<div class="steps-ran">Steps I ran: read the column names and summary statistics · checked for leakage · compared churn rate by month. Raw rows were not sent anywhere.</div></div>';
  }
  var CANNED = {
    'Why is precision down?': 'Precision fell from 0.63 to 0.48 after the new plan type <code>family_5g</code> appeared (6% of customers, drift 0.22). Recall held at 0.80. The retrained model gets 0.59 on the same test design. I can only suggest the switch; you decide in the Inbox.',
    'What did Improve find?': 'Four tries, best cross-validation precision 0.65 against 0.63 now. The spread between folds is ±0.02, so that is not beyond noise. The current model is kept and the final test set was not used.',
    'Explain the calibration warning': 'Balanced class weights push scores up, so a score of 0.3 is not a 30% chance. Ranking and the 0.31 threshold are still fine. If you need real chances, a calibration step can be added in a new run.'
  };
  function renderAsst() {
    if (!asstPanel) return;
    if (!chatLog.length) chatLog = [{ me: 'Predict who will churn next month; catch 80%' }, { html: firstReply() }];
    var h = '<div class="asst-head"><h2>Assistant</h2><span class="pill off">Built, switched off</span><button class="asst-x" type="button" id="asst-close" aria-label="Close the assistant">×</button></div>' +
      '<div class="asst-note">Shown to users after testing. <b>Works with AI off:</b> every action here is also a button on its screen. Anything that changes the project appears as a suggestion that needs your OK.</div>' +
      '<div class="chat" id="chat" aria-live="polite">';
    chatLog.forEach(function (m) { h += m.me ? '<div class="msg me">' + esc(m.me) + '</div>' : (m.html || '<div class="msg bot">' + m.text + '</div>'); });
    h += '</div><div class="chat-foot"><div class="chat-chips">' + Object.keys(CANNED).map(function (k) { return '<button type="button" data-q="' + esc(k) + '">' + esc(k) + '</button>'; }).join('') + '</div>' +
      '<form class="chat-form" id="chat-form"><input id="chat-input" type="text" placeholder="Ask about this project…" aria-label="Message the assistant" autocomplete="off"><button class="btn primary sm" type="submit">Send</button></form>' +
      '<div class="cost"><span>Cost of this conversation: <b>$' + cost.toFixed(2) + '</b></span><span>Monthly AI budget: $25</span></div></div>';
    asstPanel.innerHTML = h;
    var box = document.getElementById('chat');
    if (box) box.scrollTop = box.scrollHeight;
    document.getElementById('asst-close').addEventListener('click', function () { setAsst(false); });
    document.getElementById('chat-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var inp = document.getElementById('chat-input'), v = inp.value.trim();
      if (v) ask(v);
    });
    asstPanel.querySelectorAll('[data-q]').forEach(function (b) { b.addEventListener('click', function () { ask(b.getAttribute('data-q')); }); });
    asstPanel.querySelectorAll('[data-card-act]').forEach(function (b) {
      b.addEventListener('click', function () {
        var card = b.closest('[data-card]');
        if (b.getAttribute('data-card-act') === 'ok') { card.className = 'sug done accepted'; card.querySelector('.row').outerHTML = '<span class="state">Accepted ✓ (demo)</span>'; toast('Accepted (demo): nothing else on the screen changes.'); }
        else { card.className = 'sug done'; card.querySelector('.row').outerHTML = '<span class="state">Ignored</span>'; }
      });
    });
  }
  function ask(q) {
    chatLog.push({ me: q });
    cost += 0.02;
    chatLog.push({ text: CANNED[q] || 'Demo only: this is a scripted conversation. In the real product I would answer from this project’s data, show the steps I ran, and put any change in front of you as a suggestion.' });
    renderAsst();
    var inp = document.getElementById('chat-input'); if (inp) inp.focus();
  }
  function setAsst(on) {
    if (!shell || !asstPanel) return;
    if (on) {
      if (sw && sw.getAttribute('aria-checked') === 'true') { sw.click(); }
      renderAsst();
    }
    shell.classList.toggle('asst-on', on);
    asstPanel.hidden = !on;
    if (asstBtn) asstBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
  if (asstBtn) asstBtn.addEventListener('click', function () { setAsst(asstPanel.hidden); });

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
    if (on) setAsst(false);
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
    var scope = input.closest('[data-tabpanel]') || document;
    var rows = scope.querySelectorAll('tbody tr:not(.dim)');
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      rows.forEach(function (r) { r.hidden = !!q && r.textContent.toLowerCase().indexOf(q) === -1; });
    });
  });

  // ------------------------------------------------------------ compare two runs (Experiments)
  var cmp = document.querySelector('[data-compare]');
  if (cmp) {
    var RUNS = {
      '1': { name: 'Run 1 · Baseline', model: 'Always predicts “no”', folds: [0.21, 0.22, 0.22, 0.23, 0.22], mean: '0.22', std: '0.01', checks: '14 ✓ · 1 ⚠' },
      '2': { name: 'Run 2 · LightGBM', model: 'LightGBM', folds: [0.65, 0.67, 0.64, 0.68, 0.66], mean: '0.66', std: '0.02', checks: '13 ✓ · 2 ⚠' },
      '3': { name: 'Run 3 · LightGBM + balanced class weights', model: 'LightGBM, balanced class weights', folds: [0.69, 0.71, 0.68, 0.72, 0.70], mean: '0.70', std: '0.02', checks: '14 ✓ · 1 ⚠' },
      '4': { name: 'Run 4 · CatBoost', model: 'CatBoost', folds: [0.66, 0.70, 0.65, 0.71, 0.68], mean: '0.68', std: '0.03', checks: '13 ✓ · 2 ⚠' },
      '5': { name: 'Run 5 · Run 3 with more trees', model: 'LightGBM, balanced class weights, 500 trees', folds: [0.69, 0.71, 0.69, 0.72, 0.69], mean: '0.70', std: '0.02', checks: '14 ✓ · 1 ⚠' }
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

  // ------------------------------------------------------------ v7 extras
  // Undo / revoke buttons mark themselves
  document.querySelectorAll('[data-mock="undo"],[data-mock="revoke"]').forEach(function (b) {
    b.addEventListener('click', function () {
      var undo = b.getAttribute('data-mock') === 'undo';
      b.textContent = undo ? 'Undone ✓' : 'Revoked ✓'; b.disabled = true;
    });
  });
  // Inbox: approve / reject the retrained model (same decision as on Monitoring)
  document.querySelectorAll('[data-inbox]').forEach(function (b) {
    var box = b.closest('.item');
    var out = box && box.querySelector('[data-inbox-result]');
    function show() {
      var d = state.decision;
      if (!d || !out) return;
      var bt = box.querySelector('[data-inbox-buttons]'); if (bt) bt.hidden = true;
      out.hidden = false;
      out.innerHTML = d === 'switch' ? '<b>You approved the retrained model.</b> It scores the new customer file next Monday. The old model is kept, so you can switch back.' : '<b>You rejected the retrained model.</b> The current model stays in use.';
    }
    b.addEventListener('click', function () {
      state.decision = b.getAttribute('data-inbox') === 'approve' ? 'switch' : 'keep'; save(); show();
      toast(state.decision === 'switch' ? 'Approved (demo).' : 'Rejected (demo).');
    });
    show();
  });
  // Acknowledge-style buttons that only mark the item
  document.querySelectorAll('[data-ack]').forEach(function (b) {
    b.addEventListener('click', function () {
      var box = b.closest('.item');
      box.querySelector('[data-ack-buttons]').hidden = true;
      var r = box.querySelector('[data-ack-result]'); r.hidden = false;
      toast('Marked (demo).');
    });
  });
  // History: show the Monitoring decision at the top once it is made
  var hl = document.querySelector('[data-hist-top]');
  if (hl && state.decision) {
    hl.innerHTML = '<tr><td>today</td><td><span class="who-pill you">You</span></td><td>' + (state.decision === 'switch' ? 'Approved the retrained model for scoring' : 'Kept the current model') + '</td><td>Precision 0.48 → 0.59 on the same test design</td><td><button class="btn sm" type="button" data-mock="undo">Undo</button></td></tr>';
    hl.querySelectorAll('[data-mock]').forEach(function (b) { b.addEventListener('click', function () { delete state.decision; save(); toast(MOCK.undo); b.textContent = 'Undone ✓'; b.disabled = true; }); });
  }
  // Switches (AI settings, notifications)
  document.querySelectorAll('[data-toggle]').forEach(function (t) {
    t.addEventListener('click', function () {
      var on = t.getAttribute('aria-checked') !== 'true';
      t.setAttribute('aria-checked', on ? 'true' : 'false');
      var b = t.querySelector('b'); if (b) b.textContent = on ? 'on' : 'off';
      toast('Demo only: the setting is not saved.');
    });
  });
  document.querySelectorAll('select[data-demo-select]').forEach(function (sel) {
    sel.addEventListener('change', function () { toast('Demo only: the setting is not saved.'); });
  });
  // Thresholds on Monitoring
  document.querySelectorAll('[data-save-thresholds]').forEach(function (b) {
    b.addEventListener('click', function () { toast('Thresholds updated (demo). The chart lines do not move in this click-through.'); });
  });
  // New project wizard
  var wizBtn = document.querySelector('[data-wizard]');
  if (wizBtn) {
    wizBtn.addEventListener('click', function () {
      var d = document.getElementById('wiz-dialog');
      if (!d) {
        d = document.createElement('dialog');
        d.id = 'wiz-dialog';
        d.className = 'prototype-dialog';
        document.body.appendChild(d);
        d.addEventListener('click', function (e) { if (e.target === d) d.close(); });
      }
      var step = 0;
      var STEPS_W = [
        ['Name and data', '<label class="field">Project name<input type="text" value="Plan upgrade" aria-label="Project name"></label><label class="upload-box"><b>Choose a file (CSV)</b><span class="small muted">Demo only: the file is not read.</span><input type="file" accept=".csv"></label>'],
        ['What to predict', '<label class="field">Column to predict<select aria-label="Column to predict"><option>upgraded_next_30d</option><option>(choose after the file is read)</option></select></label><label class="field">Rule for success<input type="text" value="precision ≥ 0.50 while recall ≥ 0.60" aria-label="Rule for success"></label><p class="small muted">The assistant can suggest these when it is switched on. Without it you choose them here.</p>'],
        ['Test design', '<p>Time-ordered cross-validation, 5 folds, with the last 3 months kept as the final test set (used once).</p><p class="small muted">Suggested by the rules because the data has dates. You confirm it before any run starts.</p>']
      ];
      function draw() {
        d.innerHTML = '<form method="dialog" class="dialog-card"><button class="dialog-x" value="close" aria-label="Close">×</button><h2>New project</h2>' +
          '<div class="wiz-steps">' + STEPS_W.map(function (x, i) { return '<span class="' + (i === step ? 'on' : '') + '">' + (i + 1) + ' · ' + x[0] + '</span>'; }).join('') + '</div>' +
          '<div>' + STEPS_W[step][1] + '</div><div class="dialog-actions">' +
          (step > 0 ? '<button class="btn" type="button" id="wiz-back">Back</button>' : '') +
          (step < 2 ? '<button class="btn primary" type="button" id="wiz-next">Next</button>' : '<button class="btn primary" value="close" id="wiz-done">Create project (demo)</button>') + '</div></form>';
        var n = d.querySelector('#wiz-next'), bk = d.querySelector('#wiz-back'), dn = d.querySelector('#wiz-done');
        if (n) n.addEventListener('click', function () { step++; draw(); });
        if (bk) bk.addEventListener('click', function () { step--; draw(); });
        if (dn) dn.addEventListener('click', function () { toast('Demo only: no project was created.'); });
      }
      draw();
      if (!d.open) d.showModal();
    });
  }
})();
