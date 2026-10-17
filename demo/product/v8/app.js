// DCLab demo v8 "Real product". Everything here is browser-local demo state; nothing calls a service.
(function () {
  'use strict';

  var page = document.body.getAttribute('data-page') || 'index.html';
  var KEY = 'dclab-demo-v8';
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
    toastTimer = setTimeout(function () { t.classList.remove('visible'); }, 3600);
  }
  window.dclabToast = toast;

  // ------------------------------------------------------------ "View as": an admin-only preview, not a role switcher
  // The signed-in person (Sam) is an Admin. "View as" previews what another role's sidebar and screens look like,
  // in this browser tab only; rights and requests stay the admin's. There is no role switcher in the product.
  var ROLE_PAGES = {
    admin: null, // everything
    ds: ['index.html', 'inbox.html', 'projects.html', 'data.html', 'goal.html', 'experiments.html', 'improve.html', 'model.html', 'predictions.html', 'monitoring.html', 'history.html', 'connect.html', 'business.html', 'real.html'],
    analyst: ['index.html', 'inbox.html', 'projects.html', 'data.html', 'goal.html', 'experiments.html', 'improve.html', 'model.html', 'predictions.html', 'monitoring.html', 'history.html', 'connect.html', 'business.html', 'real.html'],
    biz: ['business.html']
  };
  var ROLE_NAMES = { admin: 'Admin', ds: 'Data scientist', analyst: 'Analyst (view only)', biz: 'Business user' };
  function role() { return ROLE_NAMES[state.role] ? state.role : 'admin'; }
  function previewing() { return role() !== 'admin'; }
  function allowed(r, pg) { var l = ROLE_PAGES[r]; return !l || l.indexOf(pg) > -1; }

  function link(href, icon, label, extra) {
    return '<a href="' + href + '"' + (page === href ? ' aria-current="page"' : '') + '><span class="ic" aria-hidden="true">' + icon + '</span><span>' + esc(label) + '</span>' + (extra || '') + '</a>';
  }
  function renderNav() {
    var nav = document.getElementById('nav');
    if (!nav) return;
    var r = role();
    var h = '<a class="brand" href="index.html" aria-label="DCLab demo home"><span class="logo">DC</span><b>DCLab</b><span class="brand-sub">DEMO</span></a>' +
      '<div class="ws"><b>Northwind Telecom</b><small>sample workspace · ' + (previewing() ? 'preview: ' + esc(ROLE_NAMES[r]) : 'Sam · Admin') + '</small></div>';
    if (r === 'biz') {
      h += '<div class="nav-group"><div class="nav-label">Other views</div>' + link('business.html', '◧', 'Business view') + '</div>';
    } else {
      h += '<div class="nav-group"><div class="nav-label">Workspace</div>' + link('index.html', '⌂', 'Home') +
        link('inbox.html', '✉', 'Inbox', '<span class="cnt">3</span>') + link('projects.html', '▤', 'Projects') + '</div>' +
        '<div class="nav-group"><div class="nav-label">Project · Northwind churn</div>' +
        link('data.html', '1', 'Data') + link('goal.html', '2', 'Goal & test design') + link('experiments.html', '3', 'Experiments') +
        link('improve.html', '4', 'Improve') + link('model.html', '5', 'Model') + link('predictions.html', '6', 'Predictions') +
        link('monitoring.html', '7', 'Monitoring', '<span class="cnt">!</span>') + link('history.html', '8', 'History') + '</div>' +
        '<div class="nav-group"><div class="nav-label">Assistant</div><button class="nav-btn" type="button" id="asst-nav"><span class="ic" aria-hidden="true">✦</span><span>Open the assistant</span><span class="tg">beta</span></button></div>' +
        '<div class="nav-group"><div class="nav-label">Admin</div>' +
        (r === 'admin' ? link('settings.html', '⚙', 'Settings') + link('ai-settings.html', '✦', 'AI settings', '<span class="tg">planned</span>') : '') +
        link('connect.html', '⇄', 'Connect (API, SDK, MCP)') + '</div>' +
        '<div class="nav-group"><div class="nav-label">Other views</div>' + link('business.html', '◧', 'Business view') +
        (r === 'admin' ? link('console.html', '▦', 'Platform console', '<span class="tg">staff</span>') : '') + '</div>' +
        '<div class="nav-group"><div class="nav-label">Help</div>' + link('real.html', '✓', 'What’s real today') + '</div>';
    }
    h += '<div class="about-note"><b>About this demo</b>All names and numbers are invented. Nothing here calls a service.</div>';
    nav.innerHTML = h;
    var nb = document.getElementById('asst-nav');
    if (nb) nb.addEventListener('click', function () { setAsst(true); });
  }
  renderNav();

  function setRole(r) { if (r === 'admin') delete state.role; else state.role = r; save(); }
  var roleSel = document.getElementById('role-switch');
  if (roleSel) {
    roleSel.value = role();
    roleSel.addEventListener('change', function () {
      setRole(roleSel.value);
      if (!allowed(role(), page)) { location.href = role() === 'biz' ? 'business.html' : 'index.html'; return; }
      location.reload();
    });
  }
  var content = document.querySelector('.content');
  if (previewing() && content) {
    var vb = document.createElement('section');
    vb.className = 'view-as-banner';
    vb.setAttribute('aria-label', 'Preview of another role');
    vb.innerHTML = '<span class="sp"><b>Preview: ' + esc(ROLE_NAMES[role()]) + '.</b> You still have admin rights; nothing is changed. Only this browser tab shows the preview.</span><button class="btn sm" type="button" id="stop-preview">Stop the preview</button>';
    content.insertBefore(vb, content.firstChild);
    document.getElementById('stop-preview').addEventListener('click', function () { setRole('admin'); location.reload(); });
  }
  // A page the previewed role cannot open
  if (!allowed(role(), page) && content) {
    document.body.setAttribute('data-gated', '1');
    var keep = content.querySelector('.view-as-banner');
    content.innerHTML = '<div class="card gate"><h2>' + esc(ROLE_NAMES[role()]) + ' cannot open this screen</h2>' +
      '<p class="muted">' + (role() === 'biz' ? 'A business user sees only the Business view: plain outcomes and the latest list.' : 'Only an admin can open this screen.') + '</p>' +
      '<div class="toolbar">' + (role() === 'biz' ? '<a class="btn primary" href="business.html">Go to the Business view</a>' : '') + '<button class="btn" type="button" data-stop>Stop the preview</button></div></div>';
    if (keep) content.insertBefore(keep, content.firstChild);
    content.querySelectorAll('[data-stop]').forEach(function (b) { b.addEventListener('click', function () { setRole('admin'); location.reload(); }); });
  }
  if (role() === 'biz') {
    ['asst-toggle', 'ai-switch'].forEach(function (id) { var e = document.getElementById(id); if (e) e.hidden = true; });
  }
  if (role() === 'analyst') {
    // View only: buttons that change something say so instead of acting.
    document.querySelectorAll('.content .btn.primary, [data-decide], [data-inbox], [data-loop-accept], [data-save-thresholds]').forEach(function (b) {
      if (b.closest('.view-as-banner')) return;
      b.setAttribute('aria-disabled', 'true');
      b.title = 'Analyst (view only) can read this screen but not change anything.';
    });
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


  if (role() === 'analyst') {
    document.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[aria-disabled="true"]');
      if (!b) return;
      e.preventDefault(); e.stopImmediatePropagation();
      toast('Analyst (view only): this role can read the screen but not change anything.');
    }, true);
  }

  // ------------------------------------------------------------ simple demo buttons
  var MOCK = {
    upload: 'Demo only: no file is uploaded. In the product the file is checked on upload (duplicates, missing values, dates, ID-like and constant columns) and listed in the upload log, accepted or rejected.',
    download: 'A small sample CSV was saved. It contains invented data only.',
    edit: 'Demo only: nothing is changed in this click-through.',
    record: 'Recorded as a choice in History (demo). Scoring keeps the locked threshold 0.31; applying a new one needs a new model version.',
    revoke: 'Access token revoked (demo).',
    create: 'Demo only: nothing was created.',
    other: 'Demo only: nothing was changed.'
  };
  function sampleCsv(name) {
    var csv = 'customer_id,score,flagged,reason_1,reason_2,reason_3\n' +
      '"C-48112",0.91,yes,"Days since last login: high (top 5%), raised the score compared with the model\'s average row","Fee change in 3 months: high (top 20%), raised the score compared with the model\'s average row","Days left on contract: low (bottom 10%), raised the score compared with the model\'s average row"\n' +
      '"C-10377",0.87,yes,"Days left on contract: low (bottom 10%), raised the score compared with the model\'s average row","Support tickets in 90 days: high (top 10%), raised the score compared with the model\'s average row","Days since last login: high (top 20%), raised the score compared with the model\'s average row"\n';
    var url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    var a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  document.querySelectorAll('[data-mock]').forEach(function (b) {
    b.addEventListener('click', function (e) {
      e.preventDefault();
      var kind = b.getAttribute('data-mock');
      if (kind === 'download') sampleCsv('newest_list_sample.csv');
      if (kind === 'revoke') { b.textContent = 'Revoked ✓'; b.disabled = true; }
      toast(MOCK[kind] || MOCK.other);
    });
  });
  document.querySelectorAll('input[type=file]').forEach(function (f) {
    f.addEventListener('change', function () {
      if (f.files && f.files[0]) toast('Selected ' + f.files[0].name + '. Demo only: the file stays in your browser and is not read.');
    });
  });

  // ------------------------------------------------------------ switch or keep the retrained model (Monitoring, Inbox, History)
  var SWITCH_TEXT = '<b>You switched to the retrained model (v5).</b> The model in use, the data, the test design and the features moved together in one History entry. The next weekly scoring uses v5. Version 1 stays saved; going back is a new approval on Model › Approved for scoring.';
  var KEEP_TEXT = '<b>You kept the model in use (v1).</b> Your reason is saved in History. The retrained model stays saved; nothing switches by itself.';
  function resetLink() { return ' <button class="btn sm" type="button" data-reset-decision>Reset the demo</button>'; }
  var result = document.getElementById('decision-result');
  if (result) {
    var historyEl = document.getElementById('history');
    var historyDefault = historyEl ? historyEl.innerHTML : '';
    function render() {
      var s = state.decision;
      var box = document.getElementById('decision-buttons');
      if (!s) { result.hidden = true; if (box) box.hidden = false; if (historyEl) historyEl.innerHTML = historyDefault; return; }
      if (box) box.hidden = true;
      result.hidden = false;
      result.className = 'banner ' + (s === 'switch' ? '' : 'warn');
      result.innerHTML = '<div class="sp">' + (s === 'switch' ? SWITCH_TEXT : KEEP_TEXT) + '</div>' + resetLink();
      if (historyEl) historyEl.innerHTML = '<li>' + (s === 'switch' ? 'You switched to the retrained model (v5) · today' : 'You kept the model in use (v1) · today') + '</li>' + historyDefault;
      result.querySelector('[data-reset-decision]').addEventListener('click', function () { delete state.decision; save(); render(); });
    }
    document.querySelectorAll('[data-decide]').forEach(function (b) {
      b.addEventListener('click', function () {
        state.decision = b.getAttribute('data-decide'); save(); render();
        toast(state.decision === 'switch' ? 'Switched (demo). In the product this is the existing approval, with your reason.' : 'Kept (demo). In the product this rejects the switch, with your reason.');
      });
    });
    render();
  }
  document.querySelectorAll('[data-inbox]').forEach(function (b) {
    var box = b.closest('.item');
    var out = box && box.querySelector('[data-inbox-result]');
    function show() {
      var d = state.decision;
      var bt = box.querySelector('[data-inbox-buttons]');
      if (!d || !out) { if (out) out.hidden = true; if (bt) bt.hidden = false; return; }
      if (bt) bt.hidden = true;
      out.hidden = false;
      out.innerHTML = (d === 'switch' ? SWITCH_TEXT : KEEP_TEXT) + resetLink();
      out.querySelector('[data-reset-decision]').addEventListener('click', function () { delete state.decision; save(); show(); });
    }
    b.addEventListener('click', function () {
      state.decision = b.getAttribute('data-inbox') === 'approve' ? 'switch' : 'keep'; save(); show();
      toast(state.decision === 'switch' ? 'Switched (demo).' : 'Kept (demo).');
    });
    show();
  });
  var hl = document.querySelector('[data-hist-top]');
  if (hl && state.decision) {
    hl.innerHTML = '<tr><td>today</td><td><span class="who-pill you">You</span></td><td>' + (state.decision === 'switch' ? 'Switched to the retrained model (v5) for scoring' : 'Kept the model in use (v1)') + '</td><td>Same kind of test, latest months: precision 0.59 (v5) and 0.52 (v1)</td><td>' + (state.decision === 'switch' ? 'Change it again on Model › Approved for scoring' : '—') + '</td></tr>';
  }

  // ------------------------------------------------------------ Improve: accept a loop try (a person only) or keep
  var loopOut = document.querySelector('[data-loop-result]');
  if (loopOut) {
    var loopBtns = document.querySelector('[data-loop-buttons]');
    function showLoop() {
      var s = state.loop;
      if (!s) { loopOut.hidden = true; if (loopBtns) loopBtns.hidden = false; return; }
      if (loopBtns) loopBtns.hidden = true;
      loopOut.hidden = false;
      loopOut.className = 'banner' + (s === 'keep' ? ' warn' : '');
      loopOut.innerHTML = '<div class="sp">' + (s === 'accept'
        ? '<b>You accepted Try 2.</b> Its choices were pinned and it was re-run once as the final run. <b>Final test (one look): recall 0.71 while precision is at least 0.60</b>, on 2,412 rows. That is the one final-test number of this loop; it is evidence, not a choice: the model in use changes only when someone approves it on Model › Approved for scoring.'
        : '<b>You kept the current model.</b> The final test set was not used. Nothing is recorded in the model in use.') + '</div><button class="btn sm" type="button" data-reset-loop>Reset the demo</button>';
      loopOut.querySelector('[data-reset-loop]').addEventListener('click', function () { delete state.loop; save(); showLoop(); });
    }
    document.querySelectorAll('[data-loop-accept],[data-loop-keep]').forEach(function (b) {
      b.addEventListener('click', function () {
        state.loop = b.hasAttribute('data-loop-accept') ? 'accept' : 'keep'; save(); showLoop();
        toast(state.loop === 'accept' ? 'Accepted (demo): one final run, one final-test number.' : 'Kept (demo): the final test set was not used.');
      });
    });
    showLoop();
  }

  // ------------------------------------------------------------ AI suggestions (planned: lists existing suggestions, never acts)
  var SUGGESTIONS = {
    'data.html': [
      ['Leave <code>customer_id</code> out', 'Every value is different, so it looks like an ID.'],
      ['Fill empty <code>contract_end</code> values with “no contract”', 'These customers have no fixed contract.']
    ],
    'goal.html': [
      ['Rank models by PR-AUC, not accuracy', 'Only about 1 in 5 training rows is a churner, so accuracy would look good even for a model that always says “no”.']
    ],
    'experiments.html': [
      ['Try the gradient boosting model family', 'Run 1 did not train it; it is one of the families the runs have not tried.'],
      ['Look at fold 5 of Run 1', 'It scored lowest; the newest rows may behave differently.']
    ],
    'improve.html': [
      ['Try the gradient boosting model family yourself', 'The loop cannot run on this time-ordered test design; a guided try can.']
    ],
    'predictions.html': [
      ['Start with the 200 highest scores', 'The top of the list is where the model is most sure.']
    ],
    'inbox.html': [
      ['Switch to the retrained model', 'Same kind of test, latest months: precision 0.59 against 0.52 for the model in use.']
    ],
    'monitoring.html': [
      ['Switch to the retrained model', 'Same kind of test, latest months: precision 0.59 against 0.52 for the model in use.'],
      ['Link the September outcomes file when it arrives', 'Real precision is known only for weeks with linked outcomes.']
    ]
  };
  var shell = document.getElementById('shell');
  var panel = document.getElementById('ai-panel');
  var sw = document.getElementById('ai-switch');

  // ------------------------------------------------------------ assistant chat panel (Beta)
  var asstPanel = document.getElementById('asst-panel');
  var asstBtn = document.getElementById('asst-toggle');
  var chatLog = [];
  var cost = 0.19;
  function botCard(id, text) {
    return '<div class="sug" data-card="' + id + '"><b>' + text + '</b><span class="why">Suggestion · needs your OK · est. compute 15 min</span>' +
      '<span class="row"><button class="btn sm primary" type="button" data-card-act="ok">Accept</button><button class="btn sm" type="button" data-card-act="no">Reject</button></span></div>';
  }
  function firstReply() {
    return '<div class="msg bot"><div>I read the column summaries of the Jun 2026 file and its data checks. Nothing is saved until you accept each suggestion below.</div>' +
      '<ol><li>Goal: predict <code>churned_next_30d</code>, rank by PR-AUC, with the rule <b>recall ≥ 0.80</b>.</li><li>A run on the Jun 2026 file. The rules choose the test design (the data has dates, so time-ordered folds), and you confirm it before training starts.</li></ol>' +
      botCard('goal', 'Set the goal: catch at least 80% of churners') + botCard('run', 'Start a run on the Jun 2026 file') +
      '<div class="steps-ran">Steps I ran: read the column summaries · read the data checks · read the target over time (training months only). Raw rows and final-test numbers are never sent to me.</div></div>';
  }
  var CANNED = {
    'Summarize this project': 'Northwind churn predicts <code>churned_next_30d</code>. The model in use is v1 (Run 1, LightGBM with balanced class weights, PR-AUC 0.70 ± 0.02 on cross-validation). Monitoring has a drift alert on <code>plan_type</code> (week of 28 Sep), and real precision was 0.48 in the week of 31 Aug, below the floor of 0.55. A retrained model (v5) is waiting for your decision in the Inbox.',
    'Why is precision down?': 'Real precision was 0.48 in the week of 31 Aug, on 2,140 rows with a real outcome, below the floor of 0.55. In the same weeks <code>plan_type</code> drifted: <code>family_5g</code>, a value not seen in training, is now 6% of rows. Drift says the inputs changed; it does not say why precision fell. On the same kind of test, latest months, the retrained model scored precision 0.59 and the model in use 0.52. I can only suggest a switch; you decide.',
    'What can you do?': 'I read this project (data summaries, runs, trust checks, models, monitoring) and answer in plain words with links. Anything that changes the project, such as starting a run or setting a goal, comes as a suggestion card that needs your OK. I never see raw rows or final-test numbers, and I cannot accept anything myself.'
  };
  function renderAsst() {
    if (!asstPanel) return;
    if (!chatLog.length) chatLog = [{ me: 'Predict who will churn next month; catch 80%' }, { html: firstReply() }];
    var h = '<div class="asst-head"><h2>Assistant <span class="beta">Beta</span></h2><span class="pill warn">Partly working</span><button class="asst-x" type="button" id="asst-close" aria-label="Close the assistant">×</button></div>' +
      '<div class="asst-note"><b>Off by default.</b> An admin turns the beta on for this workspace in AI settings. The assistant’s answers and suggestion cards are built and checked on 10 recorded conversations; <b>this panel is still being built</b>, so what you see is how it will look. Works with AI off: every action here is also a button on its screen.</div>' +
      '<div class="chat" id="chat" aria-live="polite">';
    chatLog.forEach(function (m) { h += m.me ? '<div class="msg me">' + esc(m.me) + '</div>' : (m.html || '<div class="msg bot">' + m.text + '</div>'); });
    h += '</div><div class="chat-foot"><div class="chat-chips">' + Object.keys(CANNED).map(function (k) { return '<button type="button" data-q="' + esc(k) + '">' + esc(k) + '</button>'; }).join('') + '</div>' +
      '<p class="small muted" style="margin:0">What you type is sent to the AI provider (Anthropic) to answer, with the screen you are on. Raw rows are never sent. People who approve suggestions can read this conversation.</p>' +
      '<form class="chat-form" id="chat-form"><input id="chat-input" type="text" placeholder="Ask about this project…" aria-label="Message the assistant" autocomplete="off"><button class="btn primary sm" type="submit">Send</button></form>' +
      '<div class="cost"><span>Cost of this conversation: <b>$' + cost.toFixed(2) + '</b> of $1.00</span><span>Model: Claude Sonnet 5.5 · price not confirmed yet</span></div></div>';
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
        if (b.getAttribute('data-card-act') === 'ok') { card.className = 'sug done accepted'; card.querySelector('.row').outerHTML = '<span class="state">Accepted ✓ (demo)</span>'; toast('Accepted (demo): in the product this saves a History entry and acts as you.'); }
        else { card.className = 'sug done'; card.querySelector('.row').outerHTML = '<span class="state">Rejected</span>'; }
      });
    });
  }
  function ask(q) {
    chatLog.push({ me: q });
    cost += 0.02;
    chatLog.push({ text: CANNED[q] || 'Demo only: this is a scripted conversation. In the product I would answer from this project’s summaries, show the steps I ran, and put any change in front of you as a suggestion.' });
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
    var h = '<h2>AI suggestions</h2><p class="ai-sub"><span class="pill plan">Planned</span> Comes with the assistant beta. It lists suggestions that already exist for this screen and never acts on its own. AI is off in this workspace, so this list is empty in the product today; the items below are examples.</p>';
    if (!list.length) h += '<div class="empty">Nothing to suggest on this screen.</div>';
    list.slice(0, 3).forEach(function (s, i) {
      var id = page + ':' + i, st = state.sug[id];
      h += '<div class="sug' + (st ? ' done ' + st : '') + '" data-sug="' + id + '"><b>' + s[0] + '</b><span class="why">' + esc(s[1]) + '</span>' +
        (st ? '<span class="state">' + (st === 'accepted' ? 'Accepted ✓' : 'Rejected') + '</span>'
          : '<span class="row"><button class="btn sm primary" type="button" data-act="accepted">Accept</button><button class="btn sm" type="button" data-act="rejected">Reject</button></span>') + '</div>';
    });
    panel.innerHTML = h;
    panel.querySelectorAll('[data-act]').forEach(function (b) {
      b.addEventListener('click', function () {
        var id = b.closest('[data-sug]').getAttribute('data-sug');
        state.sug[id] = b.getAttribute('data-act'); save(); renderPanel();
        toast(b.getAttribute('data-act') === 'accepted' ? 'Accepted (demo): nothing else on the screen changes.' : 'Rejected.');
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
      '1': { name: 'Run 1 · first run', model: 'LightGBM, balanced class weights', folds: [0.68, 0.71, 0.69, 0.72, 0.70], mean: '0.70', std: '0.02', checks: '1 to review · 2 not checked' },
      '2': { name: 'Run 2 · left out region_code', model: 'LightGBM, balanced class weights', folds: [0.67, 0.70, 0.69, 0.71, 0.69], mean: '0.69', std: '0.02', checks: '1 to review · 2 not checked' },
      '3': { name: 'Run 3 · 500 trees', model: 'LightGBM, balanced class weights, 500 trees', folds: [0.69, 0.71, 0.69, 0.72, 0.69], mean: '0.70', std: '0.02', checks: '1 to review · 2 not checked' },
      '4': { name: 'Run 4 · added extra trees', model: 'LightGBM, balanced class weights (extra trees did not beat it)', folds: [0.68, 0.71, 0.69, 0.72, 0.70], mean: '0.70', std: '0.02', checks: '1 to review · 2 not checked' }
    };
    var a = cmp.querySelector('[data-cmp-a]'), b = cmp.querySelector('[data-cmp-b]'), out = cmp.querySelector('[data-cmp-out]');
    function draw() {
      var ra = RUNS[a.value], rb = RUNS[b.value];
      var rows = '<tr><td>Chosen model</td><td>' + esc(ra.model) + '</td><td>' + esc(rb.model) + '</td></tr>';
      for (var i = 0; i < 5; i++) {
        rows += '<tr><td>Fold ' + (i + 1) + ' PR-AUC</td><td class="r">' + ra.folds[i].toFixed(2) + '</td><td class="r">' + rb.folds[i].toFixed(2) + '</td></tr>';
      }
      rows += '<tr><td><b>Average ± spread</b></td><td class="r"><b>' + ra.mean + ' ± ' + ra.std + '</b></td><td class="r"><b>' + rb.mean + ' ± ' + rb.std + '</b></td></tr>' +
        '<tr><td>Trust checks</td><td class="r">' + ra.checks + '</td><td class="r">' + rb.checks + '</td></tr>';
      var d = (parseFloat(rb.mean) - parseFloat(ra.mean)).toFixed(2);
      out.innerHTML = '<div class="tbl"><table><thead><tr><th></th><th class="r">' + esc(ra.name) + '</th><th class="r">' + esc(rb.name) + '</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
        '<p class="small muted">' + (a.value === b.value ? 'Pick two different runs to compare.' : 'Both runs use the same test design, so they were scored on the same folds. Difference in average PR-AUC: ' + (d > 0 ? '+' : '') + d + '. No “beats the noise” verdict is given: this test design is ordered by time, and the comparison rule assumes folds that are not.') + '</p>';
    }
    a.addEventListener('change', draw); b.addEventListener('change', draw); draw();
  }

  // Switches and settings that are not saved in the demo
  document.querySelectorAll('[data-toggle]').forEach(function (t) {
    t.addEventListener('click', function () {
      var on = t.getAttribute('aria-checked') !== 'true';
      t.setAttribute('aria-checked', on ? 'true' : 'false');
      var b = t.querySelector('b'); if (b) b.textContent = on ? 'on' : 'off';
      toast('Demo only: the setting is not saved.');
    });
  });
  document.querySelectorAll('select[data-demo-select]').forEach(function (sel) {
    sel.addEventListener('change', function () { toast('Demo only: not saved. In the product a change needs a reason and is saved in History.'); });
  });
  document.querySelectorAll('[data-save-thresholds]').forEach(function (b) {
    b.addEventListener('click', function () { toast('Saved (demo). Weeks computed with new scored data after you save use the new values; past weeks keep the values they used. Nothing is computed again now.'); });
  });

  // New project: the 3-step dialog
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
        ['Name and file', '<label class="field">Project name<input type="text" value="Plan upgrade" aria-label="Project name"></label><label class="upload-box"><b>Choose a file</b><span class="small muted">CSV, TSV, JSON, Parquet or Excel. Demo only: the file is not read.</span><input type="file"></label>'],
        ['What to predict', '<label class="field">Column to predict<select aria-label="Column to predict"><option>upgraded_next_30d</option></select></label><label class="field">Rule for success<input type="text" value="recall ≥ 0.60" aria-label="Rule for success"></label>'],
        ['Test design', '<p><b>The rules suggest:</b> time-ordered cross-validation with 5 folds, because the file has a date column (<code>snapshot_month</code>); the latest rows are kept as the final test set, used once per run.</p><p class="small muted">Training starts only after you confirm. Your confirmation is saved as “Suggested by the rules · Confirmed by you”. Prefer more control? <a href="projects.html">The longer four-step page</a> is still there.</p>']
      ];
      function draw() {
        d.innerHTML = '<form method="dialog" class="dialog-card"><button class="dialog-x" value="close" aria-label="Close">×</button><h2>New project</h2>' +
          '<div class="wiz-steps">' + STEPS_W.map(function (x, i) { return '<span class="' + (i === step ? 'on' : '') + '">' + (i + 1) + ' · ' + x[0] + '</span>'; }).join('') + '</div>' +
          '<div>' + STEPS_W[step][1] + '</div><div class="dialog-actions">' +
          (step > 0 ? '<button class="btn" type="button" id="wiz-back">Back</button>' : '') +
          (step < 2 ? '<button class="btn primary" type="button" id="wiz-next">Next</button>' : '<button class="btn primary" value="close" id="wiz-done">Confirm and start training (demo)</button>') + '</div></form>';
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
