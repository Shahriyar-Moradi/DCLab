// DCLab product prototype. All state below is browser-local demo state; no API is called.
(function () {
  'use strict';

  var page = document.body.getAttribute('data-page') || location.pathname.split('/').pop();
  var role = document.body.getAttribute('data-role') || 'developer';
  var storeKey = 'dclab-product-prototype-v1';
  var state = readState();
  var toastTimer;

  function readState() {
    try {
      return JSON.parse(localStorage.getItem(storeKey) || '{"actions":[],"switches":{}}');
    } catch (_) {
      return { actions: [], switches: {} };
    }
  }

  function saveState() {
    try { localStorage.setItem(storeKey, JSON.stringify(state)); } catch (_) { /* Private browsing can disable storage. */ }
  }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function announce(message) {
    var toast = document.getElementById('demo-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'demo-toast';
      toast.className = 'demo-toast';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove('visible'); }, 2800);
  }

  var routes = [
    ['home.html', 'Developer home', 'Workspace overview, project activity, run queue and spend'],
    ['lab.html', 'Lab', 'Describe a model goal and review the agent plan'],
    ['pipeline.html', 'Pipeline evidence', 'Inspect each run stage, artifacts and validation'],
    ['data.html', 'Data', 'Datasets, columns, leakage checks and access'],
    ['experiments.html', 'Experiments', 'Compare candidates, folds and results'],
    ['improve.html', 'Improve', 'Constraint-aware experiment loop and budget'],
    ['graph.html', 'State graph', 'Lineage, project refs and stale nodes'],
    ['models.html', 'Models', 'Versions, batch predictions and release controls'],
    ['monitoring.html', 'Monitoring', 'Drift, labels, review and rollback evidence'],
    ['inbox.html', 'Decision inbox', 'Human approvals, questions and reversible proposals'],
    ['decisions.html', 'Decision history', 'Auditable decisions, actors and evidence'],
    ['governance.html', 'Governance', 'Policy, trust levels, budgets and kill switches'],
    ['agents.html', 'Agents & tools', 'NOOA, Jev, MCP, SDK, CLI and service tokens'],
    ['settings.html', 'Workspace settings', 'Members, usage, integrations and safety'],
    ['client.html', 'Business outcomes', 'Plain-language model results, prediction list and questions'],
    ['operator.html', 'Platform operator', 'Platform health, jobs, quarantine and gates'],
    ['map.html', 'Product map & review', 'Roadmap coverage, open gaps and deferred scope']
  ];

  function item(path, title, icon, count) { return [path, title, icon, count]; }
  var groups;
  if (role === 'client') {
    groups = [
      { label: 'Business workspace', items: [item('client.html', 'Outcomes', '◉'), item('client.html#predictions', 'Prediction list', '▤'), item('client.html#questions', 'Questions & reports', '✉', '1')] },
      { label: 'Preview', items: [item('index.html', 'Exit to workspace chooser', '⌂')] }
    ];
  } else if (role === 'operator') {
    groups = [
      { label: 'Platform operations', items: [item('operator.html', 'Overview', '▣'), item('operator.html#jobs', 'Jobs & workers', '⇶'), item('operator.html#quarantine', 'Quarantine', '▤'), item('operator.html#caps', 'Platform AI caps', '⚖'), item('operator.html#bench', 'Benchmarks & gates', '✓')] },
      { label: 'Preview', items: [item('index.html', 'Exit to workspace chooser', '⌂')] }
    ];
  } else {
    groups = [
      { label: 'Workspace', items: [item('home.html', 'Home', '⌂'), item('inbox.html', 'Inbox', '✉', '7'), item('governance.html', 'Governance', '⚖'), item('agents.html', 'Agents & tools', '⚙'), item('settings.html', 'Settings', '≡')] },
      { label: 'Project · Northwind churn', items: [item('lab.html', 'Lab (chat)', '◎'), item('pipeline.html', 'Pipeline', '⇶'), item('graph.html', 'Graph', '⬡'), item('data.html', 'Data', '▤'), item('experiments.html', 'Experiments', '⚗', '9'), item('improve.html', 'Improve', '↗'), item('models.html', 'Models', '◆'), item('monitoring.html', 'Monitoring', '∿', '1'), item('decisions.html', 'Decisions', '✓')] },
      { label: 'Preview', items: [item('index.html', 'Exit to workspace chooser', '⌂'), item('map.html', 'Product map & review', '⊞')] }
    ];
  }

  var nav = document.getElementById('nav');
  if (nav) {
    var user = role === 'client' ? 'demo@client.io' : role === 'operator' ? 'admin@dclab.io' : 'developer@dclab.io';
    var roleLabel = role === 'client' ? 'Business user · outcomes only' : role === 'operator' ? 'Platform operator · internal' : 'Developer · ML workspace';
    var h = '<a class="brand" href="index.html" aria-label="DCLab product preview home"><span class="logo">DC</span><b>DCLab</b><span class="brand-sub">PREVIEW</span></a>' +
      '<button class="ws ws-button" type="button" data-mock="switch workspace (demo)" aria-label="Preview workspace selection"><b>Northwind Telecom</b><small>sample workspace · EU region <span aria-hidden="true">⌄</span></small></button>';
    groups.forEach(function (g) {
      h += '<div class="nav-group"><div class="nav-label">' + g.label + '</div>';
      g.items.forEach(function (it) {
        var current = it[0].split('#')[0] === page ? ' aria-current="page"' : '';
        h += '<a href="' + it[0] + '"' + current + '><span class="ic" aria-hidden="true">' + it[2] + '</span><span>' + it[1] + '</span>' +
          (it[3] ? '<span class="cnt">' + it[3] + '</span>' : '') + '</a>';
      });
      h += '</div>';
    });
    h += '<div class="user"><span class="avatar" aria-hidden="true">' + (role === 'client' ? 'DM' : role === 'operator' ? 'AD' : 'DM') + '</span><span><b>' + user + '</b><small>' + roleLabel + '</small></span></div>';
    nav.innerHTML = h;
  }

  function setupTabs() {
    document.querySelectorAll('[data-tabs]').forEach(function (bar) {
      var scope = bar.getAttribute('data-tabs');
      var buttons = Array.prototype.slice.call(bar.querySelectorAll('button[data-tab]'));
      bar.setAttribute('role', 'tablist');
      buttons.forEach(function (button, index) {
        var tabId = scope + '-tab-' + button.getAttribute('data-tab');
        var panel = document.querySelector('[data-tabpanel="' + scope + ':' + button.getAttribute('data-tab') + '"]');
        button.id = tabId;
        button.setAttribute('role', 'tab');
        button.setAttribute('aria-controls', scope + '-panel-' + button.getAttribute('data-tab'));
        button.setAttribute('tabindex', button.getAttribute('aria-selected') === 'true' ? '0' : '-1');
        if (panel) {
          panel.id = scope + '-panel-' + button.getAttribute('data-tab');
          panel.setAttribute('role', 'tabpanel');
          panel.setAttribute('aria-labelledby', tabId);
          panel.hidden = button.getAttribute('aria-selected') !== 'true';
        }
        button.addEventListener('click', function () { activate(buttons, button, scope); });
        button.addEventListener('keydown', function (event) {
          if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
          event.preventDefault();
          var next = (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
          buttons[next].focus();
          activate(buttons, buttons[next], scope);
        });
      });
    });
  }

  function activate(buttons, selected, scope) {
    buttons.forEach(function (button) {
      var active = button === selected;
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      button.setAttribute('tabindex', active ? '0' : '-1');
      var panel = document.querySelector('[data-tabpanel="' + scope + ':' + button.getAttribute('data-tab') + '"]');
      if (panel) {
        panel.hidden = !active;
        panel.classList.toggle('on', active);
      }
    });
  }

  setupTabs();

  // Section tabs: a .card.sect whose children are <section data-tab="Label" data-count="n">.
  document.querySelectorAll('.card.sect').forEach(function (card, cardIndex) {
    var sections = Array.prototype.slice.call(card.querySelectorAll(':scope > section[data-tab]'));
    if (!sections.length) return;
    var bar = document.createElement('div');
    bar.className = 'sect-tabs';
    bar.setAttribute('role', 'tablist');
    var buttons = sections.map(function (section, index) {
      var button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'tab');
      button.id = page + '-sect-' + cardIndex + '-' + index;
      button.textContent = section.getAttribute('data-tab');
      section.setAttribute('role', 'tabpanel');
      section.setAttribute('aria-labelledby', button.id);
      bar.appendChild(button);
      return button;
    });
    function show(index) {
      buttons.forEach(function (button, i) {
        button.setAttribute('aria-selected', i === index ? 'true' : 'false');
        button.setAttribute('tabindex', i === index ? '0' : '-1');
        sections[i].hidden = i !== index;
      });
    }
    buttons.forEach(function (button, index) {
      button.addEventListener('click', function () { show(index); });
    });
    var head = card.querySelector(':scope > .sect-head');
    if (head) head.insertAdjacentElement('afterend', bar); else card.insertAdjacentElement('afterbegin', bar);
    show(0);
  });
  function activateHashTab() {
    var name = location.hash.slice(1);
    if (!name) return;
    var match = Array.prototype.slice.call(document.querySelectorAll('[data-tabs] button[data-tab]')).find(function (button) {
      return button.getAttribute('data-tab') === name;
    });
    if (!match) return;
    var bar = match.closest('[data-tabs]');
    activate(Array.prototype.slice.call(bar.querySelectorAll('button[data-tab]')), match, bar.getAttribute('data-tabs'));
    setTimeout(function () { match.scrollIntoView({ block: 'nearest' }); }, 0);
  }
  activateHashTab();
  window.addEventListener('hashchange', activateHashTab);

  document.querySelectorAll('.node[data-insp]').forEach(function (node) {
    node.setAttribute('role', 'button');
    node.setAttribute('tabindex', '0');
    function selectNode() {
      document.querySelectorAll('.node.sel').forEach(function (other) { other.classList.remove('sel'); });
      node.classList.add('sel');
      document.querySelectorAll('[data-insp-panel]').forEach(function (panel) { panel.hidden = true; });
      var panel = document.querySelector('[data-insp-panel="' + node.getAttribute('data-insp') + '"]');
      if (panel) panel.hidden = false;
    }
    node.addEventListener('click', selectNode);
    node.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(); } });
  });

  function getDialog() {
    var dialog = document.getElementById('prototype-dialog');
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'prototype-dialog';
      dialog.className = 'prototype-dialog';
      dialog.addEventListener('click', function (event) {
        if (event.target === dialog) dialog.close();
      });
      document.body.appendChild(dialog);
    }
    return dialog;
  }

  function openInfo(title, body, className) {
    var dialog = getDialog();
    dialog.className = 'prototype-dialog ' + (className || '');
    dialog.innerHTML = '<form method="dialog" class="dialog-card"><button class="dialog-x" value="close" aria-label="Close dialog">×</button>' +
      '<p class="eyebrow">DCLAB PRODUCT PREVIEW</p><h2>' + esc(title) + '</h2><div class="dialog-body">' + body + '</div>' +
      '<div class="dialog-actions"><button class="btn primary" value="close">Done</button></div></form>';
    if (!dialog.open) dialog.showModal();
  }

  function makePalette() {
    var visibleRoutes = routes.filter(function (route) {
      if (route[0] === 'index.html') return true;
      if (role === 'client') return route[0] === 'client.html';
      if (role === 'operator') return route[0] === 'operator.html';
      return route[0] !== 'client.html' && route[0] !== 'operator.html';
    });
    var links = visibleRoutes.map(function (route) {
      return '<a class="palette-link" href="' + route[0] + '" data-search="' + esc((route[1] + ' ' + route[2]).toLowerCase()) + '"><b>' + esc(route[1]) + '</b><small>' + esc(route[2]) + '</small></a>';
    }).join('');
    openInfo('Jump to a workspace or tool', '<label class="field">Search pages and capabilities<input class="input palette-search" type="search" placeholder="Try “MCP”, “pipeline” or “predictions”" autofocus></label><div class="palette-list">' + links + '</div>', 'palette-dialog');
    var input = document.querySelector('.palette-search');
    if (input) {
      input.focus();
      input.addEventListener('input', function () {
        var query = input.value.trim().toLowerCase();
        document.querySelectorAll('.palette-link').forEach(function (link) { link.hidden = !link.getAttribute('data-search').includes(query); });
      });
    }
  }

  var commandButton = document.querySelector('.cmd');
  if (commandButton) {
    commandButton.setAttribute('role', 'button');
    commandButton.setAttribute('tabindex', '0');
    commandButton.setAttribute('aria-label', 'Search and jump to a DCLab page, Command or Control K');
    commandButton.addEventListener('click', makePalette);
    commandButton.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); makePalette(); } });
  }
  document.addEventListener('keydown', function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); makePalette(); }
  });

  function updateDemoBadge() {
    var badge = document.querySelector('[data-demo-status]');
    if (!badge) return;
    var count = state.actions.length;
    badge.textContent = count ? 'Synthetic data · ' + count + ' simulated' : 'Synthetic data · local demo';
    badge.setAttribute('aria-label', count + ' UI actions simulated in this browser. No server requests were made. Click for details.');
  }

  function actionResult(action) {
    var text = action.toLowerCase();
    if (/delete/.test(text)) return 'Deletion preview completed. In the real product this would require the correct operator capability, confirmation and an audit record.';
    if (/revoke/.test(text)) return 'Token revocation preview completed. A real token would stop authorizing future requests immediately.';
    if (/reject/.test(text)) return 'Rejection preview completed. A real rejection would require a reason and append a decision record; it would not erase the proposal.';
    if (/approve|accept|release|answer yes|answer no|confirm availability/.test(text)) return 'Approval preview completed. A real accepted choice would be validated and recorded with its evidence and actor.';
    if (/revert|rollback/.test(text)) return 'Reversal preview completed. A real revert would add a new decision and restore the prior safe reference; history stays intact.';
    if (/cancel|pause/.test(text)) return 'Stop/pause preview completed. A real request would respect the job stage and report whether cancellation was still safe.';
    if (/upload|score a file|labels/.test(text)) return 'Upload/score preview completed locally. The selected file was not transmitted, parsed or stored.';
    if (/download|export/.test(text)) return 'A small synthetic sample file is ready. It contains prototype data only, not workspace data.';
    if (/token/.test(text)) return 'Credential setup preview completed. No credential was generated or saved; the real UI must show a secret once and store only its hash.';
    if (/send|question/.test(text)) return 'Question preview completed using the prewritten sample response. No agent or model was called.';
    return 'Preview completed. The prototype recorded the intended UI action locally; it did not call DCLab or change a real resource.';
  }

  function downloadSample(action) {
    if (!/download|export/.test(action.toLowerCase())) return;
    var csv = /csv|predictions|usage/.test(action.toLowerCase());
    var content = csv
      ? 'customer_id,risk_score,top_reason\nC-48112,0.91,no login for 61 days\nC-10377,0.87,contract ends in 12 days\nC-22930,0.84,recent support tickets\n'
      : 'DCLab UI prototype sample\nAction: ' + action + '\nThis file contains synthetic demo data only.\n';
    var blob = new Blob([content], { type: csv ? 'text/csv;charset=utf-8' : 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = csv ? 'dclab-prototype-sample.csv' : 'dclab-prototype-sample.txt';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function updateLocalSurface(button, result) {
    button.classList.add('is-simulated');
    button.setAttribute('aria-pressed', 'true');
    button.title = 'Simulated locally only. ' + result;
    if (!button.querySelector('.sim-indicator')) {
      var indicator = document.createElement('span');
      indicator.className = 'sim-indicator';
      indicator.textContent = '✓ demo';
      button.appendChild(indicator);
    }
    var container = button.closest('.card, .page-head, .banner');
    if (container && !container.querySelector(':scope > .simulation-note')) {
      var note = document.createElement('div');
      note.className = 'simulation-note';
      note.setAttribute('role', 'status');
      note.textContent = result;
      container.appendChild(note);
    }
  }

  function simulateChat(button) {
    var composer = button.closest('.composer');
    var textarea = composer && composer.querySelector('textarea');
    var text = textarea && textarea.value.trim();
    if (!text) {
      if (textarea) { textarea.focus(); textarea.setCustomValidity('Type a question or instruction first.'); textarea.reportValidity(); textarea.addEventListener('input', function clear() { textarea.setCustomValidity(''); textarea.removeEventListener('input', clear); }); }
      else announce('This is a UI-only chat preview; no message was sent.');
      return;
    }
    var chat = composer.closest('.grid, .card') || composer.parentElement;
    var transcript = chat && chat.querySelector('.chat');
    if (transcript) {
      var user = document.createElement('div'); user.className = 'msg user'; user.textContent = text; transcript.appendChild(user);
      var reply = document.createElement('div'); reply.className = 'msg agent';
      var from = document.createElement('span'); from.className = 'from'; from.textContent = 'Prototype response · no AI call';
      var answer = document.createElement('p'); answer.textContent = /why|reason|flag/i.test(text) ? 'The displayed sample marks this customer as high risk based on the stored prediction reasons shown in the preview. In the real product, this response must cite the persisted explanation and never invent a reason.' : 'I recorded this as a local preview. In the real product, the Lab agent would use only authorized DCLab tools, show evidence and request approval for gated actions.';
      reply.appendChild(from); reply.appendChild(answer); transcript.appendChild(reply);
      reply.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    if (textarea) textarea.value = '';
    completeAction(button, 'Question added to the sample conversation. No model was called and no message left this browser.');
  }

  function completeAction(button, result, action) {
    var actionText = action || button.getAttribute('data-mock') || 'UI control';
    updateLocalSurface(button, result);
    state.actions.unshift({ id: button.dataset.actionId, page: page, action: actionText, at: new Date().toISOString() });
    state.actions = state.actions.slice(0, 40);
    saveState();
    updateDemoBadge();
    announce(result);
  }

  function openAction(button) {
    var action = button.getAttribute('data-mock') || button.textContent.trim();
    var needsReason = /reject|revert|delete|rollback|policy change|budget change/i.test(action);
    var needsFile = /upload|score a file|labels/i.test(action);
    var dialog = getDialog();
    dialog.className = 'prototype-dialog action-dialog';
    dialog.innerHTML = '<form class="dialog-card action-form"><button type="button" class="dialog-x" data-dialog-close aria-label="Close dialog">×</button>' +
      '<p class="eyebrow">INTERACTIVE, SYNTHETIC PREVIEW</p><h2>' + esc(action) + '</h2>' +
      '<p class="dialog-lead">Review the intended product behavior before simulating this control.</p>' +
      '<div class="action-flow"><div><b>1 · Check</b><span>Workspace, capability and current resource state are validated.</span></div><div><b>2 · Confirm</b><span>Approval, reason or budget gate is shown when this action requires it.</span></div><div><b>3 · Record</b><span>In DCLab, the result and evidence would be auditable and reversible where supported.</span></div></div>' +
      (needsFile ? '<label class="field">Optional local sample file<input class="input demo-file" type="file" accept=".csv,.parquet,.xlsx,.json"><small>The file name stays in this form. Nothing is uploaded or parsed.</small></label>' : '') +
      (needsReason ? '<label class="field">Reason for the decision<textarea class="input demo-reason" rows="3" placeholder="Example: reviewed evidence and policy…"></textarea></label>' : '') +
      '<div class="demo-warning"><b>UI-only safety boundary</b><span>No API request, model call, compute job, token, email or external action will occur. Only a small “simulated” mark is stored in this browser.</span></div>' +
      '<div class="dialog-actions"><button type="button" class="btn" data-dialog-close>Cancel</button><button type="submit" class="btn primary">Simulate in this browser</button></div></form>';
    var closeButtons = dialog.querySelectorAll('[data-dialog-close]');
    closeButtons.forEach(function (close) { close.addEventListener('click', function () { dialog.close(); }); });
    var form = dialog.querySelector('.action-form');
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var reason = form.querySelector('.demo-reason');
      if (reason && !reason.value.trim()) { reason.setCustomValidity('Add a short reason to preview this decision.'); reason.reportValidity(); reason.addEventListener('input', function clear() { reason.setCustomValidity(''); reason.removeEventListener('input', clear); }); return; }
      var file = form.querySelector('.demo-file');
      var result = actionResult(action);
      if (file && file.files && file.files[0]) result += ' Local file selected: ' + file.files[0].name + '.';
      downloadSample(action);
      completeAction(button, result, action);
      dialog.close();
    });
    if (!dialog.open) dialog.showModal();
  }

  document.querySelectorAll('.btn[data-mock]').forEach(function (button, index) {
    var id = page + ':' + index + ':' + button.getAttribute('data-mock');
    button.dataset.actionId = id;
    var priorAction = state.actions.find(function (entry) { return entry.id === id; });
    if (priorAction) updateLocalSurface(button, 'Previously simulated in this browser. No DCLab request was made.');
    button.addEventListener('click', function (event) {
      event.preventDefault();
      if (/^send$/i.test(button.getAttribute('data-mock')) && button.closest('.composer')) simulateChat(button);
      else openAction(button);
    });
  });

  // Make the design-system switches keyboard-operable in the prototype.
  document.querySelectorAll('.switch').forEach(function (control, index) {
    control.setAttribute('role', 'switch');
    control.setAttribute('tabindex', '0');
    var id = page + ':switch:' + index;
    var stored = state.switches[id];
    if (stored !== undefined) control.classList.toggle('on', stored);
    control.setAttribute('aria-checked', control.classList.contains('on') ? 'true' : 'false');
    function toggle() {
      var enabled = !control.classList.contains('on');
      control.classList.toggle('on', enabled);
      control.setAttribute('aria-checked', enabled ? 'true' : 'false');
      state.switches[id] = enabled;
      saveState();
      announce((control.textContent.trim() || 'Setting') + ': ' + (enabled ? 'on' : 'off') + ' in this demo only.');
    }
    control.addEventListener('click', toggle);
    control.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); } });
  });

  // Local search controls filter only the visible sample rows on the current page.
  document.querySelectorAll('input[placeholder]').forEach(function (input) {
    if (!/search|filter/i.test(input.placeholder)) return;
    var scope = input.closest('.card') || input.closest('.content') || document;
    var rows = scope.querySelectorAll('tbody tr');
    if (!rows.length) return;
    input.addEventListener('input', function () {
      var query = input.value.trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (row) {
        var match = !query || row.textContent.toLowerCase().includes(query);
        row.hidden = !match;
        if (match) shown += 1;
      });
      var count = scope.querySelector('.filter-count');
      if (!count) { count = document.createElement('small'); count.className = 'filter-count muted'; input.insertAdjacentElement('afterend', count); }
      count.textContent = query ? shown + ' sample row' + (shown === 1 ? '' : 's') + ' match' : rows.length + ' sample rows';
    });
  });

  document.querySelectorAll('select.input').forEach(function (select) {
    select.addEventListener('change', function () {
      var selection = select.options[select.selectedIndex];
      if (selection) announce('Preview control changed to “' + selection.textContent.trim() + '”. Values are synthetic; no query was sent.');
    });
  });

  // Table headings sort the synthetic rows; the stable evidence text is not changed.
  document.querySelectorAll('table thead th').forEach(function (heading, column) {
    var table = heading.closest('table');
    var body = table && table.querySelector('tbody');
    if (!body || heading.colSpan > 1) return;
    heading.tabIndex = 0;
    heading.title = 'Sort sample rows by ' + heading.textContent.trim();
    function sortRows() {
      var direction = heading.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
      table.querySelectorAll('thead th[aria-sort]').forEach(function (other) { other.removeAttribute('aria-sort'); });
      heading.setAttribute('aria-sort', direction);
      var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
      rows.sort(function (left, right) {
        var a = left.cells[column] ? left.cells[column].textContent.trim() : '';
        var b = right.cells[column] ? right.cells[column].textContent.trim() : '';
        var numberA = Number(a.replace(/[^\d.-]/g, ''));
        var numberB = Number(b.replace(/[^\d.-]/g, ''));
        var bothNumeric = a !== '' && b !== '' && Number.isFinite(numberA) && Number.isFinite(numberB);
        var compared = bothNumeric ? numberA - numberB : a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
        return direction === 'ascending' ? compared : -compared;
      });
      rows.forEach(function (row) { body.appendChild(row); });
    }
    heading.addEventListener('click', sortRows);
    heading.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sortRows(); } });
  });

  var demoBadge = document.querySelector('[data-demo-status]');
  if (demoBadge) {
    demoBadge.addEventListener('click', function () {
      var entries = state.actions.slice(0, 8).map(function (entry) { return '<li><b>' + esc(entry.action) + '</b><small>' + esc(entry.page) + ' · local preview</small></li>'; }).join('');
      openInfo('Demo activity in this browser', '<p>These are simulated UI clicks, not actual DCLab events. No customer content is stored in this history.</p>' + (entries ? '<ol class="demo-log">' + entries + '</ol>' : '<p class="empty">No actions simulated yet. Open a workspace and try a control.</p>') + '<p class="small muted">To clear the prototype history and switches, use “Reset demo state” on the workspace chooser.</p>');
    });
  }

  updateDemoBadge();
})();
