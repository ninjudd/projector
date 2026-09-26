// Projector site: renders the README, the docs, the project plans, and the list of
// pull request walkthroughs from site.json, routing on the URL's hash.
(function () {
  'use strict';

  var STATUS_ORDER = ['in-progress', 'ready', 'draft', 'completed'];
  var PRIORITY_ORDER = ['now', 'next', 'later'];
  var site = null;
  var markdownFiles = {};
  var root = document.getElementById('site');

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function repoUrl(kind, path) {
    return 'https://github.com/' + site.repo + (path ? '/' + kind + '/' + encodeURIComponent(site.branch) + '/' + path.split('/').map(encodeURIComponent).join('/') : '');
  }
  function contentUrl(path) {
    return site.content + '/' + path.split('/').map(encodeURIComponent).join('/');
  }
  function projectFor(path) {
    for (var i = 0; i < site.projects.length; i++) if (site.projects[i].path === path) return site.projects[i];
    return null;
  }
  function routeFor(path) {
    var project = projectFor(path);
    if (project) return '#/projects/' + project.name;
    if (path === site.readme) return '#/';
    if (path === site.projectsReadme) return '#/projects';
    return '#/' + path;
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
      if (href.charAt(0) === '#') {
        var id = decodeURIComponent(href.slice(1));
        a.addEventListener('click', function (event) {
          var target = document.getElementById(id);
          if (target) { event.preventDefault(); target.scrollIntoView(); }
        });
        return;
      }
      if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.charAt(0) === '/') return;
      var target = resolve(path, href.split('#')[0]);
      if (markdownFiles[target]) a.setAttribute('href', routeFor(target));
      else a.setAttribute('href', repoUrl('blob', target));
    });
    box.querySelectorAll('img[src]').forEach(function (img) {
      var src = img.getAttribute('src');
      if (!/^[a-z][a-z0-9+.-]*:/i.test(src) && src.charAt(0) !== '/') img.setAttribute('src', repoUrl('raw', resolve(path, src)));
    });
    if (window.hljs) box.querySelectorAll('pre code').forEach(function (code) { window.hljs.highlightElement(code); });
    return box;
  }

  function badge(kind, value) {
    return value ? '<span class="badge ' + kind + '-' + esc(value) + '">' + esc(value) + '</span>' : '';
  }

  function nav(active) {
    var items = [['#/', 'Home', 'home']];
    if (site.projects.length) items.push(['#/projects', 'Projects', 'projects']);
    if (site.walkthroughs.length) items.push(['#/prs', 'PRs', 'prs']);
    site.docs.forEach(function (doc) { items.push([routeFor(doc.path), doc.title, doc.path]); });
    return items.map(function (item) {
      return '<a href="' + esc(item[0]) + '"' + (item[2] === active ? ' class="active" aria-current="page"' : '') + '>' + esc(item[1]) + '</a>';
    }).join('');
  }

  function frame(active, title, body) {
    document.title = title ? title + ' · ' + site.repo : site.repo;
    root.innerHTML =
      '<header class="sitebar"><a class="sitename" href="#/">' + esc(site.repo || 'Projector') + '</a>' +
      '<nav class="sitenav" aria-label="Site">' + nav(active) + '</nav></header>' +
      '<main class="sitemain" id="sitemain"></main>' +
      '<footer class="sitefoot">Built by <a href="https://github.com/ninjudd/projector">Projector</a> from ' +
      '<a href="' + esc(repoUrl()) + '">' + esc(site.repo) + '</a>.</footer>';
    var main = document.getElementById('sitemain');
    if (typeof body === 'string') main.innerHTML = body;
    else main.appendChild(body);
    window.scrollTo(0, 0);
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
            '<td style="padding-left:' + (0.75 + depth(p.name) * 1.25) + 'rem"><a href="#/projects/' + esc(p.name) + '">' + esc(p.title) + '</a></td>' +
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
    if (!project) return frame('projects', 'Not found', '<h1>No project named ' + esc(name) + '</h1><p><a href="#/projects">All projects</a></p>');
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
      '<div class="crumbs"><a href="#/projects">Projects</a>' +
      parents.map(function (p) { return ' / <a href="#/projects/' + esc(p.name) + '">' + esc(p.name.split('/').pop()) + '</a>'; }).join('') +
      ' / <span class="mono">' + esc(parts[parts.length - 1]) + '</span></div>' +
      '<div class="pmeta">' + badge('status', project.status) + badge('priority', project.priority) +
      (project.owner ? '<span class="note">Owner ' + esc(project.owner) + '</span>' : '') +
      '<a class="note" href="' + esc(repoUrl('blob', project.path)) + '">View on GitHub</a></div>' +
      (children.length ? '<div class="plist"><b>Nested projects</b> ' + children.map(function (c) {
        return '<a href="#/projects/' + esc(c.name) + '">' + esc(c.title) + '</a> ' + badge('status', c.status);
      }).join(' · ') + '</div>' : '') +
      (project.files.length ? '<div class="plist"><b>Supplemental files</b> ' + project.files.map(function (f) {
        return '<a href="#/' + esc(f) + '">' + esc(f.split('/').pop()) + '</a>';
      }).join(' · ') + '</div>' : '');
    showDocument('projects', project.path, project.title, head);
  }

  function showPrs() {
    frame('prs', 'Pull request walkthroughs',
      '<h1>Pull request walkthroughs</h1>' +
      '<div class="tblwrap"><table class="tbl"><tr><th>PR</th><th>Walkthrough</th><th>Head</th><th>Versions</th><th>Updated</th></tr>' +
      site.walkthroughs.map(function (w) {
        return '<tr><td><a href="' + w.number + '/">#' + w.number + '</a></td><td><a href="' + w.number + '/">' + esc(w.name || w.title) + '</a>' +
          '<div class="note">' + esc(w.title) + '</div></td><td class="mono">' + esc(String(w.head).slice(0, 9)) + '</td>' +
          '<td>' + w.heads + '</td><td>' + esc(w.updated) + '</td></tr>';
      }).join('') + '</table></div>' +
      '<p class="note">Built by Projector\'s <span class="mono">walkthrough-pr</span> skill. Each link opens the newest version; older heads are listed in its sidebar.</p>');
  }

  function route() {
    var hash = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
    if (!hash) return showHome();
    if (hash === 'projects') return showProjects();
    if (hash.indexOf('projects/') === 0) return showProject(hash.slice('projects/'.length));
    if (hash === 'prs') return showPrs();
    if (hash.indexOf('docs/') === 0) {
      var path = hash;
      var doc = null;
      site.docs.forEach(function (d) { if (d.path === path) doc = d; });
      if (markdownFiles[path]) return showDocument(doc ? path : 'projects', path, doc ? doc.title : path.split('/').pop());
    }
    frame('', 'Not found', '<h1>Not found</h1><p><a href="#/">Home</a></p>');
  }

  fetch('site.json')
    .then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    })
    .then(function (data) {
      site = data;
      if (site.readme) markdownFiles[site.readme] = true;
      if (site.projectsReadme) markdownFiles[site.projectsReadme] = true;
      site.docs.forEach(function (d) { markdownFiles[d.path] = true; });
      site.projects.forEach(function (p) {
        markdownFiles[p.path] = true;
        p.files.forEach(function (f) { markdownFiles[f] = true; });
      });
      window.addEventListener('hashchange', route);
      route();
    }, function (error) {
      root.textContent = 'This site could not load its data: ' + error.message;
    });
})();
