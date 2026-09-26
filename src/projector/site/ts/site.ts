// Projector site: three sections, drawn from site.json. Projects lists the projects
// and renders each one's files beside a sidebar of its folder; Reviews lists the
// pull request summaries; Docs renders the README and everything else under
// docs/ beside a sidebar of that tree. A page is Markdown, rendered here, or HTML,
// shown as it is in a frame. Every view has a real path under the site's base;
// links between views update the address without a page load.
(function (): void {
  type Section = 'projects' | 'reviews' | 'docs' | 'search' | '';
  type Body = string | Node;

  /** A page in a sidebar's tree. */
  interface Item {
    path: string;
    title: string;
  }

  interface TreeNode {
    dirs: Record<string, TreeNode>;
    files: Item[];
    index: Item | null;
  }

  const STATUS_ORDER = ['in-progress', 'ready', 'draft', 'completed'];
  const PRIORITY_ORDER = ['now', 'next', 'later'];
  // Set once site.json loads, before any view draws.
  let site!: SiteData;
  const pageFiles = new Set<string>();
  let fitFrame: (() => void) | null = null;
  const siteFiles = new Set<string>();
  const routes: Record<string, string> = {};
  let searchIndex: SearchEntry[] | null = null;
  const root = document.getElementById('site');
  // A review page draws its own layout and carries only this bar.
  const bar = document.getElementById('sitebar');
  const host = root ?? bar;
  if (host === null) return;
  const baseAttr = host.dataset.base;
  const base = baseAttr !== undefined && baseAttr !== '' ? baseAttr : '/';

  function esc(s: string | number | null | undefined): string {
    return (s == null ? '' : String(s)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }
  function repoUrl(kind = '', path = ''): string {
    return `https://github.com/${site.repo}${path !== '' ? `/${kind}/${encodeURIComponent(site.branch)}/${path.split('/').map(encodeURIComponent).join('/')}` : ''}`;
  }
  function contentUrl(path: string): string {
    return `${base}${site.content}/${path.split('/').map(encodeURIComponent).join('/')}`;
  }

  function inProjects(path: string): boolean {
    return site.projectsDir !== null && path.startsWith(`${site.projectsDir}/`);
  }
  // The build gives every document and project file its route in site.json, so a
  // file and a folder of the same name never share one.
  function routeFor(path: string): string {
    if (path === site.readme) return `${base}docs/`;
    if (path === site.projectsReadme) return `${base}projects/`;
    const doc = site.docs.find(function (d) { return d.path === path; });
    if (doc !== undefined) return base + doc.route;
    for (const p of site.projects) {
      if (p.path === path) return `${base}projects/${p.name}/`;
      const file = p.files.find(function (f) { return f.path === path; });
      if (file !== undefined) return base + file.route;
    }
    return repoUrl('blob', path);
  }

  function folderOf(project: SiteProject): string { return project.path.slice(0, project.path.lastIndexOf('/') + 1); }
  function projectNamed(name: string): SiteProject | null {
    return site.projects.find(function (p) { return p.name === name; }) ?? null;
  }
  // The deepest project whose folder holds the file.
  function ownerOf(path: string): SiteProject | null {
    let best: SiteProject | null = null;
    for (const p of site.projects) {
      if (path.startsWith(folderOf(p)) && (best === null || p.name.length > best.name.length)) best = p;
    }
    return best;
  }

  // A path relative to the document at `from`, resolved to a repository path.
  function resolve(from: string, href: string): string {
    const parts = from.split('/').slice(0, -1);
    href.split('/').forEach(function (part) {
      if (part === '..') parts.pop();
      else if (part !== '' && part !== '.') parts.push(part);
    });
    return parts.join('/');
  }

  function stripFrontmatter(text: string): string {
    const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(text);
    return match !== null ? text.slice(match[0].length) : text;
  }

  function slug(text: string): string {
    return text.toLowerCase().trim().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-');
  }

  // Render Markdown from the repository file at `path`: sanitize it, give headings
  // ids, keep links between copied documents inside the site, and send every
  // other repository link to GitHub.
  function renderMarkdown(text: string, path: string): HTMLElement {
    const box = document.createElement('div');
    box.className = 'markdown';
    box.innerHTML = window.DOMPurify.sanitize(window.marked.parse(stripFrontmatter(text)));
    box.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(function (h) {
      if (h.id === '') h.id = slug(h.textContent);
    });
    box.querySelectorAll('a[href]').forEach(function (a) {
      const href = a.getAttribute('href') ?? '';
      if (href.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('/')) return;
      const hashAt = href.indexOf('#');
      const hash = hashAt >= 0 ? href.slice(hashAt) : '';
      const target = resolve(path, hashAt >= 0 ? href.slice(0, hashAt) : href);
      if (pageFiles.has(target)) a.setAttribute('href', routeFor(target) + hash);
      else if (siteFiles.has(target)) a.setAttribute('href', contentUrl(target));
      else a.setAttribute('href', repoUrl('blob', target));
    });
    box.querySelectorAll('img[src]').forEach(function (img) {
      const src = img.getAttribute('src') ?? '';
      if (/^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith('/')) return;
      const target = resolve(path, src);
      img.setAttribute('src', siteFiles.has(target) ? contentUrl(target) : repoUrl('raw', target));
    });
    const hljs = window.hljs;
    if (hljs !== undefined) box.querySelectorAll('pre code').forEach(function (code) { hljs.highlightElement(code); });
    return box;
  }

  function badge(kind: string, value: string | null | undefined): string {
    return value != null && value !== '' ? `<span class="badge ${kind}-${esc(value)}">${esc(value)}</span>` : '';
  }

  function nav(active: Section): string {
    const items: [string, string, Section][] = [];
    if (site.projects.length > 0) items.push([base, 'Projects', 'projects']);
    if (site.reviews.length > 0) items.push([`${base}reviews/`, 'Reviews', 'reviews']);
    if (site.readme !== null || site.docs.length > 0) items.push([`${base}docs/`, 'Docs', 'docs']);
    return items.map(function (item) {
      return `<a href="${esc(item[0])}"${item[2] === active ? ' class="active" aria-current="page"' : ''}>${esc(item[1])}</a>`;
    }).join('');
  }

  function header(active: Section, extra?: string): string {
    return `<header class="sitebar${extra !== undefined && extra !== '' ? ` ${extra}` : ''}"><a class="sitename" href="${esc(base)}">` +
      `${esc(site.repo !== '' ? site.repo : 'Projector')}</a><nav class="sitenav" aria-label="Site">${nav(active)}</nav>` +
      `<form class="sitesearch" role="search" action="${esc(`${base}search/`)}">` +
      '<input type="search" name="q" placeholder="Search" aria-label="Search the site"></form></header>';
  }

  function isHtml(path: string): boolean { return /\.html$/i.test(path); }

  // Draw the page around `body`, with `side` as a sidebar when given, and return
  // the element the body went into. A wide page, an HTML page's frame, takes the
  // whole width and starts with its sidebar collapsed behind the toggle.
  function frame(active: Section, title: string, body: Body, side?: string | null, wide?: boolean): HTMLElement {
    if (root === null) throw new Error('A view needs the #site element to draw into');
    document.title = title !== '' ? `${title} · ${site.repo}` : site.repo;
    fitFrame = null;
    const hasSide = side != null && side !== '';
    const isWide = wide === true;
    const classes = `sitemain${hasSide ? ' withside' : ''}${isWide ? ' wide' : ''}${hasSide && isWide ? ' collapsed' : ''}`;
    root.innerHTML =
      header(active) +
      `<main class="${classes}">` +
      (hasSide ? `<nav class="side" aria-label="Section"><button type="button" class="sidetoggle" aria-expanded="${String(!isWide)}" ` +
        `title="Show or hide this section's pages">☰</button><div class="sidetree">${side}</div></nav>` : '') +
      '<div class="sidebody" id="sidebody"></div></main>' +
      '<footer class="sitefoot">Built by <a href="https://github.com/ninjudd/projector">Projector</a> from ' +
      `<a href="${esc(repoUrl())}">${esc(site.repo)}</a>.</footer>`;
    const main = document.getElementById('sidebody');
    if (main === null) throw new Error('The page lost the #sidebody element it just drew');
    if (typeof body === 'string') main.innerHTML = body;
    else main.appendChild(body);
    return main;
  }

  // A nested list of `items`, each a {path, title} under `prefix`, with `top` as its
  // heading. A folder holding a readme is labelled by it; any other folder by its name.
  function tree(items: Item[], prefix: string, top: Item | null, current: string | null): string {
    const rootNode: TreeNode = { dirs: {}, files: [], index: null };
    items.forEach(function (item) {
      const parts = item.path.slice(prefix.length).split('/');
      const name = parts.pop() ?? '';
      let node = rootNode;
      for (const dir of parts) {
        node = node.dirs[dir] = node.dirs[dir] ?? { dirs: {}, files: [], index: null };
      }
      if (node !== rootNode && /^readme\.md$/i.test(name)) node.index = item;
      else node.files.push(item);
    });
    function link(item: Item): string {
      return `<a href="${esc(routeFor(item.path))}"` +
        `${item.path === current ? ' class="active" aria-current="page"' : ''}>${esc(item.title)}</a>`;
    }
    function render(node: TreeNode): string {
      const files = node.files.slice().sort(function (a, b) { return a.title.localeCompare(b.title); })
        .map(function (f) { return `<li>${link(f)}</li>`; });
      const dirs = Object.keys(node.dirs).sort().map(function (name) {
        const dir = node.dirs[name];
        if (dir === undefined) return '';
        return `<li>${dir.index !== null ? link(dir.index) : `<span class="sidedir">${esc(name)}</span>`}${render(dir)}</li>`;
      });
      const all = files.concat(dirs);
      return all.length > 0 ? `<ul>${all.join('')}</ul>` : '';
    }
    return (top !== null ? `<div class="sidetop">${link(top)}</div>` : '') + render(rootNode);
  }

  function loadMarkdown(path: string): Promise<string> {
    return fetch(contentUrl(path)).then(function (response) {
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
      return response.text();
    });
  }

  // An HTML page as it is, in a frame of its copy under content/, so the files it
  // loads by relative path resolve beside it. The address's hash passes into the
  // frame and follows it back out, so a deep link into a hash-routed page works.
  function showHtml(active: Section, path: string, title: string, before: Node | null, side: string | null): void {
    const main = frame(active, title, '', side, true);
    if (before !== null) main.appendChild(before);
    const src = contentUrl(path) + location.hash;
    const bar = document.createElement('div');
    bar.className = 'htmlbar';
    bar.innerHTML = `<a class="note" href="${esc(src)}" target="_blank" rel="noopener">Open full page</a>`;
    const page = document.createElement('iframe');
    page.className = 'htmlpage';
    page.title = title;
    page.src = src;
    main.appendChild(bar);
    main.appendChild(page);
    fitFrame = function (): void {
      page.style.height = `${String(Math.max(320, window.innerHeight - page.getBoundingClientRect().top - 16))}px`;
    };
    fitFrame();
    page.addEventListener('load', function () {
      try {
        const inner = page.contentWindow;
        if (inner === null) return;
        if (inner.document.title !== '') document.title = `${inner.document.title} · ${site.repo}`;
        inner.addEventListener('hashchange', function () {
          history.replaceState(null, '', location.pathname + location.search + inner.location.hash);
          (bar.firstChild as Element).setAttribute('href', contentUrl(path) + inner.location.hash);
        });
      } catch { /* a page that navigated elsewhere is no longer ours to read */ }
    });
  }

  function showPage(active: Section, path: string, title: string, before: Node | null, side: string | null): void {
    if (isHtml(path)) showHtml(active, path, title, before, side);
    else showDocument(active, path, title, before, side);
  }

  function showDocument(active: Section, path: string, title: string, before: Node | null, side: string | null): void {
    frame(active, title, '<p class="note">Loading…</p>', side);
    loadMarkdown(path).then(function (text) {
      const main = frame(active, title, renderMarkdown(text, path), side);
      if (before !== null) main.insertBefore(before, main.firstChild);
      const target = location.hash !== '' ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
      if (target !== null) target.scrollIntoView();
    }, function (error: unknown) {
      frame(active, title, `<p class="note">Could not load ${esc(path)}: ${esc(errorMessage(error))}</p>`, side);
    });
  }

  // The README, or any document under docs/, beside the docs tree when there is
  // more than the README to list. The README's entry is Overview, the page the
  // tree starts from, rather than a title the README's own heading repeats.
  function showDocs(path: string | null): void {
    const top = site.readme !== null ? { path: site.readme, title: 'Overview' } : null;
    const side = site.docs.length > 0 ? tree(site.docs, 'docs/', top, path) : null;
    if (path === null) {
      frame('docs', 'Docs', '<h1>Docs</h1><p class="note">This repository has no README.md.</p>', side);
      return;
    }
    const doc = site.docs.find(function (d) { return d.path === path; });
    showPage('docs', path, doc !== undefined ? doc.title : 'Docs', null, side);
  }

  function depth(name: string): number { return name.split('/').length - 1; }

  function showProjects(): void {
    const groups = STATUS_ORDER.map(function (status) {
      const rows = site.projects.filter(function (p) { return p.status === status; });
      rows.sort(function (a, b) {
        const pa = PRIORITY_ORDER.indexOf(a.priority ?? ''), pb = PRIORITY_ORDER.indexOf(b.priority ?? '');
        return (pa < 0 ? 9 : pa) - (pb < 0 ? 9 : pb) || a.name.localeCompare(b.name);
      });
      if (rows.length === 0) return '';
      return `<section class="pgroup"><h2>${esc(status)} <span class="count">${String(rows.length)}</span></h2>` +
        '<div class="tblwrap"><table class="tbl projects"><tr><th>Project</th><th>Priority</th><th>Name</th></tr>' +
        rows.map(function (p) {
          return `<tr data-search="${esc(`${p.name} ${p.title}`.toLowerCase())}">` +
            `<td style="padding-left:${String(0.75 + depth(p.name) * 1.25)}rem"><a href="${esc(`${base}projects/${p.name}/`)}">${esc(p.title)}</a></td>` +
            `<td>${badge('priority', p.priority)}</td><td class="mono">${esc(p.name)}</td></tr>`;
        }).join('') + '</table></div></section>';
    }).join('');
    const main = frame('projects', 'Projects',
      `<h1>Projects</h1><p class="note">${String(site.projects.length)} projects under <span class="mono">${esc(site.projectsDir)}</span>. ` +
      'Status says how finished a project is; priority says when it is scheduled.</p>' +
      '<input class="filter" type="search" placeholder="Filter projects" aria-label="Filter projects">' + groups);
    const filter = main.querySelector('.filter');
    if (filter === null) throw new Error('The projects view lost the filter it just drew');
    filter.addEventListener('input', function (event) {
      const q = (event.target as HTMLInputElement).value.toLowerCase();
      main.querySelectorAll<HTMLElement>('tr[data-search]').forEach(function (row) {
        row.hidden = q !== '' && !(row.dataset.search ?? '').includes(q);
      });
    });
    const intro = site.projectsReadme;
    if (intro !== null) {
      loadMarkdown(intro).then(function (text) {
        const details = document.createElement('details');
        details.className = 'pintro';
        details.innerHTML = '<summary>How projects work</summary>';
        details.appendChild(renderMarkdown(text, intro));
        main.insertBefore(details, filter);
      }, function () { /* the list reads fine without the introduction */ });
    }
  }

  // Any file of a project, its readme or a supplemental file, beside a sidebar of
  // its top-level project's folder: every file, subdirectory, and nested project.
  function showProjectFile(path: string): void {
    const project = ownerOf(path);
    if (project === null) {
      frame('projects', 'Not found', `<h1>Not found</h1><p><a href="${esc(base)}">All projects</a></p>`);
      return;
    }
    const parts = project.name.split('/');
    const top = projectNamed(parts[0] ?? project.name) ?? project;
    const prefix = folderOf(top);
    const items: Item[] = [];
    site.projects.forEach(function (p) {
      if (p === top || !p.path.startsWith(prefix)) return;
      items.push({ path: p.path, title: p.title });
    });
    site.projects.forEach(function (p) {
      if (p.path.startsWith(prefix)) p.files.forEach(function (f) { items.push(f); });
    });
    // A project with nothing but its readme needs no sidebar. The top-level
    // project's readme is Overview, the page the tree starts from: on that page
    // its title would repeat the heading, and on every other page of the
    // project the breadcrumbs already name the project.
    const side = items.length > 0 ? tree(items, prefix, { path: top.path, title: 'Overview' }, path) : null;

    const isReadme = path === project.path;
    const file = project.files.find(function (f) { return f.path === path; });
    const leaf = path.slice(path.lastIndexOf('/') + 1);
    const crumbs = `<a href="${esc(base)}">Projects</a>` + parts.map(function (part, i) {
      const name = parts.slice(0, i + 1).join('/');
      const here = isReadme && i === parts.length - 1;
      return ` / ${here ? `<span class="mono">${esc(part)}</span>` : `<a href="${esc(`${base}projects/${name}/`)}">${esc(part)}</a>`}`;
    }).join('') + (isReadme ? '' : ` / <span class="mono">${esc(leaf)}</span>`);
    const head = document.createElement('div');
    head.className = 'phead';
    head.innerHTML = `<div class="crumbs">${crumbs}</div>` +
      (isReadme ? `<div class="pmeta">${badge('status', project.status)}${badge('priority', project.priority)}` +
        (project.owner != null && project.owner !== '' ? `<span class="note">Owner ${esc(project.owner)}</span>` : '') +
        `<a class="note" href="${esc(repoUrl('blob', project.path))}">View on GitHub</a></div>` +
        (project.reviews.length > 0 ? '<div class="plist"><b>Reviews</b> ' + project.reviews.map(function (n) {
          const r = reviewFor(n);
          return `<a href="${esc(`${base}reviews/${String(n)}/`)}">#${String(n)}${r !== null ? ` ${esc(r.name !== '' ? r.name : r.title)}` : ''}</a>`;
        }).join(' · ') + '</div>' : '')
        : `<div class="pmeta"><a class="note" href="${esc(repoUrl('blob', path))}">View on GitHub</a></div>`);
    showPage('projects', path, isReadme ? project.title : (file !== undefined ? file.title : (leaf !== '' ? leaf : path)), head, side);
  }

  function reviewFor(number: number): SiteReview | null {
    return site.reviews.find(function (r) { return r.number === number; }) ?? null;
  }

  function projectLinks(names: string[]): string {
    return names.map(function (name) {
      const project = projectNamed(name);
      return `<a href="${esc(`${base}projects/${name}/`)}">${esc(project !== null ? project.title : name)}</a>`;
    }).join('<br>');
  }

  function showReviews(): void {
    frame('reviews', 'Reviews',
      '<h1>Reviews</h1>' +
      '<div class="tblwrap"><table class="tbl"><tr><th>#</th><th>Review</th><th>Projects</th><th>Head</th><th>Versions</th><th>Updated</th></tr>' +
      site.reviews.map(function (r) {
        const url = esc(`${base}reviews/${String(r.number)}/`);
        return `<tr><td><a href="${url}">#${String(r.number)}</a></td><td><a href="${url}">${esc(r.name !== '' ? r.name : r.title)}</a>` +
          `<div class="note">${esc(r.title)}</div></td><td>${projectLinks(r.projects)}</td>` +
          `<td class="mono">${esc(r.head.slice(0, 9))}</td>` +
          `<td>${String(r.heads)}</td><td>${esc(r.updated)}</td></tr>`;
      }).join('') + '</table></div>' +
      '<p class="note">Built by Projector\'s <span class="mono">summarize-changes</span> skill. Each link opens the newest version; older heads are listed in its sidebar.</p>');
  }

  // Centered on the whole query where the text holds it, else on the earliest word.
  function snippet(text: string, words: string[]): string {
    const lower = text.toLowerCase();
    let at = lower.indexOf(words.join(' '));
    if (at < 0) at = words.reduce(function (first, w) { const i = lower.indexOf(w); return i >= 0 && (first < 0 || i < first) ? i : first; }, -1);
    const start = Math.max(0, at - 60);
    const piece = (start > 0 ? '…' : '') + text.slice(start, start + 200) + (start + 200 < text.length ? '…' : '');
    return highlight(piece, words);
  }

  // One pass over the raw text, longest word first, so a later word never
  // matches inside markup or an entity an earlier one produced.
  function highlight(text: string, words: string[]): string {
    const pattern = words.slice().sort(function (a, b) { return b.length - a.length; })
      .map(function (w) { return w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|');
    return text.split(new RegExp(`(${pattern})`, 'i')).map(function (part, i) {
      return i % 2 === 1 ? `<mark>${esc(part)}</mark>` : esc(part);
    }).join('');
  }

  // Every word must appear; a word in the title counts more than one in the text.
  function search(query: string, index: SearchEntry[]): string[] {
    const words = query.toLowerCase().split(/\s+/).filter(function (w) { return w !== ''; });
    if (words.length === 0) return [];
    const scored: { entry: SearchEntry; score: number }[] = [];
    index.forEach(function (entry) {
      const title = entry.title.toLowerCase(), text = entry.text.toLowerCase();
      let score = 0;
      for (const word of words) {
        const inTitle = title.includes(word), inText = text.includes(word);
        if (!inTitle && !inText) return;
        score += (inTitle ? 10 : 0) + text.split(word).length - 1;
      }
      scored.push({ entry: entry, score: score });
    });
    return scored.sort(function (a, b) { return b.score - a.score; }).map(function (r) {
      return `<li><a href="${esc(base + r.entry.route)}">${esc(r.entry.title)}</a> <span class="note">${esc(r.entry.kind)}</span>` +
        `<div class="snippet">${snippet(r.entry.text, words)}</div></li>`;
    });
  }

  function showSearch(query: string): void {
    const main = frame('search', query !== '' ? `Search: ${query}` : 'Search',
      '<h1>Search</h1><input class="filter" type="search" aria-label="Search the site" placeholder="Search the projects and docs">' +
      '<ol class="results"></ol>');
    const input = main.querySelector<HTMLInputElement>('.filter');
    const list = main.querySelector<HTMLElement>('.results');
    if (input === null || list === null) throw new Error('The search view lost the elements it just drew');
    input.value = query;
    function render(): void {
      if (input === null || list === null) return;
      const q = input.value.trim();
      history.replaceState(null, '', `${base}search/${q !== '' ? `?q=${encodeURIComponent(q)}` : ''}`);
      const results = q !== '' && searchIndex !== null ? search(q, searchIndex) : [];
      list.innerHTML = results.length > 0 ? results.join('') : (q !== '' ? '<p class="note">Nothing matches every word.</p>' : '');
    }
    input.addEventListener('input', render);
    input.focus();
    if (searchIndex !== null) {
      render();
      return;
    }
    list.innerHTML = '<p class="note">Loading…</p>';
    fetch(`${base}search.json`).then(function (r) { return r.json() as Promise<SearchEntry[]>; }).then(function (data) {
      searchIndex = data;
      render();
    }, function (error: unknown) {
      list.innerHTML = `<p class="note">Could not load the search index: ${esc(errorMessage(error))}</p>`;
    });
  }

  function route(): void {
    const path = location.pathname;
    let rel = path.startsWith(base) ? path.slice(base.length) : path.replace(/^\//, '');
    rel = decodeURIComponent(rel).replace(/index\.html$/, '').replace(/\/$/, '');
    window.scrollTo(0, 0);
    switch (rel) {
      case '':
        // A repository with no projects opens on its docs instead.
        if (site.projects.length > 0) showProjects();
        else showDocs(site.readme);
        return;
      case 'projects': showProjects(); return;
      case 'reviews': showReviews(); return;
      case 'docs': showDocs(site.readme); return;
      case 'search': showSearch(new URLSearchParams(location.search).get('q') ?? ''); return;
    }
    const file = routes[`${rel}/`];
    if (file !== undefined) {
      if (inProjects(file)) showProjectFile(file);
      else showDocs(file);
      return;
    }
    frame('', 'Not found', `<h1>Not found</h1><p><a href="${esc(base)}">Home</a></p>`);
  }

  // A link to another view of this site renders in place; a review page, an asset,
  // or anything outside the site loads normally.
  function inSite(a: HTMLAnchorElement): boolean {
    if (a.target !== '' || a.hasAttribute('download') || a.origin !== location.origin) return false;
    const path = a.pathname;
    if (!path.startsWith(base)) return false;
    const rel = path.slice(base.length);
    return !/^(reviews\/\d|assets\/|content\/)/.test(rel) && !/\.[a-z0-9]+$/i.test(rel);
  }

  document.addEventListener('click', function (event) {
    const target = event.target instanceof Element ? event.target : null;
    const toggle = target?.closest('.sidetoggle');
    if (toggle != null) {
      const main = toggle.closest('.sitemain');
      if (main === null) throw new Error('A section toggle sits outside the page it toggles');
      const collapsed = main.classList.toggle('collapsed');
      toggle.setAttribute('aria-expanded', String(!collapsed));
      if (fitFrame !== null) fitFrame();
      return;
    }
    if (root === null || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const a = target?.closest<HTMLAnchorElement>('a[href]');
    if (a == null || !inSite(a)) return;
    if (a.pathname === location.pathname && a.hash !== '') return;
    event.preventDefault();
    history.pushState(null, '', a.href);
    route();
  });

  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (root === null || !(form instanceof HTMLFormElement) || !form.classList.contains('sitesearch')) return;
    event.preventDefault();
    const input = form.querySelector('input');
    if (input === null) throw new Error('The search form has no input');
    const q = input.value.trim();
    history.pushState(null, '', `${base}search/${q !== '' ? `?q=${encodeURIComponent(q)}` : ''}`);
    route();
  });

  fetch(`${base}site.json`)
    .then(function (response) {
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
      return response.json() as Promise<SiteData>;
    })
    .then(function (data) {
      site = data;
      if (root === null) {
        host.outerHTML = header('reviews', 'scrolls');
        return;
      }
      site.files.forEach(function (f) { siteFiles.add(f); });
      if (site.readme !== null) pageFiles.add(site.readme);
      if (site.projectsReadme !== null) pageFiles.add(site.projectsReadme);
      site.docs.forEach(function (d) { pageFiles.add(d.path); routes[d.route] = d.path; });
      site.projects.forEach(function (p) {
        pageFiles.add(p.path);
        routes[`projects/${p.name}/`] = p.path;
        p.files.forEach(function (f) { pageFiles.add(f.path); routes[f.route] = f.path; });
      });
      window.addEventListener('popstate', route);
      window.addEventListener('resize', function () { if (fitFrame !== null) fitFrame(); });
      route();
    }, function (error: unknown) {
      host.textContent = `This site could not load its data: ${errorMessage(error)}`;
    });
})();
