// Projector site: three sections, drawn from site.json. Projects lists the projects
// and renders each one's files beside a sidebar of its folder; Reviews lists the
// pull request walkthroughs; Docs renders the README and everything else under
// docs/ beside a sidebar of that tree. Every view has a real path under the site's
// base; links between views update the address without a page load.
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
  // A review page draws its own layout and carries only this bar.
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

  // A Markdown file's route inside its section, as the build names it: its path
  // without `.md`, and a readme at its folder.
  function localRoute(path) {
    var slash = path.lastIndexOf('/');
    if (path.slice(slash + 1).toLowerCase() === 'readme.md') return slash < 0 ? '' : path.slice(0, slash + 1);
    return (/\.md$/.test(path) ? path.slice(0, -3) : path) + '/';
  }
  // The README owns docs/ itself, so a readme at the top of docs/ moves aside.
  function docRoute(path) {
    return path.toLowerCase() === 'docs/readme.md' ? 'docs/readme/' : localRoute(path);
  }
  function inProjects(path) {
    return !!site.projectsDir && path.indexOf(site.projectsDir + '/') === 0;
  }
  function routeFor(path) {
    if (path === site.readme) return base + 'docs/';
    if (inProjects(path)) {
      var owner = ownerOf(path);
      if (!owner) return base + 'projects/' + localRoute(path.slice(site.projectsDir.length + 1));
      return base + 'projects/' + localRoute(owner.name + '/' + path.slice(folderOf(owner).length));
    }
    return base + docRoute(path);
  }

  function folderOf(project) { return project.path.slice(0, project.path.lastIndexOf('/') + 1); }
  function projectNamed(name) {
    for (var i = 0; i < site.projects.length; i++) if (site.projects[i].name === name) return site.projects[i];
    return null;
  }
  // The deepest project whose folder holds the file.
  function ownerOf(path) {
    var best = null;
    site.projects.forEach(function (p) {
      if (path.indexOf(folderOf(p)) === 0 && (!best || p.name.length > best.name.length)) best = p;
    });
    return best;
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
    var items = [];
    if (site.projects.length) items.push([base, 'Projects', 'projects']);
    if (site.reviews.length) items.push([base + 'reviews/', 'Reviews', 'reviews']);
    if (site.readme || site.docs.length) items.push([base + 'docs/', 'Docs', 'docs']);
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

  // Draw the page around `body`, with `side` as a sidebar when given, and return
  // the element the body went into.
  function frame(active, title, body, side) {
    document.title = title ? title + ' · ' + site.repo : site.repo;
    root.innerHTML =
      header(active) +
      '<main class="sitemain' + (side ? ' withside' : '') + '">' +
      (side ? '<nav class="side" aria-label="Section">' + side + '</nav>' : '') +
      '<div class="sidebody" id="sidebody"></div></main>' +
      '<footer class="sitefoot">Built by <a href="https://github.com/ninjudd/projector">Projector</a> from ' +
      '<a href="' + esc(repoUrl()) + '">' + esc(site.repo) + '</a>.</footer>';
    var main = document.getElementById('sidebody');
    if (typeof body === 'string') main.innerHTML = body;
    else main.appendChild(body);
    return main;
  }

  // A nested list of `items`, each a {path, title} under `prefix`, with `top` as its
  // heading. A folder holding a readme is labelled by it; any other folder by its name.
  function tree(items, prefix, top, current) {
    var rootNode = { dirs: {}, files: [] };
    items.forEach(function (item) {
      var parts = item.path.slice(prefix.length).split('/');
      var node = rootNode;
      for (var i = 0; i < parts.length - 1; i++) {
        node = node.dirs[parts[i]] = node.dirs[parts[i]] || { dirs: {}, files: [], index: null };
      }
      if (node !== rootNode && /^readme\.md$/i.test(parts[parts.length - 1])) node.index = item;
      else node.files.push(item);
    });
    function link(item) {
      return '<a href="' + esc(routeFor(item.path)) + '"' +
        (item.path === current ? ' class="active" aria-current="page"' : '') + '>' + esc(item.title) + '</a>';
    }
    function render(node) {
      var files = node.files.slice().sort(function (a, b) { return a.title.localeCompare(b.title); })
        .map(function (f) { return '<li>' + link(f) + '</li>'; });
      var dirs = Object.keys(node.dirs).sort().map(function (name) {
        var dir = node.dirs[name];
        return '<li>' + (dir.index ? link(dir.index) : '<span class="sidedir">' + esc(name) + '</span>') + render(dir) + '</li>';
      });
      var all = files.concat(dirs);
      return all.length ? '<ul>' + all.join('') + '</ul>' : '';
    }
    return (top ? '<div class="sidetop">' + link(top) + '</div>' : '') + render(rootNode);
  }

  function loadMarkdown(path) {
    return fetch(contentUrl(path)).then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.text();
    });
  }

  function showDocument(active, path, title, before, side) {
    frame(active, title, '<p class="note">Loading…</p>', side);
    loadMarkdown(path).then(function (text) {
      var main = frame(active, title, renderMarkdown(text, path), side);
      if (before) main.insertBefore(before, main.firstChild);
      var target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (target) target.scrollIntoView();
    }, function (error) {
      frame(active, title, '<p class="note">Could not load ' + esc(path) + ': ' + esc(error.message) + '</p>', side);
    });
  }

  // The README, or any document under docs/, beside the docs tree.
  function showDocs(path) {
    var top = site.readme ? { path: site.readme, title: 'README' } : null;
    var side = tree(site.docs, 'docs/', top, path);
    if (!path) return frame('docs', 'Docs', '<h1>Docs</h1><p class="note">This repository has no README.md.</p>', side);
    var doc = null;
    site.docs.forEach(function (d) { if (d.path === path) doc = d; });
    showDocument('docs', path, doc ? doc.title : 'README', null, side);
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
      '<h1>Projects</h1><p class="note">' + site.projects.length + ' projects under <span class="mono">' + esc(site.projectsDir) + '</span>. ' +
      'Status says how finished a project is; priority says when it is scheduled.</p>' +
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

  // Any file of a project, its readme or a supplemental file, beside a sidebar of
  // its top-level project's folder: every file, subdirectory, and nested project.
  function showProjectFile(path) {
    var project = ownerOf(path);
    if (!project) return frame('projects', 'Not found', '<h1>Not found</h1><p><a href="' + esc(base) + '">All projects</a></p>');
    var top = projectNamed(project.name.split('/')[0]) || project;
    var prefix = folderOf(top);
    var items = [];
    site.projects.forEach(function (p) {
      if (p === top || p.path.indexOf(prefix) !== 0) return;
      items.push({ path: p.path, title: p.title });
    });
    site.projects.forEach(function (p) {
      if (p.path.indexOf(prefix) === 0) p.files.forEach(function (f) { items.push(f); });
    });
    var side = tree(items, prefix, { path: top.path, title: top.title }, path);

    var isReadme = path === project.path;
    var file = null;
    project.files.forEach(function (f) { if (f.path === path) file = f; });
    var parts = project.name.split('/');
    var crumbs = '<a href="' + esc(base) + '">Projects</a>' + parts.map(function (part, i) {
      var name = parts.slice(0, i + 1).join('/');
      var here = isReadme && i === parts.length - 1;
      return ' / ' + (here ? '<span class="mono">' + esc(part) + '</span>' : '<a href="' + esc(base + 'projects/' + name + '/') + '">' + esc(part) + '</a>');
    }).join('') + (isReadme ? '' : ' / <span class="mono">' + esc(path.split('/').pop()) + '</span>');
    var head = document.createElement('div');
    head.className = 'phead';
    head.innerHTML = '<div class="crumbs">' + crumbs + '</div>' +
      (isReadme ? '<div class="pmeta">' + badge('status', project.status) + badge('priority', project.priority) +
        (project.owner ? '<span class="note">Owner ' + esc(project.owner) + '</span>' : '') +
        '<a class="note" href="' + esc(repoUrl('blob', project.path)) + '">View on GitHub</a></div>' +
        (project.reviews.length ? '<div class="plist"><b>Reviews</b> ' + project.reviews.map(function (n) {
          var r = reviewFor(n);
          return '<a href="' + esc(base + 'reviews/' + n + '/') + '">#' + n + (r ? ' ' + esc(r.name || r.title) : '') + '</a>';
        }).join(' · ') + '</div>' : '')
        : '<div class="pmeta"><a class="note" href="' + esc(repoUrl('blob', path)) + '">View on GitHub</a></div>');
    showDocument('projects', path, isReadme ? project.title : (file ? file.title : path.split('/').pop()), head, side);
  }

  function reviewFor(number) {
    for (var i = 0; i < site.reviews.length; i++) if (site.reviews[i].number === number) return site.reviews[i];
    return null;
  }

  function projectLinks(names) {
    return names.map(function (name) {
      var project = projectNamed(name);
      return '<a href="' + esc(base + 'projects/' + name + '/') + '">' + esc(project ? project.title : name) + '</a>';
    }).join('<br>');
  }

  function showReviews() {
    frame('reviews', 'Reviews',
      '<h1>Reviews</h1>' +
      '<div class="tblwrap"><table class="tbl"><tr><th>#</th><th>Review</th><th>Projects</th><th>Head</th><th>Versions</th><th>Updated</th></tr>' +
      site.reviews.map(function (r) {
        var url = esc(base + 'reviews/' + r.number + '/');
        return '<tr><td><a href="' + url + '">#' + r.number + '</a></td><td><a href="' + url + '">' + esc(r.name || r.title) + '</a>' +
          '<div class="note">' + esc(r.title) + '</div></td><td>' + projectLinks(r.projects || []) + '</td>' +
          '<td class="mono">' + esc(String(r.head).slice(0, 9)) + '</td>' +
          '<td>' + r.heads + '</td><td>' + esc(r.updated) + '</td></tr>';
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
    return highlight(piece, words);
  }

  // One pass over the raw text, longest word first, so a later word never
  // matches inside markup or an entity an earlier one produced.
  function highlight(text, words) {
    var pattern = words.slice().sort(function (a, b) { return b.length - a.length; })
      .map(function (w) { return w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|');
    return text.split(new RegExp('(' + pattern + ')', 'i')).map(function (part, i) {
      return i % 2 ? '<mark>' + esc(part) + '</mark>' : esc(part);
    }).join('');
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
      '<h1>Search</h1><input class="filter" type="search" aria-label="Search the site" placeholder="Search the projects and docs">' +
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
    // A repository with no projects opens on its docs instead.
    if (!rel) return site.projects.length ? showProjects() : showDocs(site.readme);
    if (rel === 'projects') return showProjects();
    if (rel === 'reviews') return showReviews();
    if (rel === 'docs') return showDocs(site.readme);
    if (rel === 'search') return showSearch(new URLSearchParams(location.search).get('q') || '');
    var file = routes[rel + '/'];
    if (file) return inProjects(file) ? showProjectFile(file) : showDocs(file);
    frame('', 'Not found', '<h1>Not found</h1><p><a href="' + esc(base) + '">Home</a></p>');
  }

  // A link to another view of this site renders in place; a review page, an asset,
  // or anything outside the site loads normally.
  function inSite(a) {
    if (a.target || a.hasAttribute('download') || a.origin !== location.origin) return false;
    var path = a.pathname;
    if (path.indexOf(base) !== 0) return false;
    var rel = path.slice(base.length);
    return !/^(reviews\/\d|assets\/|content\/)/.test(rel) && !/\.[a-z0-9]+$/i.test(rel);
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
        bar.outerHTML = header('reviews', 'scrolls');
        return;
      }
      (site.files || []).forEach(function (f) { siteFiles[f] = true; });
      if (site.readme) markdownFiles[site.readme] = true;
      if (site.projectsReadme) markdownFiles[site.projectsReadme] = true;
      site.docs.forEach(function (d) { markdownFiles[d.path] = true; routes[docRoute(d.path)] = d.path; });
      site.projects.forEach(function (p) {
        markdownFiles[p.path] = true;
        routes['projects/' + p.name + '/'] = p.path;
        p.files.forEach(function (f) { markdownFiles[f.path] = true; routes[routeFor(f.path).slice(base.length)] = f.path; });
      });
      window.addEventListener('popstate', route);
      route();
    }, function (error) {
      root.textContent = 'This site could not load its data: ' + error.message;
    });
})();
