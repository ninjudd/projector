// Projector site: renders the README, the docs, the project plans, and the list of
// pull request walkthroughs from site.json. Every view has a real path under the
// site's base; links between views update the address without a page load.
(function () {
  'use strict';

  var STATUS_ORDER = ['in-progress', 'ready', 'draft', 'completed'];
  var PRIORITY_ORDER = ['now', 'next', 'later'];
  var site = null;
  var markdownFiles = {};
  var siteFiles = {};
  var routes = {};
  var searchIndex = null;
  var root = document.getElementById('site');
  // A walkthrough page draws its own layout and carries only this bar.
  var bar = document.getElementById('sitebar');
  if (!root && !bar) return;
  var base = (root || bar).dataset.base || '/';

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function repoUrl(kind, path) {
    return 'https://github.com/' + site.repo + (path ? '/' + kind + '/' + encodeURIComponent(site.branch) + '/' + path.split('/').map(encodeURIComponent).join('/') : '');
  }
  function contentUrl(path) {
    return base + site.content + '/' + path.split('/').map(encodeURIComponent).join('/');
  }
  // A Markdown file's site path: its repository path without `.md`, as the build names it.
  function docRoute(path) {
    if (path === 'README.md') return '';
    var slash = path.lastIndexOf('/');
    if (path.slice(slash + 1).toLowerCase() === 'readme.md') return path.slice(0, slash + 1);
    return (/\.md$/.test(path) ? path.slice(0, -3) : path) + '/';
  }
  function projectFor(path) {
    for (var i = 0; i < site.projects.length; i++) if (site.projects[i].path === path) return site.projects[i];
    return null;
  }
  function routeFor(path) {
    var project = projectFor(path);
    if (project) return base + 'projects/' + project.name + '/';
    if (path === site.readme) return base;
    if (path === site.projectsReadme) return base + 'projects/';
    return base + docRoute(path);
  }

  // A path relative to the document at `from`, resolved to a repository path.
  function resolve(from, href) {
    var parts = from.split('/').slice(0, -1);
    href.split('/').forEach(function (part) {
      if (part === '..') parts.pop();
      else if (part && part !== '.') parts.push(part);
    });
    return parts.join('/');
  }

  function stripFrontmatter(text) {
    var match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(text);
    return match ? text.slice(match[0].length) : text;
  }

  function slug(text) {
    return text.toLowerCase().trim().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-');
  }

  // Render Markdown from the repository file at `path`: sanitize it, give headings
  // ids, keep links between copied documents inside the site, and send every
  // other repository link to GitHub.
  function renderMarkdown(text, path) {
    var box = document.createElement('div');
    box.className = 'markdown';
    box.innerHTML = window.DOMPurify.sanitize(window.marked.parse(stripFrontmatter(text)));
    box.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(function (h) {
      if (!h.id) h.id = slug(h.textContent);
    });
    box.querySelectorAll('a[href]').forEach(function (a) {
      var href = a.getAttribute('href');
      if (href.charAt(0) === '#' || /^[a-z][a-z0-9+.-]*:/i.test(href) || href.charAt(0) === '/') return;
      var hash = href.indexOf('#') >= 0 ? href.slice(href.indexOf('#')) : '';
      var target = resolve(path, href.split('#')[0]);
      if (markdownFiles[target]) a.setAttribute('href', routeFor(target) + hash);
      else if (siteFiles[target]) a.setAttribute('href', contentUrl(target));
      else a.setAttribute('href', repoUrl('blob', target));
    });
    box.querySelectorAll('img[src]').forEach(function (img) {
      var src = img.getAttribute('src');
      if (/^[a-z][a-z0-9+.-]*:/i.test(src) || src.charAt(0) === '/') return;
      var target = resolve(path, src);
      img.setAttribute('src', siteFiles[target] ? contentUrl(target) : repoUrl('raw', target));
    });
    if (window.hljs) box.querySelectorAll('pre code').forEach(function (code) { window.hljs.highlightElement(code); });
    return box;
  }

  function badge(kind, value) {
    return value ? '<span class="badge ' + kind + '-' + esc(value) + '">' + esc(value) + '</span>' : '';
  }

  function nav(active) {
    var items = [[base, 'Home', 'home']];
    if (site.projects.length) items.push([base + 'projects/', 'Projects', 'projects']);
    if (site.walkthroughs.length) items.push([base + 'prs/', 'PRs', 'prs']);
    site.docs.forEach(function (doc) { items.push([routeFor(doc.path), doc.title, doc.path]); });
    return items.map(function (item) {
      return '<a href="' + esc(item[0]) + '"' + (item[2] === active ? ' class="active" aria-current="page"' : '') + '>' + esc(item[1]) + '</a>';
    }).join('');
  }

  function header(active, extra) {
    return '<header class="sitebar' + (extra ? ' ' + extra : '') + '"><a class="sitename" href="' + esc(base) + '">' +
      esc(site.repo || 'Projector') + '</a><nav class="sitenav" aria-label="Site">' + nav(active) + '</nav>' +
      '<form class="sitesearch" role="search" action="' + esc(base + 'search/') + '">' +
      '<input type="search" name="q" placeholder="Search" aria-label="Search the site"></form></header>';
  }

  function frame(active, title, body) {
    document.title = title ? title + ' · ' + site.repo : site.repo;
    root.innerHTML =
      header(active) +
      '<main class="sitemain" id="sitemain"></main>' +
      '<footer class="sitefoot">Built by <a href="https://github.com/ninjudd/projector">Projector</a> from ' +
      '<a href="' + esc(repoUrl()) + '">' + esc(site.repo) + '</a>.</footer>';
    var main = document.getElementById('sitemain');
    if (typeof body === 'string') main.innerHTML = body;
    else main.appendChild(body);
    return main;
  }

  function loadMarkdown(path) {
    return fetch(contentUrl(path)).then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.text();
    });
  }

  function showDocument(active, path, title, before) {
    frame(active, title, '<p class="note">Loading…</p>');
    loadMarkdown(path).then(function (text) {
      var main = frame(active, title, renderMarkdown(text, path));
      if (before) main.insertBefore(before, main.firstChild);
      var target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (target) target.scrollIntoView();
    }, function (error) {
      frame(active, title, '<p class="note">Could not load ' + esc(path) + ': ' + esc(error.message) + '</p>');
    });
  }

  function showHome() {
    if (site.readme) return showDocument('home', site.readme, '');
    frame('home', '', '<h1>' + esc(site.repo) + '</h1><p class="note">This repository has no README.md.</p>');
  }

  function depth(name) { return name.split('/').length - 1; }

  function showProjects() {
    var groups = STATUS_ORDER.map(function (status) {
      var rows = site.projects.filter(function (p) { return p.status === status; });
      rows.sort(function (a, b) {
        var pa = PRIORITY_ORDER.indexOf(a.priority), pb = PRIORITY_ORDER.indexOf(b.priority);
        return (pa < 0 ? 9 : pa) - (pb < 0 ? 9 : pb) || a.name.localeCompare(b.name);
      });
      if (!rows.length) return '';
      return '<section class="pgroup"><h2>' + esc(status) + ' <span class="count">' + rows.length + '</span></h2>' +
        '<div class="tblwrap"><table class="tbl projects"><tr><th>Project</th><th>Priority</th><th>Name</th></tr>' +
        rows.map(function (p) {
          return '<tr data-search="' + esc((p.name + ' ' + p.title).toLowerCase()) + '">' +
            '<td style="padding-left:' + (0.75 + depth(p.name) * 1.25) + 'rem"><a href="' + esc(base + 'projects/' + p.name + '/') + '">' + esc(p.title) + '</a></td>' +
            '<td>' + badge('priority', p.priority) + '</td><td class="mono">' + esc(p.name) + '</td></tr>';
        }).join('') + '</table></div></section>';
    }).join('');
    var main = frame('projects', 'Projects',
      '<h1>Projects</h1><p class="note">' + site.projects.length + ' plans under <span class="mono">' + esc(site.projectsDir) + '</span>. ' +
      'Status says how finished a plan is; priority says when it is scheduled.</p>' +
      '<input class="filter" type="search" placeholder="Filter projects" aria-label="Filter projects">' + groups);
    main.querySelector('.filter').addEventListener('input', function (event) {
      var q = event.target.value.toLowerCase();
      main.querySelectorAll('tr[data-search]').forEach(function (row) { row.hidden = q && row.dataset.search.indexOf(q) < 0; });
    });
    if (site.projectsReadme) {
      loadMarkdown(site.projectsReadme).then(function (text) {
        var intro = document.createElement('details');
        intro.className = 'pintro';
        intro.innerHTML = '<summary>How projects work</summary>';
        intro.appendChild(renderMarkdown(text, site.projectsReadme));
        main.insertBefore(intro, main.querySelector('.filter'));
      }, function () {});
    }
  }

  function showProject(name) {
    var project = null;
    site.projects.forEach(function (p) { if (p.name === name) project = p; });
    if (!project) return frame('projects', 'Not found', '<h1>No project named ' + esc(name) + '</h1><p><a href="' + esc(base + 'projects/') + '">All projects</a></p>');
    var parents = [];
    var parts = name.split('/');
    for (var i = 1; i < parts.length; i++) {
      var parentName = parts.slice(0, i).join('/');
      site.projects.forEach(function (p) { if (p.name === parentName) parents.push(p); });
    }
    var children = site.projects.filter(function (p) { return p.name.indexOf(name + '/') === 0 && depth(p.name) === depth(name) + 1; });
    var head = document.createElement('div');
    head.className = 'phead';
    head.innerHTML =
      '<div class="crumbs"><a href="' + esc(base + 'projects/') + '">Projects</a>' +
      parents.map(function (p) { return ' / <a href="' + esc(base + 'projects/' + p.name + '/') + '">' + esc(p.name.split('/').pop()) + '</a>'; }).join('') +
      ' / <span class="mono">' + esc(parts[parts.length - 1]) + '</span></div>' +
      '<div class="pmeta">' + badge('status', project.status) + badge('priority', project.priority) +
      (project.owner ? '<span class="note">Owner ' + esc(project.owner) + '</span>' : '') +
      '<a class="note" href="' + esc(repoUrl('blob', project.path)) + '">View on GitHub</a></div>' +
      (children.length ? '<div class="plist"><b>Nested projects</b> ' + children.map(function (c) {
        return '<a href="' + esc(base + 'projects/' + c.name + '/') + '">' + esc(c.title) + '</a> ' + badge('status', c.status);
      }).join(' · ') + '</div>' : '') +
      (project.files.length ? '<div class="plist"><b>Supplemental files</b> ' + project.files.map(function (f) {
        return '<a href="' + esc(routeFor(f)) + '">' + esc(f.split('/').pop()) + '</a>';
      }).join(' · ') + '</div>' : '') +
      (project.walkthroughs.length ? '<div class="plist"><b>Pull requests</b> ' + project.walkthroughs.map(function (n) {
        var w = walkthroughFor(n);
        return '<a href="' + esc(base + 'prs/' + n + '/') + '">#' + n + (w ? ' ' + esc(w.name || w.title) : '') + '</a>';
      }).join(' · ') + '</div>' : '');
    showDocument('projects', project.path, project.title, head);
  }

  function walkthroughFor(number) {
    for (var i = 0; i < site.walkthroughs.length; i++) if (site.walkthroughs[i].number === number) return site.walkthroughs[i];
    return null;
  }

  function projectLinks(names) {
    return names.map(function (name) {
      var project = null;
      site.projects.forEach(function (p) { if (p.name === name) project = p; });
      return '<a href="' + esc(base + 'projects/' + name + '/') + '">' + esc(project ? project.title : name) + '</a>';
    }).join('<br>');
  }

  function showPrs() {
    frame('prs', 'Pull request walkthroughs',
      '<h1>Pull request walkthroughs</h1>' +
      '<div class="tblwrap"><table class="tbl"><tr><th>PR</th><th>Walkthrough</th><th>Plans</th><th>Head</th><th>Versions</th><th>Updated</th></tr>' +
      site.walkthroughs.map(function (w) {
        var url = esc(base + 'prs/' + w.number + '/');
        return '<tr><td><a href="' + url + '">#' + w.number + '</a></td><td><a href="' + url + '">' + esc(w.name || w.title) + '</a>' +
          '<div class="note">' + esc(w.title) + '</div></td><td>' + projectLinks(w.projects || []) + '</td>' +
          '<td class="mono">' + esc(String(w.head).slice(0, 9)) + '</td>' +
          '<td>' + w.heads + '</td><td>' + esc(w.updated) + '</td></tr>';
      }).join('') + '</table></div>' +
      '<p class="note">Built by Projector\'s <span class="mono">walkthrough-pr</span> skill. Each link opens the newest version; older heads are listed in its sidebar.</p>');
  }

  // Centered on the whole query where the text holds it, else on the earliest word.
  function snippet(text, words) {
    var lower = text.toLowerCase();
    var at = lower.indexOf(words.join(' '));
    if (at < 0) at = words.reduce(function (first, w) { var i = lower.indexOf(w); return i >= 0 && (first < 0 || i < first) ? i : first; }, -1);
    var start = Math.max(0, at - 60);
    var piece = (start ? '…' : '') + text.slice(start, start + 200) + (start + 200 < text.length ? '…' : '');
    var marked = esc(piece);
    words.forEach(function (w) {
      marked = marked.replace(new RegExp('(' + esc(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi'), '<mark>$1</mark>');
    });
    return marked;
  }

  // Every word must appear; a word in the title counts more than one in the text.
  function search(query) {
    var words = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return searchIndex.map(function (entry) {
      var title = entry.title.toLowerCase(), text = entry.text.toLowerCase(), score = 0;
      for (var i = 0; i < words.length; i++) {
        var inTitle = title.indexOf(words[i]) >= 0, inText = text.indexOf(words[i]) >= 0;
        if (!inTitle && !inText) return null;
        score += (inTitle ? 10 : 0) + text.split(words[i]).length - 1;
      }
      return { entry: entry, score: score };
    }).filter(Boolean).sort(function (a, b) { return b.score - a.score; }).map(function (r) {
      return '<li><a href="' + esc(base + r.entry.route) + '">' + esc(r.entry.title) + '</a> <span class="note">' + esc(r.entry.kind) + '</span>' +
        '<div class="snippet">' + snippet(r.entry.text, words) + '</div></li>';
    });
  }

  function showSearch(query) {
    var main = frame('search', query ? 'Search: ' + query : 'Search',
      '<h1>Search</h1><input class="filter" type="search" aria-label="Search the site" placeholder="Search the README, docs, and plans">' +
      '<ol class="results"></ol>');
    var input = main.querySelector('.filter');
    var list = main.querySelector('.results');
    input.value = query || '';
    function render() {
      var q = input.value.trim();
      history.replaceState(null, '', base + 'search/' + (q ? '?q=' + encodeURIComponent(q) : ''));
      var results = q ? search(q) : [];
      list.innerHTML = results.length ? results.join('') : (q ? '<p class="note">Nothing matches every word.</p>' : '');
    }
    input.addEventListener('input', render);
    input.focus();
    if (searchIndex) return render();
    list.innerHTML = '<p class="note">Loading…</p>';
    fetch(base + 'search.json').then(function (r) { return r.json(); }).then(function (data) {
      searchIndex = data;
      render();
    }, function (error) {
      list.innerHTML = '<p class="note">Could not load the search index: ' + esc(error.message) + '</p>';
    });
  }

  function route() {
    var path = location.pathname;
    var rel = path.indexOf(base) === 0 ? path.slice(base.length) : path.replace(/^\//, '');
    rel = decodeURIComponent(rel).replace(/index\.html$/, '').replace(/\/$/, '');
    window.scrollTo(0, 0);
    if (!rel) return showHome();
    if (rel === 'projects') return showProjects();
    if (rel.indexOf('projects/') === 0) return showProject(rel.slice('projects/'.length));
    if (rel === 'prs') return showPrs();
    if (rel === 'search') return showSearch(new URLSearchParams(location.search).get('q') || '');
    var file = routes[rel + '/'];
    if (file) {
      var doc = null;
      site.docs.forEach(function (d) { if (d.path === file) doc = d; });
      return showDocument(doc ? file : 'projects', file, doc ? doc.title : file.split('/').pop());
    }
    frame('', 'Not found', '<h1>Not found</h1><p><a href="' + esc(base) + '">Home</a></p>');
  }

  // A link to another view of this site renders in place; a walkthrough page, an
  // asset, or anything outside the site loads normally.
  function inSite(a) {
    if (a.target || a.hasAttribute('download') || a.origin !== location.origin) return false;
    var path = a.pathname;
    if (path.indexOf(base) !== 0) return false;
    var rel = path.slice(base.length);
    return !/^(prs\/\d|assets\/|content\/)/.test(rel) && !/\.[a-z0-9]+$/i.test(rel);
  }

  document.addEventListener('click', function (event) {
    if (!root || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var a = event.target.closest && event.target.closest('a[href]');
    if (!a || !inSite(a)) return;
    if (a.pathname === location.pathname && a.hash) return;
    event.preventDefault();
    history.pushState(null, '', a.href);
    route();
  });

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!root || !form.classList || !form.classList.contains('sitesearch')) return;
    event.preventDefault();
    var q = form.querySelector('input').value.trim();
    history.pushState(null, '', base + 'search/' + (q ? '?q=' + encodeURIComponent(q) : ''));
    route();
  });

  fetch(base + 'site.json')
    .then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    })
    .then(function (data) {
      site = data;
      if (!root) {
        bar.outerHTML = header('prs', 'scrolls');
        return;
      }
      (site.files || []).forEach(function (f) { siteFiles[f] = true; });
      if (site.readme) markdownFiles[site.readme] = true;
      if (site.projectsReadme) markdownFiles[site.projectsReadme] = true;
      site.docs.forEach(function (d) { markdownFiles[d.path] = true; routes[docRoute(d.path)] = d.path; });
      site.projects.forEach(function (p) {
        markdownFiles[p.path] = true;
        p.files.forEach(function (f) { markdownFiles[f] = true; routes[docRoute(f)] = f; });
      });
      window.addEventListener('popstate', route);
      route();
    }, function (error) {
      root.textContent = 'This site could not load its data: ' + error.message;
    });
})();
