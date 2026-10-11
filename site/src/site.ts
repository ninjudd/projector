// Projector site: three sections, drawn from site.json. Projects lists the projects
// and renders each one's files in a summary page's layout, beside a summary's
// sidebar of its stacks, sections, and pages; Reviews lists the pull request
// summaries; Docs renders the README and everything else under docs/ beside a
// sidebar of that tree. A page is Markdown, rendered here, or HTML, shown as it is in a frame.
// Every view has a real path under the site's base; links between views update
// the address without a page load. Every page loads summary.js first, for the
// helpers a summary page draws with too.
(function (): void {
  type Section = 'projects' | 'reviews' | 'docs' | 'search' | '';
  type Body = string | Node;
  /** A page's sidebar, drawn once the page's sections are known. */
  type SideNavOf = (sections: PageSection[]) => string;

  /** A page in a sidebar's tree, and for a proposed project's plan the pull request that proposes it. */
  interface Item {
    path: string;
    title: string;
    proposed?: number | undefined;
  }

  interface TreeNode {
    dirs: Record<string, TreeNode>;
    files: Item[];
    index: Item | null;
  }

  const STATUS_ORDER = ['in-progress', 'ready', 'draft', 'completed'];
  const PRIORITY_ORDER = ['now', 'next', 'later'];
  // What the site calls a project whose plan an open pull request adds.
  const PROPOSED = 'Proposed';
  // Projector's mark, as projector.bot draws it: an orange beam from a lens.
  const MARK = '<svg class="sitemark" viewBox="0 0 32 32" aria-hidden="true">' +
    '<path d="M7 16 L29 5.5 V26.5 Z" fill="var(--brand)"/><circle cx="7" cy="16" r="4.5" fill="currentColor"/></svg>';
  // The header's icon buttons: the section menu, and entering and leaving full screen.
  const MENU_ICON = `${ICON}<path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13"/></svg>`;
  const FULL_ICON = `${ICON}<path d="M3.5 7.5v-4h4M12.5 3.5h4v4M16.5 12.5v4h-4M7.5 16.5h-4v-4"/></svg>`;
  const EXIT_FULL_ICON = `${ICON}<path d="M7.5 3.5v4h-4M16.5 7.5h-4v-4M12.5 16.5v-4h4M3.5 12.5h4v4"/></svg>`;
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

  function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }
  // A repository file on GitHub at `ref`: the site's branch, or a proposed project's head.
  function repoUrl(kind: string, path: string, ref: string = site.branch): string {
    return `https://github.com/${site.repo}/${kind}/${encodeURIComponent(ref)}/${path.split('/').map(encodeURIComponent).join('/')}`;
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
  // other repository link to GitHub, at `ref`.
  function renderMarkdown(text: string, path: string, ref: string = site.branch): HTMLElement {
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
      else a.setAttribute('href', repoUrl('blob', target, ref));
    });
    box.querySelectorAll('img[src]').forEach(function (img) {
      const src = img.getAttribute('src') ?? '';
      if (/^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith('/')) return;
      const target = resolve(path, src);
      img.setAttribute('src', siteFiles.has(target) ? contentUrl(target) : repoUrl('raw', target, ref));
    });
    const hljs = window.hljs;
    if (hljs !== undefined) box.querySelectorAll('pre code').forEach(function (code) { hljs.highlightElement(code); });
    return box;
  }

  function badge(kind: string, value: string | null | undefined): string {
    return value != null && value !== '' ? `<span class="badge ${kind}-${esc(value)}">${esc(value)}</span>` : '';
  }

  // The badge of a project an open pull request proposes, linking that pull request's review.
  function proposedBadge(number: number | undefined): string {
    return number !== undefined
      ? `<a class="badge proposed" href="${esc(`${base}reviews/${String(number)}/`)}">${PROPOSED} #${String(number)}</a>`
      : '';
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

  // The site's header. A page with a section sidebar gets the button that shows
  // and hides it at the header's start, and any other page an empty slot in its
  // place, so the site name sits in the same spot on every page. A wide page, an
  // HTML page's frame, gets a full-screen button at its end, which hides the
  // header so the page has the whole window.
  function header(active: Section, extra?: string, side?: 'open' | 'closed', wide?: boolean): string {
    const menu = side !== undefined
      ? `<button type="button" class="sitebtn sidetoggle" aria-controls="siteside" aria-expanded="${String(side === 'open')}" ` +
        `title="Show or hide this section's pages" aria-label="Show or hide this section's pages">${MENU_ICON}</button>`
      : '<span class="sitebtn menuspace" aria-hidden="true"></span>';
    const hide = wide === true
      ? `<button type="button" class="sitebtn barhide" title="Full screen" aria-label="Full screen">${FULL_ICON}</button>`
      : '';
    return `<header class="sitebar${extra !== undefined && extra !== '' ? ` ${extra}` : ''}">${menu}<a class="sitename" href="${esc(base)}">` +
      `${MARK}${esc(site.repo !== '' ? site.repo : 'Projector')}</a><nav class="sitenav" aria-label="Site">${nav(active)}</nav>` +
      `<form class="sitesearch" role="search" action="${esc(`${base}search/`)}">` +
      `<input type="search" name="q" placeholder="Search" aria-label="Search the site"></form>${hide}</header>`;
  }

  function isHtml(path: string): boolean { return /\.html$/i.test(path); }

  // Draw the page around `body`, make its menus work, and return the element
  // the body went into.
  // `side`, a section's tree, is a column at the window's left edge. A page
  // with `top`, a title row, takes a summary page's layout instead: the title
  // row across the top, `nav`, a summary's sidebar, beside the body, and a
  // site bar that scrolls away. A wide page, an HTML page's frame, fills the
  // window below the header, starts with its column, `side` or else `nav`,
  // hidden, and can go full screen, hiding the header; a button pinned to the
  // corner leaves full screen.
  function frame(active: Section, title: string, body: Body, side?: string | null, wide?: boolean, top?: string,
    nav?: string): HTMLElement {
    if (root === null) throw new Error('A view needs the #site element to draw into');
    document.title = title !== '' ? `${title} · ${site.repo}` : site.repo;
    fitFrame = null;
    watchSections(null);
    const isWide = wide === true;
    if (!isWide) document.body.classList.remove('barhidden');
    if (top !== undefined) {
      root.innerHTML = header(active, 'scrolls') +
        `<main class="sitemain withnav"><div class="wrap">${top}<div class="layout"><aside class="side">${nav ?? ''}</aside>` +
        '<div class="sidebody" id="sidebody"></div></div></div></main>';
    } else {
      const column = side != null && side !== '' ? `<nav class="side" id="siteside" aria-label="Section">${side}</nav>`
        : nav !== undefined && nav !== '' ? `<aside class="side" id="siteside">${nav}</aside>` : '';
      const hasSide = column !== '';
      const classes = `sitemain${hasSide ? ' withside' : ''}${isWide ? ' wide' : ''}${hasSide && isWide ? ' collapsed' : ''}`;
      root.innerHTML =
        header(active, undefined, hasSide ? (isWide ? 'closed' : 'open') : undefined, isWide) +
        (isWide ? `<button type="button" class="sitebtn barshow" title="Exit full screen" aria-label="Exit full screen">${EXIT_FULL_ICON}</button>` : '') +
        `<main class="${classes}">${column}<div class="sidebody" id="sidebody"></div></main>` +
        (isWide ? '' : `<footer class="sitefoot">${creditHtml()}</footer>`);
    }
    const main = document.getElementById('sidebody');
    if (main === null) throw new Error('The page lost the #sidebody element it just drew');
    if (typeof body === 'string') main.innerHTML = body;
    else main.appendChild(body);
    wireMenus(root);
    return main;
  }

  // A nested list of `items`, each a {path, title} under `prefix`, with `top` as its
  // heading. A folder holding a readme is labelled by it; any other folder by its name.
  // The outer list is a `sidetree`, which its styles name, so they reach no other list.
  // `under` follows the current entry, inside its item.
  function tree(items: Item[], prefix: string, top: Item | null, current: string | null, under = ''): string {
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
    function entry(item: Item): string {
      const here = item.path === current;
      return `<a href="${esc(routeFor(item.path))}"${here ? ' class="active" aria-current="page"' : ''}>${esc(item.title)}</a>` +
        (item.proposed !== undefined ? ` ${proposedBadge(item.proposed)}` : '') + (here ? under : '');
    }
    function render(node: TreeNode): string {
      const files = node.files.slice().sort(function (a, b) { return a.title.localeCompare(b.title); })
        .map(function (f) { return `<li>${entry(f)}</li>`; });
      const dirs = Object.keys(node.dirs).sort().map(function (name) {
        const dir = node.dirs[name];
        if (dir === undefined) return '';
        return `<li>${dir.index !== null ? entry(dir.index) : `<span class="sidedir">${esc(name)}</span>`}${render(dir)}</li>`;
      });
      const all = files.concat(dirs);
      return all.length > 0 ? `<ul${node === rootNode ? ' class="sidetree"' : ''}>${all.join('')}</ul>` : '';
    }
    return (top !== null ? `<div class="sidetop">${entry(top)}</div>` : '') + render(rootNode);
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
  function showHtml(active: Section, path: string, title: string, before: Node | null, side: string | null,
    nav?: string): void {
    const main = frame(active, title, '', side, true, undefined, nav);
    if (before !== null) {
      main.appendChild(before);
      wireMenus(main);
    }
    const page = document.createElement('iframe');
    page.className = 'htmlpage';
    page.title = title;
    page.src = contentUrl(path) + location.hash;
    main.appendChild(page);
    fitFrame = function (): void {
      page.style.height = `${String(Math.max(240, window.innerHeight - page.getBoundingClientRect().top))}px`;
    };
    fitFrame();
    page.addEventListener('load', function () {
      try {
        const inner = page.contentWindow;
        if (inner === null) return;
        if (inner.document.title !== '') document.title = `${inner.document.title} · ${site.repo}`;
        inner.addEventListener('hashchange', function () {
          history.replaceState(null, '', location.pathname + location.search + inner.location.hash);
        });
      } catch { /* a page that navigated elsewhere is no longer ours to read */ }
    });
  }

  // `ref` is where a Markdown page's links to repository files the site lacks
  // point on GitHub. `top` and `nav` give a Markdown page a summary page's
  // layout, and `nav` is an HTML page's column, as `frame` draws them. The
  // site sees no sections in an HTML page.
  function showPage(active: Section, path: string, title: string, before: Node | null, side: string | null,
    ref: string = site.branch, top?: string, nav?: SideNavOf): void {
    if (isHtml(path)) showHtml(active, path, title, before, side, nav?.([]));
    else showDocument(active, path, title, before, side, ref, top, nav);
  }

  // Under a title row, which shows the page's title, the body leaves out its
  // own when that is its first element. The sidebar lists the sections of the
  // page as it renders, and marks the one in view.
  function showDocument(active: Section, path: string, title: string, before: Node | null, side: string | null,
    ref: string, top?: string, nav?: SideNavOf): void {
    frame(active, title, '<p class="note">Loading…</p>', side, false, top, nav?.([]));
    loadMarkdown(path).then(function (text) {
      const box = renderMarkdown(text, path, ref);
      if (top !== undefined && box.firstElementChild?.tagName === 'H1') box.firstElementChild.remove();
      const main = frame(active, title, box, side, false, top, nav?.(sectionsOf(box)));
      if (before !== null) main.insertBefore(before, main.firstChild);
      const target = location.hash !== '' ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
      if (target !== null) target.scrollIntoView();
      watchSections(document.querySelector('.layout > .side ol.sections'));
    }, function (error: unknown) {
      frame(active, title, `<p class="note">Could not load ${esc(path)}: ${esc(errorMessage(error))}</p>`, side, false, top,
        nav?.([]));
    });
  }

  // A rendered page's sections: its `##` headings at the top level of the
  // page. A heading that opens with its number, as `2. Solution` and
  // `2.1 Detail` do, gives the number a column of its own, without its last
  // dot. The title keeps its inline markup but not its links, because the
  // sidebar's row is itself a link.
  function sectionsOf(box: HTMLElement): PageSection[] {
    return Array.from(box.children).flatMap(function (h): PageSection[] {
      if (h.tagName !== 'H2' || h.id === '') return [];
      const unlinked = h.cloneNode(true) as Element;
      unlinked.querySelectorAll('a').forEach(function (a) { a.replaceWith(...Array.from(a.childNodes)); });
      const title = unlinked.innerHTML;
      const number = /^(?=\d+\.)(\d+(?:\.\d+)*)\.?\s+/.exec(h.textContent);
      return number !== null && title.startsWith(number[0])
        ? [{ id: h.id, num: number[1] ?? '', title: title.slice(number[0].length) }]
        : [{ id: h.id, num: '', title: title }];
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

  function ownerName(p: SiteProject): string { return p.owner ?? ''; }

  // One table for each status. The Owner column appears only in a repository
  // whose plans name owners. Every column but the project's has a set width,
  // the same in every table, so the columns line up from one status to the next.
  function showProjects(): void {
    const owners = site.projects.some(function (p) { return ownerName(p) !== ''; });
    const proposed = site.projects.filter(function (p) { return p.proposed !== undefined; }).length;
    const groups = STATUS_ORDER.map(function (status) {
      const rows = site.projects.filter(function (p) { return p.status === status; });
      rows.sort(function (a, b) {
        const pa = PRIORITY_ORDER.indexOf(a.priority ?? ''), pb = PRIORITY_ORDER.indexOf(b.priority ?? '');
        return (pa < 0 ? 9 : pa) - (pb < 0 ? 9 : pb) || a.name.localeCompare(b.name);
      });
      if (rows.length === 0) return '';
      return `<section class="pgroup"><h2>${esc(status)} <span class="count">${String(rows.length)}</span></h2>` +
        '<div class="tblwrap"><table class="tbl projects">' +
        `<colgroup><col><col class="priority">${owners ? '<col class="owner">' : ''}<col class="name"></colgroup>` +
        `<thead><tr><th>Project</th><th>Priority</th>${owners ? '<th>Owner</th>' : ''}<th class="name">Name</th></tr></thead><tbody>` +
        rows.map(function (p) {
          const search = `${p.name} ${p.title} ${ownerName(p)}${p.proposed !== undefined ? ` ${PROPOSED}` : ''}`;
          return `<tr data-search="${esc(search.toLowerCase())}">` +
            `<td style="padding-left:${String(0.75 + depth(p.name) * 1.25)}rem"><a href="${esc(`${base}projects/${p.name}/`)}">${esc(p.title)}</a>` +
            `${p.proposed !== undefined ? ` ${proposedBadge(p.proposed.number)}` : ''}</td>` +
            `<td>${badge('priority', p.priority)}</td>${owners ? `<td class="owner">${esc(ownerName(p))}</td>` : ''}` +
            `<td class="mono name">${esc(p.name)}</td></tr>`;
        }).join('') + '</tbody></table></div></section>';
    }).join('');
    const main = frame('projects', 'Projects',
      `<h1>Projects</h1><p class="note">${String(site.projects.length)} projects under <span class="mono">${esc(site.projectsDir)}</span>` +
      `${proposed > 0 ? `, ${String(proposed)} of them proposed in open pull requests` : ''}. ` +
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

  // Any file of a project, its readme or a supplemental file, in a summary
  // page's layout: a title row of the breadcrumbs and the readme's badges and
  // owner over the page's title, with a "…" menu of the file's links to
  // GitHub at its end, and the project's sidebar beside the page. An HTML
  // page keeps its frame, with the sidebar in its column and the breadcrumbs
  // and menu above it. A proposed project's banner opens every page.
  function showProjectFile(path: string): void {
    const project = ownerOf(path);
    if (project === null) {
      frame('projects', 'Not found', `<h1>Not found</h1><p><a href="${esc(base)}">All projects</a></p>`);
      return;
    }
    const parts = project.name.split('/');
    const isReadme = path === project.path;
    const file = project.files.find(function (f) { return f.path === path; });
    const leaf = path.slice(path.lastIndexOf('/') + 1);
    const title = isReadme ? project.title : (file !== undefined ? file.title : (leaf !== '' ? leaf : path));
    const ref = project.proposed?.head ?? site.branch;
    const crumbs = `<a href="${esc(base)}">Projects</a>` + parts.map(function (part, i) {
      const name = parts.slice(0, i + 1).join('/');
      const here = isReadme && i === parts.length - 1;
      return ` / ${here ? `<span class="mono">${esc(part)}</span>` : `<a href="${esc(`${base}projects/${name}/`)}">${esc(part)}</a>`}`;
    }).join('') + (isReadme ? '' : ` / <span class="mono">${esc(leaf)}</span>`);
    const menu = moreMenu('page-menu', 'Page links', [
      { href: repoUrl('blob', path, ref), text: 'View file on GitHub', short: 'File', external: true },
      { href: repoUrl('commits', path, ref), text: 'View history on GitHub', short: 'History', external: true },
    ]);
    const banner = project.proposed !== undefined ? proposalBanner(project.proposed) : '';
    const nav = function (sections: PageSection[]): string { return projectNav(project, path, sections); };
    if (isHtml(path)) {
      const head = document.createElement('div');
      head.className = 'phead';
      head.innerHTML = `<div class="crumbs"><span>${crumbs}</span>${menu}</div>${banner}`;
      showPage('projects', path, title, head, null, ref, undefined, nav);
      return;
    }
    const owner = project.owner != null && project.owner !== '' ? `Owner ${esc(project.owner)}` : '';
    const eyebrow = (isReadme ? [crumbs, badge('status', project.status) + badge('priority', project.priority), owner] : [crumbs])
      .filter(function (group) { return group !== ''; }).join(' · ');
    let before: HTMLElement | null = null;
    if (banner !== '') {
      before = document.createElement('div');
      before.innerHTML = banner;
    }
    showPage('projects', path, title, before, null, ref, titleRowHtml(eyebrow, title, menu), nav);
  }

  // A project's sidebar, drawn as a summary's is: the project's header once,
  // then every stack that holds a segment it heads, each whole but for this
  // project's headers, or a line saying it has none; then the readme's
  // `sections`; then the other pages under its folder, with another page's
  // sections under its entry; then a nested project's neighbors; then the
  // credit and its history on GitHub. On its readme, its header is the page
  // you are on, and on any other page it links to the readme and the page's
  // own entry in the pages is current.
  function projectNav(project: SiteProject, path: string, sections: PageSection[]): string {
    const isReadme = path === project.path;
    const ref = project.proposed?.head ?? site.branch;
    const stacks = project.stacks.map(function (segments) {
      const rows = segments.flatMap(function (s) { return s.members; }).map(function (n): StackRow {
        const r = reviewFor(n);
        return r !== null ? { number: n, title: r.title, url: `${base}reviews/${String(n)}/` }
          : { number: n, title: '', url: `https://github.com/${site.repo}/pull/${String(n)}` };
      });
      return { segments: segments, rows: rows };
    });
    const own: StackHeader = { project: project.name, title: project.title, url: `${base}projects/${project.name}/` };
    const folder = folderOf(project);
    const items: Item[] = [];
    site.projects.forEach(function (p) {
      if (p !== project && p.path.startsWith(folder)) items.push(itemOf(p));
    });
    site.projects.forEach(function (p) {
      if (p.path.startsWith(folder)) p.files.forEach(function (f) { items.push(f); });
    });
    const listed = sections.length > 0 ? foldedSections(sections) : '';
    const pages = items.length > 0 ? `<div class="navpages">${tree(items, folder, null, path, isReadme ? '' : listed)}</div>` : '';
    const history = `<a class="history" href="${esc(repoUrl('commits', path, ref))}" aria-label="History on GitHub" ` +
      `title="History on GitHub">${HISTORY_ICON}</a>`;
    return sideNavHtml({
      label: 'Project',
      head: { header: own, current: isReadme },
      stacks: stacks,
      empty: '<p class="snone">No reviews of this project.</p>',
      middle: (isReadme ? listed : '') + pages + neighborsHtml(project),
      foot: history,
    });
  }

  function itemOf(project: SiteProject): Item {
    return { path: project.path, title: project.title, proposed: project.proposed?.number };
  }

  // A page's sections in its project's sidebar. At 980 pixels and narrower
  // they start folded behind a Sections toggle, which a wider screen hides.
  function foldedSections(sections: PageSection[]): string {
    return '<div class="navsections"><button type="button" class="navfold" aria-expanded="false" aria-controls="pagesections">' +
      `<span class="chev" aria-hidden="true"></span>Sections</button>${sectionListHtml(sections, false, 'pagesections')}</div>`;
  }

  // The project a nested project sits in, which is the deepest project whose
  // folder holds its folder, or null for a top-level project.
  function parentOf(project: SiteProject): SiteProject | null {
    return ownerOf(folderOf(project).slice(0, -1));
  }

  // A nested project's neighbors: its parent, linked, over the parent's other
  // nested projects. A top-level project draws none, because the Projects
  // page lists its neighbors.
  function neighborsHtml(project: SiteProject): string {
    const parent = parentOf(project);
    if (parent === null) return '';
    const siblings = site.projects.filter(function (p) { return p !== project && parentOf(p) === parent; });
    return `<div class="navnear">${tree(siblings.map(itemOf), folderOf(parent), itemOf(parent), null)}</div>`;
  }

  // The banner atop every page of a proposed project: the pull request that
  // proposes it, with its title and status as the Reviews index draws them,
  // the head the site shows, and any other open pull request that proposes a
  // plan of the same name.
  function proposalBanner(proposed: Proposal): string {
    function review(number: number): string {
      return `<a href="${esc(`${base}reviews/${String(number)}/`)}">#${String(number)}</a>`;
    }
    const r = reviewFor(proposed.number);
    const status = r !== null ? statusHtml(r.state, r.review) : '';
    const also = proposed.also.length > 0
      ? `<p class="note">A plan of this name is also proposed in ${proposed.also.map(review).join(', ')}.</p>` : '';
    return `<div class="proposal"><p><b>${PROPOSED} in</b> ${review(proposed.number)}` +
      `${r !== null ? ` ${esc(r.title)}` : ''}${status !== '' ? ` · <span class="eyebrow">${status}</span>` : ''}</p>` +
      `<p class="note">This plan is in an open pull request and is not on <span class="mono">${esc(site.branch)}</span> yet. ` +
      `It shows head <span class="mono">${esc(proposed.head.slice(0, 7))}</span>, the newest head with a summary.</p>${also}</div>`;
  }

  function reviewFor(number: number): SiteReview | null {
    return site.reviews.find(function (r) { return r.number === number; }) ?? null;
  }

  // A merged pull request counts as closed, as on GitHub, and one whose state
  // the build could not ask GitHub for counts as open.
  function stateOf(r: SiteReview): 'open' | 'closed' { return r.state === 'merged' || r.state === 'closed' ? 'closed' : 'open'; }

  // The states the address asks to list: ?state=open,closed for both,
  // ?state=closed for closed alone, and ?state= for neither. With no query it
  // lists the open ones.
  function listedStates(): string[] {
    const asked = new URLSearchParams(location.search).get('state');
    return asked === null ? ['open'] : asked.split(',');
  }

  // A review's row: its pull request's number and title as a summary's sidebar
  // draws a stack's rows, its status, and when its newest head was published.
  function reviewRow(r: SiteReview): string {
    return `<tr><td class="pr"><a class="srow" href="${esc(`${base}reviews/${String(r.number)}/`)}">` +
      `<span class="snum">#${String(r.number)}</span><span class="stitle">${esc(r.title)}</span></a></td>` +
      `<td class="status"><span class="eyebrow">${statusHtml(r.state, r.review)}</span></td>` +
      `<td class="date"><time datetime="${esc(r.updated)}">${esc(localTime(new Date(r.updated)))}</time></td></tr>`;
  }

  function sameHeader(a: StackHeader, b: StackHeader): boolean {
    return a.project === b.project && a.title === b.title && a.url === b.url;
  }

  // The reviews in site.json's order, each stack's rows together, with a
  // divider only below the stack. Each segment of a stack is a table body
  // that opens with its header, and a new segment starts wherever a row's
  // header differs from the row drawn above it. Every body of a stack but the
  // last is `continued`, and draws no divider. Every column but the pull
  // request's has a set width, so checking a box never moves one.
  function reviewsTable(reviews: SiteReview[]): string {
    const stacks: SiteReview[][] = [];
    reviews.forEach(function (r) {
      const last = stacks[stacks.length - 1];
      if (last !== undefined && r.stack !== null && last[0]?.stack === r.stack) last.push(r);
      else stacks.push([r]);
    });
    const bodies = stacks.map(function (rows) {
      const segments: { header: StackHeader; rows: SiteReview[] }[] = [];
      rows.forEach(function (r) {
        const last = segments[segments.length - 1];
        if (last !== undefined && sameHeader(last.header, r.header)) last.rows.push(r);
        else segments.push({ header: r.header, rows: [r] });
      });
      return segments.map(function (segment, i) {
        return `<tbody${i < segments.length - 1 ? ' class="continued"' : ''}>` +
          `<tr class="stackhead"><th colspan="3" scope="rowgroup">${stackHeaderHtml(segment.header, false)}</th></tr>` +
          `${segment.rows.map(reviewRow).join('')}</tbody>`;
      }).join('');
    });
    return '<div class="tblwrap"><table class="tbl reviews">' +
      '<colgroup><col><col class="status"><col class="date"></colgroup>' +
      '<thead><tr><th>Pull request</th><th>Status</th><th class="date">Updated</th></tr></thead>' +
      bodies.join('') + '</table></div>';
  }

  // The reviews of open pull requests, closed ones, or both, as the boxes at
  // the top say and the address records. A stack keeps the reviews it has left
  // together.
  function showReviews(): void {
    const listed = listedStates();
    function box(state: string, label: string): string {
      const count = site.reviews.filter(function (r) { return stateOf(r) === state; }).length;
      return `<label><input type="checkbox" value="${state}"${listed.includes(state) ? ' checked' : ''}> ${label} ` +
        `<span class="count">${String(count)}</span></label>`;
    }
    const main = frame('reviews', 'Reviews', '<h1>Reviews</h1>' +
      `<div class="reviewstates" role="group" aria-label="Pull request states">${box('open', 'Open')}${box('closed', 'Closed')}</div>` +
      '<div class="reviewlist"></div>');
    const list = main.querySelector('.reviewlist');
    if (list === null) throw new Error('The reviews view lost the list it just drew');
    const boxes = Array.from(main.querySelectorAll<HTMLInputElement>('.reviewstates input'));
    function checked(): string[] {
      return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
    }
    function draw(): void {
      if (list === null) return;
      const states = checked();
      const shown = site.reviews.filter(function (r) { return states.includes(stateOf(r)); });
      if (states.length === 0) list.innerHTML = '<p class="note">Check Open or Closed to list their reviews.</p>';
      else if (shown.length === 0) list.innerHTML = `<p class="note">No ${states.length === 1 ? `${states[0] ?? ''} ` : ''}pull request has a review here.</p>`;
      else list.innerHTML = reviewsTable(shown);
    }
    boxes.forEach(function (b) {
      b.addEventListener('change', function () {
        const states = checked();
        const query = states.length === 1 && states[0] === 'open' ? '' : `?state=${states.join(',')}`;
        history.replaceState(null, '', location.pathname + query + location.hash);
        draw();
      });
    });
    draw();
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
        (r.entry.proposed !== undefined ? ` ${proposedBadge(r.entry.proposed)}` : '') +
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
      const main = document.querySelector('.sitemain');
      if (main === null) throw new Error('A section toggle has no page to toggle');
      const collapsed = main.classList.toggle('collapsed');
      toggle.setAttribute('aria-expanded', String(!collapsed));
      if (fitFrame !== null) fitFrame();
      return;
    }
    const fold = target?.closest('.navfold');
    if (fold != null) {
      fold.setAttribute('aria-expanded', String(fold.getAttribute('aria-expanded') !== 'true'));
      return;
    }
    const bar = target?.closest('.barhide, .barshow');
    if (bar != null) {
      const hidden = document.body.classList.toggle('barhidden', bar.classList.contains('barhide'));
      document.querySelector<HTMLElement>(hidden ? '.barshow' : '.barhide')?.focus();
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
