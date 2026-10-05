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
    ['home.html', 'Home', 'What needs you, projects and recent activity'],
    ['inbox.html', 'Inbox', 'Decisions waiting for you, automatic changes and history'],
    ['governance.html', 'AI governance', 'What the assistant may do, what it sees, budget and audit'],
    ['settings.html', 'Settings', 'Members, usage, API and MCP, data safety'],
    ['lab.html', 'Lab', 'Give the assistant your data and a goal'],
    ['data.html', 'Data', 'Datasets, columns, checks and versions'],
    ['experiments.html', 'Experiments', 'Runs, comparison and the improve loop'],
    ['pipeline.html', 'Run E1', 'Summary, pipeline, models tried, review and code'],
    ['models.html', 'Models', 'Model in use, versions and scoring runs'],
    ['monitoring.html', 'Monitoring', 'Drift, performance and automatic actions'],
    ['graph.html', 'Lineage', 'How data, runs and models connect'],
    ['client.html', 'Business view', 'Plain-language results and this week\'s list'],
    ['operator.html', 'Operator console', 'Workspaces, jobs, quarantine and limits'],
    ['map.html', 'Product map', 'Journeys, open questions and what comes later']
  ];

  var ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    inbox: '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
    shield: '<path d="M12 3l8 3v6c0 4.5-3.2 7.9-8 9-4.8-1.1-8-4.5-8-9V6z"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z"/>',
    data: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    flask: '<path d="M9 3h6M10 3v6l-5.5 9.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3"/>',
    cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    pulse: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    lineage: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H12a3.5 3.5 0 0 1 3.5 3.5M8.5 18H12a3.5 3.5 0 0 0 3.5-3.5"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>'
  };
  function icon(name) {
    return '<svg class="ic" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || '') + '</svg>';
  }
  function item(path, title, iconName, count) { return [path, title, iconName, count]; }
  var groups, userName, roleLabel, initials;
  if (role === 'client') {
    userName = 'Alex Kim'; roleLabel = 'Retention lead'; initials = 'AK';
    groups = [{ label: 'Northwind churn', items: [item('client.html', 'Overview', 'home'), item('client.html#predictions', 'This week\'s list', 'list'), item('client.html#questions', 'Questions', 'chat', '1'), item('client.html#about', 'About this model', 'info')] }];
  } else if (role === 'operator') {
    userName = 'Platform admin'; roleLabel = 'DCLab operations'; initials = 'PA';
    groups = [{ label: 'Platform', items: [item('operator.html', 'Workspaces', 'grid'), item('operator.html#jobs', 'Jobs', 'pulse'), item('operator.html#quarantine', 'Quarantine', 'inbox'), item('operator.html#caps', 'Platform limits', 'shield'), item('operator.html#bench', 'Quality gates', 'check')] }];
  } else {
    userName = 'Dana Reyes'; roleLabel = 'Data scientist'; initials = 'DR';
    groups = [
      { label: 'Workspace', items: [item('home.html', 'Home', 'home'), item('inbox.html', 'Inbox', 'inbox', '3'), item('governance.html', 'AI governance', 'shield'), item('settings.html', 'Settings', 'sliders')] },
      { label: 'Northwind churn', items: [item('lab.html', 'Lab', 'chat'), item('data.html', 'Data', 'data'), item('experiments.html', 'Experiments', 'flask'), item('models.html', 'Models', 'cube'), item('monitoring.html', 'Monitoring', 'pulse', '1'), item('graph.html', 'Lineage', 'lineage')] }
    ];
  }

  var nav = document.getElementById('nav');
  var navPage = document.body.getAttribute('data-nav') || page;
  var navHashes = [];
  if (nav) {
    var h = '<a class="brand" href="index.html" aria-label="DCLab"><span class="logo">DC</span><b>DCLab</b></a>' +
      '<div class="ws">Northwind Telecom <span aria-hidden="true">⌄</span></div>';
    groups.forEach(function (g) {
      h += '<div class="nav-group"><div class="nav-label">' + g.label + '</div>';
      g.items.forEach(function (it) {
        if (it[0].indexOf('#') > -1) navHashes.push(it[0].split('#')[1]);
        h += '<a class="item" href="' + it[0] + '" data-href="' + it[0] + '">' + icon(it[2]) + '<span>' + it[1] + '</span>' +
          (it[3] ? '<span class="cnt">' + it[3] + '</span>' : '') + '</a>';
      });
      h += '</div>';
    });
    h += '<div class="foot"><div class="user"><span class="avatar" aria-hidden="true">' + initials + '</span><span><b>' + userName + '</b><small>' + roleLabel + '</small></span></div>' +
      '<a class="switch-view" href="index.html">Switch view</a></div>';
    nav.innerHTML = h;
  }
  // One sidebar item is current at a time; pages with hash-linked tabs follow the open tab.
  function markCurrent() {
    if (!nav) return;
    var hash = location.hash.slice(1);
    if (navHashes.indexOf(hash) === -1) hash = '';
    nav.querySelectorAll('a.item').forEach(function (link) {
      var parts = link.getAttribute('data-href').split('#');
      var current = parts[0] === navPage && (navHashes.length ? (parts[1] || '') === hash : true);
      if (current) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
  }
  markCurrent();
  window.addEventListener('hashchange', markCurrent);
  function onTabChange(button) {
    try { history.replaceState(null, '', '#' + button.getAttribute('data-tab')); } catch (_) { /* sandboxed frames may refuse */ }
    markCurrent();
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
        button.addEventListener('click', function () { activate(buttons, button, scope); onTabChange(button); });
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
  // The tab bar is generated so page sources stay plain HTML.
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
      if (section.getAttribute('data-count')) {
        var count = document.createElement('span');
        count.className = 'cnt';
        count.textContent = section.getAttribute('data-count');
        button.appendChild(count);
      }
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
      button.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        event.preventDefault();
        var next = (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next].focus(); show(next);
      });
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
    badge.textContent = 'Demo data';
    badge.setAttribute('aria-label', 'Demo data. ' + state.actions.length + ' actions tried in this browser. Nothing real was changed.');
  }

  function actionResult(action) {
    var text = action.toLowerCase();
    if (/delete/.test(text)) return 'Deleted. (Demo: nothing real was changed.)';
    if (/revoke/.test(text)) return 'Token revoked. (Demo.)';
    if (/reject/.test(text)) return 'Rejected and recorded. (Demo.)';
    if (/undo|revert|roll ?back/.test(text)) return 'Undone. The previous choice is back. (Demo.)';
    if (/answer/.test(text)) return 'Answer saved. (Demo.)';
    if (/approve|accept|use this model|confirm/.test(text)) return 'Approved and recorded. (Demo.)';
    if (/cancel|pause|stop/.test(text)) return 'Stopped. (Demo.)';
    if (/upload|score a file|labels/.test(text)) return 'File received. (Demo: nothing was uploaded.)';
    if (/download|export/.test(text)) return 'A sample file was downloaded.';
    if (/token/.test(text)) return 'Token created. (Demo.)';
    if (/run|start|improve/.test(text)) return 'Started. (Demo: no job was queued.)';
    return 'Done. (Demo: nothing real was changed.)';
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
    button.title = result;
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
      var from = document.createElement('span'); from.className = 'from'; from.textContent = 'Assistant';
      var answer = document.createElement('p'); answer.textContent = /why|reason|flag/i.test(text) ? 'No login for 61 days, three support tickets and a recent fee increase. Risk 0.91, well above the 0.31 threshold. (Sample reply.)' : 'This is a sample reply. In the product I would answer from your project and ask before changing anything.';
      reply.appendChild(from); reply.appendChild(answer); transcript.appendChild(reply);
      reply.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    if (textarea) textarea.value = '';
    completeAction(button, 'Sample reply added. (Demo: no model was called.)');
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
    var needsReason = /reject|roll ?back|delete|revoke/i.test(action);
    var needsFile = /upload|score a file|labels/i.test(action);
    if (!needsReason && !needsFile) {
      downloadSample(action);
      completeAction(button, actionResult(action), action);
      return;
    }
    var dialog = getDialog();
    dialog.className = 'prototype-dialog action-dialog';
    dialog.innerHTML = '<form class="dialog-card action-form"><button type="button" class="dialog-x" data-dialog-close aria-label="Close">×</button>' +
      '<h2>' + esc(button.textContent.trim()) + '</h2>' +
      (needsFile ? '<label class="field">Choose a file<input class="input demo-file" type="file" accept=".csv,.parquet,.xlsx,.json"><small>Demo only: the file stays on your computer.</small></label>' : '') +
      (needsReason ? '<label class="field">Reason<textarea class="input demo-reason" rows="3" placeholder="A short note for the record"></textarea></label>' : '') +
      '<div class="dialog-actions"><button type="button" class="btn" data-dialog-close>Cancel</button><button type="submit" class="btn primary">Confirm</button></div></form>';
    dialog.querySelectorAll('[data-dialog-close]').forEach(function (close) { close.addEventListener('click', function () { dialog.close(); }); });
    var form = dialog.querySelector('.action-form');
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var reason = form.querySelector('.demo-reason');
      if (reason && !reason.value.trim()) { reason.setCustomValidity('Add a short reason.'); reason.reportValidity(); reason.addEventListener('input', function clear() { reason.setCustomValidity(''); reason.removeEventListener('input', clear); }); return; }
      downloadSample(action);
      completeAction(button, actionResult(action), action);
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
      if (selection) announce('Showing “' + selection.textContent.trim() + '” (demo data).');
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
      openInfo('Demo data', '<p>Everything on these screens is invented sample data. Buttons respond, but nothing real is changed.</p>' + (entries ? '<ol class="demo-log">' + entries + '</ol>' : '<p class="empty">No actions simulated yet. Open a workspace and try a control.</p>') + '<p class="small muted">Reset the demo from the “Switch view” page.</p>');
    });
  }

  updateDemoBadge();
})();
