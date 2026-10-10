// Projector PR summary renderer: builds the page from summary data, embedded in
// #summary-data or fetched from data.json beside the page, then wires collapsing,
// reviewed state, highlighting, and sticky headers.
(function (): void {
function renderSummary(data: SummaryData): void {
  const pr = data.pr;
  const repoUrl = `https://github.com/${pr.repo}`;
  const prUrl = `${repoUrl}/pull/${String(pr.number)}`;
  const prRef = `${pr.repo.slice(pr.repo.lastIndexOf('/') + 1)}#${String(pr.number)}`;
  const baseRef = pr.baseRef !== undefined && pr.baseRef !== '' ? pr.baseRef : null;
  const filesByPath: Record<string, SummaryFile> = {};
  data.files.forEach(function (f) { filesByPath[f.path] = f; });

  function esc(s: string | number | null | undefined): string {
    return (s == null ? '' : String(s)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function num(n: number): string { return n.toLocaleString('en-US'); }
  // A paragraph of prose is wrapped in <p>; a list, table, heading or code
  // block the summary wrote as an item of its own is placed as it is, since a
  // block inside <p> ends the paragraph early.
  function block(html: string): string {
    return /^\s*<(ul|ol|table|div|pre|h3|h4|p)[\s>]/i.test(html) ? html : `<p>${html}</p>`;
  }
  function short(sha: string | undefined): string { return (sha ?? '').slice(0, 9); }
  function lineNumber(n: number | null): string { return n === null || n === 0 ? '' : String(n); }
  function ext(href: string, text: string, cls?: string): string {
    return `<a${cls !== undefined && cls !== '' ? ` class="${cls}"` : ''} href="${esc(href)}" target="_blank" rel="noopener">${text}</a>`;
  }
  // The build checks every file a group names against the diff.
  function fileAt(path: string): SummaryFile {
    const f = filesByPath[path];
    if (f === undefined) throw new Error(`The summary names ${path}, which is not in its diff`);
    return f;
  }

  const COPY_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path class="ic-copy" fill="currentColor" d="M0 6.75C0 5.78.78 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .14.11.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25Zm5-5C5 .78 5.78 0 6.75 0h7.5C15.22 0 16 .78 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .14.11.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z"/><path class="ic-ok" fill="currentColor" d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z"/></svg>';

  const CHIPS: Record<string, string> = { context: 'context', verify: 'verified', flag: 'concern' };
  function kindOf(c: SummaryCheck): string { return c.kind === 'flag' || c.kind === 'context' ? c.kind : 'verify'; }
  // A note's checkbox state is keyed by where it sits and what it says, so a
  // note carried unchanged to a later head stays checked and a rewritten one
  // starts over.
  function noteId(scope: string, text: string): string {
    const key = `${scope}\u0000${text}`;
    let h = 0x811c9dc5;
    for (let i = 0; i < key.length; i++) h = Math.imul(h ^ key.charCodeAt(i), 0x01000193) >>> 0;
    return h.toString(16).padStart(8, '0');
  }
  function notesList(checks: SummaryCheck[], cls: string, scope: string): string {
    if (checks.length === 0) return '';
    return `<ul class="notes ${cls}">` + checks.map(function (c) {
      const k = kindOf(c);
      const box = k === 'flag'
        ? `<input type="checkbox" class="nbox note-box" data-nid="${noteId(scope, c.text)}" aria-label="Handled" title="Handled">`
        : '<span class="nbox"></span>';
      return `<li class="${k}">${box}<span class="chip ${k}">${CHIPS[k] ?? k}</span><span class="ntext">${c.text}</span></li>`;
    }).join('') + '</ul>';
  }

  function renderFile(entry: SummaryGroup['files'][number]): string {
    const f = fileAt(entry.path);
    const collapsed = entry.collapsed ?? (f.kind === 'generated' || f.kind === 'test' || f.kind === 'docs');
    const slash = f.path.lastIndexOf('/');
    const dir = slash >= 0 ? f.path.slice(0, slash + 1) : '';
    const base = f.path.slice(slash + 1);
    const checks = entry.checks ?? [];
    // A note with a line sits under that line of the diff; the rest sit at the top of the file.
    const atLine: Record<string, SummaryCheck[]> = {};
    const loose: SummaryCheck[] = [];
    checks.forEach(function (c) {
      if (c.line === undefined) { loose.push(c); return; }
      const key = `${c.side === 'old' ? 'old' : 'new'}:${String(c.line)}`;
      (atLine[key] ??= []).push(c);
    });
    // Only a side the file has gets a line-number column: a new file has no
    // old numbers, and a deleted file no new ones.
    const kinds = new Set(f.hunks.flatMap(function (h) { return h.lines.map(function (l) { return l[0]; }); }));
    const oldSide = kinds.has('d') || kinds.has('c') || !kinds.has('a');
    const newSide = kinds.has('a') || kinds.has('c') || !kinds.has('d');
    const sides = Number(oldSide) + Number(newSide);
    const gutter = `<td class="ln" colspan="${String(sides)}"></td>`;
    // A fixed table layout sizes its columns from the first row, a hunk header
    // whose gutter spans them all, so the columns are sized here instead.
    const columns = `<colgroup>${'<col class="lncol">'.repeat(sides)}<col></colgroup>`;
    function numbers(l: SummaryLine): string {
      return (oldSide ? `<td class="ln">${lineNumber(l[1])}</td>` : '') + (newSide ? `<td class="ln">${lineNumber(l[2])}</td>` : '');
    }
    function notesAt(l: SummaryLine): string {
      const keys = l[0] === 'a' ? [`new:${String(l[2])}`] : l[0] === 'd' ? [`old:${String(l[1])}`] : [`new:${String(l[2])}`, `old:${String(l[1])}`];
      const found = keys.flatMap(function (k) { return atLine[k] ?? []; });
      return found.length > 0 ? `<tr class="noterow">${gutter}<td class="nte">${notesList(found, 'inotes', f.path)}</td></tr>` : '';
    }
    let badges = checks.length > 0 ? `<span class="badge notes" data-total="${String(checks.length)}">${String(checks.length)} note${checks.length === 1 ? '' : 's'}</span>` : '';
    if (f.new === true) badges += '<span class="badge new">new</span>';
    if (f.deleted === true) badges += '<span class="badge deleted">deleted</span>';
    if (f.kind !== '') badges += `<span class="badge ${f.kind}">${f.kind}</span>`;
    const hunks = f.hunks.map(function (h) {
      const rows = [`<tr class="hunk">${gutter}<td class="code">${esc(h.header)}</td></tr>`];
      h.lines.forEach(function (l) {
        const t = l[0];
        if (t === 'm') { rows.push(`<tr class="meta">${gutter}<td class="code">${esc(l[3])}</td></tr>`); return; }
        const cls = t === 'a' ? 'add' : t === 'd' ? 'del' : 'ctx';
        const sign = t === 'a' ? '+' : t === 'd' ? '-' : ' ';
        rows.push(`<tr class="${cls}">${numbers(l)}<td class="code"><span class="sign">${sign}</span><span class="src">${esc(l[3])}</span></td></tr>`);
        rows.push(notesAt(l));
      });
      return `<table class="diff">${columns}${rows.join('')}</table>`;
    }).join('') || '<p class="fnote">No content changes.</p>';
    return `<article class="file${collapsed ? ' collapsed' : ''}" id="${f.id}" data-fid="${f.id}" data-lang="${esc(f.lang)}" data-collapsed-default="${collapsed ? '1' : ''}">` +
      '<div class="fsentinel" aria-hidden="true"></div>' +
      '<header class="fhead">' +
        `<button class="ftoggle" type="button" aria-expanded="${collapsed ? 'false' : 'true'}" aria-controls="${f.id}-body" title="Collapse or expand">` +
          '<span class="chev" aria-hidden="true"></span>' +
          `<span class="fpath"><span class="dir">${esc(dir)}</span><span class="base">${esc(base)}</span></span>` +
        '</button>' +
        `<button class="copypath" type="button" data-path="${esc(f.path)}" title="Copy file path" aria-label="Copy file path">${COPY_ICON}</button>` +
        `<span class="fmeta">${badges}` +
          `<span class="stat"><span class="plus">+${String(f.adds)}</span> <span class="minus">−${String(f.dels)}</span></span>` +
          ext(`${prUrl}/files#${f.anchor}`, 'PR', 'flink') +
          ext(`${repoUrl}/blob/${pr.head}/${f.path}`, 'file', 'flink') +
          `<label class="freviewed"><input type="checkbox" class="file-box" id="${f.id}-reviewed"> Reviewed</label>` +
        '</span>' +
      '</header>' +
      (entry.note !== undefined && entry.note !== '' ? `<p class="fnote">${entry.note}</p>` : '') +
      notesList(loose, 'fnotes', f.path) +
      `<div class="fbody" id="${f.id}-body">${hunks}</div>` +
    '</article>';
  }

  function renderGroup(g: SummaryGroup, i: number): string {
    let adds = 0, dels = 0;
    g.files.forEach(function (s) { const f = fileAt(s.path); adds += f.adds; dels += f.dels; });
    const gid = esc(g.id);
    const intro = (g.intro ?? []).map(block).join('');
    const checks = g.checks ?? [];
    const notes = checks.length > 0 ? `<div class="gnotes"><h4>Review notes</h4>${notesList(checks, 'gnotelist', `group:${g.id}`)}</div>` : '';
    const list = g.files.map(function (s) { const f = fileAt(s.path); return `<li><a href="#${f.id}">${esc(f.path.split('/').pop())}</a></li>`; }).join('');
    return `<section class="group" id="${gid}" data-gid="${gid}">` +
      '<div class="gsentinel" aria-hidden="true"></div>' +
      '<header class="ghead">' +
        `<h2 class="gtitle"><button class="gtoggle" type="button" aria-expanded="true" aria-controls="${gid}-body" title="Collapse or expand">` +
          `<span class="chev" aria-hidden="true"></span><span class="gnum">${String(i + 1)}</span><span class="gname">${g.title}</span></button></h2>` +
        `<span class="gstats">${String(g.files.length)} file${g.files.length === 1 ? '' : 's'} · <span class="plus">+${num(adds)}</span> <span class="minus">−${num(dels)}</span></span>` +
        `<label class="greviewed"><input type="checkbox" class="reviewed-box" id="${gid}-reviewed"> Reviewed</label>` +
      '</header>' +
      `<div class="gbody" id="${gid}-body">` +
        (g.kicker !== undefined && g.kicker !== '' ? `<p class="kicker">${g.kicker}</p>` : '') +
        `<div class="prose">${intro}</div>${notes}` +
        `<div class="filebar"><span class="filebar-label">Files in this section</span><ul class="filelist">${list}</ul>` +
          '<span class="filebar-actions"><button type="button" class="linkbtn expand-all">Expand all</button><button type="button" class="linkbtn collapse-all">Collapse all</button></span></div>' +
        `<div class="files">${g.files.map(renderFile).join('')}</div>` +
      '</div>' +
    '</section>';
  }

  function renderOverview(): string {
    const o = data.overview ?? {};
    const s = data.stats;
    const summary = (o.summary ?? []).map(block).join('');
    let out = `<div class="card prose"><h3>What this PR does</h3>${summary}` +
      '<p><b>How to read this.</b> Each section opens with what it does and why. Review notes come in three kinds: <span class="chip context inline">context</span> to hold in mind while you read, <span class="chip verify inline">verified</span> for an invariant the reviewer checked, and how, and <span class="chip flag inline">concern</span> for something that may be wrong or risky and needs a decision. A note about one file sits at the top of that file, and a note about one line sits under that line in the diff. Check off concern notes as you settle them; each file header counts the ones still open. Mark each file Reviewed as you go. Marking a section Reviewed closes it and its files, and a section is marked for you once all its files are. Checkboxes are remembered in this browser only.</p></div>';
    const tiles: [string, string][] = [[num(s.files), 'files'], [`<span class="plus">+${num(s.adds)}</span> <span class="minus">−${num(s.dels)}</span>`, 'lines'],
      [num(s.hand), 'hand-written lines'], [num(s.test), 'test lines'], [num(s.generated), 'generated (collapsed)'], [num(s.docs), 'documentation lines']];
    out += '<div class="card"><h3>Shape of the change</h3><div class="statgrid">' +
      tiles.map(function (t) { return `<div class="stat-tile"><div class="v">${t[0]}</div><div class="l">${t[1]}</div></div>`; }).join('') +
      '</div></div>';
    const cards = o.cards ?? [];
    if (cards.length > 0) {
      out += '<div class="twocol">' + cards.map(function (c) {
        return `<div class="card"${c.id !== undefined && c.id !== '' ? ` id="${esc(c.id)}"` : ''}><h3>${c.title}</h3>${c.html}</div>`;
      }).join('') + '</div>';
    }
    out += '<div class="legend"><span><span class="badge new">new</span> new file</span><span><span class="badge generated">generated</span> generated output</span><span><span class="badge test">test</span> collapsed by default</span><span><span class="badge docs">docs</span> collapsed by default</span><span>Click a file or section header to expand or collapse it; "Reviewed" collapses it and remembers that. Click a name in the code to mark every place it appears; click it again or press Escape to clear the marks.</span></div>';
    return out;
  }

  // Every published head, newest first, one line each with when it was
  // published in the reader's own time zone, in a list that starts closed.
  function headsList(): string {
    const heads = data.heads ?? [];
    if (heads.length < 2) return '';
    const rows = heads.map(function (h) {
      const when = h.at !== undefined
        ? `<span class="vwhen">${esc(new Date(h.at * 1000).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }))}</span>`
        : '';
      const row = `<span class="mono">${short(h.head)}</span>${when}`;
      return h.current ? `<li class="current" aria-current="page">${row}</li>` : `<li><a href="${esc(h.url)}">${row}</a></li>`;
    }).join('');
    return `<details class="versions"><summary><span class="chev" aria-hidden="true"></span>Versions · ${String(heads.length)}</summary><ul>${rows}</ul></details>`;
  }

  // The pull requests stacked with this one, one row each, this one marked; a
  // pull request alone shows its title instead. A pull request in the stack
  // with no summary on the site links to GitHub.
  function stackList(): string {
    const stack = data.stack ?? [];
    if (stack.length < 2) return `<div class="prtitle" title="${esc(pr.title)}">${esc(pr.title)}</div>`;
    return '<ul class="stack" aria-label="Pull requests in this stack">' + stack.map(function (s) {
      const row = `<span class="snum">#${String(s.number)}</span><span class="stitle">${esc(s.title)}</span>`;
      if (s.current) return `<li class="current" aria-current="page" title="${esc(s.title)}">${row}</li>`;
      const href = s.url !== '' ? s.url : `${repoUrl}/pull/${String(s.number)}`;
      return `<li><a href="${esc(href)}" title="${esc(s.title)}">${row}</a></li>`;
    }).join('') + '</ul>';
  }

  function projectsList(): string {
    const projects = data.projects ?? [];
    if (projects.length === 0) return '';
    return '<div class="prmeta">Projects: ' + projects.map(function (p) {
      return `<a href="${esc(p.url)}">${esc(p.title)}</a>`;
    }).join(' · ') + '</div>';
  }

  function renderPage(): string {
    const nav = data.groups.map(function (g, i) {
      return `<li><a href="#${esc(g.id)}"><span class="nnum">${String(i + 1)}</span><span class="ntitle">${g.title}</span><span class="ncheck" data-gid="${esc(g.id)}"></span></a></li>`;
    }).join('');
    const extra = '<a href="#overview">Overview</a>' + (data.overview?.cards ?? []).filter(function (c) { return c.id !== undefined && c.id !== ''; }).map(function (c) {
      return `<a href="#${esc(c.id)}">${c.title}</a>`;
    }).join('');
    const title = esc(data.name !== undefined && data.name !== '' ? data.name : `Review of ${prRef}`);
    return '<div class="wrap">' +
      `<div class="prbar">${ext(prUrl, esc(prRef), 'prref')}<span class="prname">${title}</span></div>` +
      `<header class="top"><div><div class="eyebrow">${ext(prUrl, esc(prRef))} · head <span class="mono">${short(pr.head)}</span> on ${esc(baseRef ?? 'base')}</div><h1>${title}</h1></div>` +
        `<div class="sub">${data.indexUrl !== undefined && data.indexUrl !== '' ? `<a href="${esc(data.indexUrl)}">All reviews</a> · ` : ''}${ext(prUrl, 'Open on GitHub')} · ${ext(`${prUrl}/files`, 'Files tab')} · ${ext(`${repoUrl}/compare/${encodeURIComponent(baseRef ?? 'main')}...${pr.head}`, 'Compare')}</div></header>` +
      '<div class="layout"><aside class="side"><nav class="nav" aria-label="Sections">' +
        `<div class="prblock">${ext(prUrl, esc(prRef), 'prref')}${stackList()}${projectsList()}</div>` +
        `<div class="extra">${extra}</div>` +
        `<div class="progress"><span>Reviewed</span><b id="progress-count">0 / ${String(data.groups.length)}</b></div><div class="bar"><i id="progress-bar"></i></div>` +
        `<ol>${nav}</ol>${headsList()}</nav></aside>` +
      `<main><div class="overview" id="overview">${renderOverview()}</div>` +
        `<div class="groups">${data.groups.map(renderGroup).join('')}</div>` +
        `<footer>Generated from the diff at head <span class="mono">${short(pr.head)}</span> on ${esc(data.generatedAt ?? '')} by Projector's <span class="mono">summarize-pr</span> skill. Syntax colours come from highlight.js; green and red row tints mark added and removed lines.</footer>` +
      '</main></div></div>';
  }

  const root = document.getElementById('summary') ?? document.body;
  root.innerHTML = renderPage();
  window.__SUMMARY_KEY__ = `summary:${pr.repo}#${String(pr.number)}:`;
}

function wireSummary(): void {
  const storedKey = window.__SUMMARY_KEY__;
  const KEY = storedKey !== undefined && storedKey !== '' ? storedKey : 'summary:';
  function get(k: string): string | null { try { return localStorage.getItem(KEY + k); } catch { return null; } }
  function set(k: string, v: boolean): void { try { if (v) localStorage.setItem(KEY + k, '1'); else localStorage.removeItem(KEY + k); } catch { /* without storage the state lasts only this visit */ } }

  const groups = Array.from(document.querySelectorAll<HTMLElement>('.group'));
  const progressEl = document.getElementById('progress-count');
  const barEl = document.getElementById('progress-bar');

  function refreshProgress(): void {
    let done = 0;
    groups.forEach(function (g) {
      const isDone = g.classList.contains('done');
      if (isDone) done++;
      const dot = document.querySelector(`.ncheck[data-gid="${g.dataset.gid ?? ''}"]`);
      if (dot !== null) dot.classList.toggle('done', isDone);
    });
    if (progressEl !== null) progressEl.textContent = `${String(done)} / ${String(groups.length)}`;
    if (barEl !== null) barEl.style.width = `${String(groups.length > 0 ? (100 * done / groups.length) : 0)}%`;
  }

  function filesOf(g: HTMLElement): HTMLElement[] { return Array.from(g.querySelectorAll<HTMLElement>('.file')); }
  function fileBox(f: HTMLElement): HTMLInputElement {
    const box = f.querySelector<HTMLInputElement>('.file-box');
    if (box === null) throw new Error(`File ${f.dataset.fid ?? ''} has no Reviewed box`);
    return box;
  }
  function groupBox(g: HTMLElement): HTMLInputElement {
    const box = g.querySelector<HTMLInputElement>('.reviewed-box');
    if (box === null) throw new Error(`Section ${g.dataset.gid ?? ''} has no Reviewed box`);
    return box;
  }

  function setCollapsed(f: HTMLElement, collapsed: boolean): void {
    f.classList.toggle('collapsed', collapsed);
    if (collapsed) f.classList.remove('stuck');
    const btn = f.querySelector('.ftoggle');
    if (btn !== null) btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    if (!collapsed) highlightFile(f);
  }

  // Reviewing a file collapses it; un-reviewing one opens it to be read again.
  function markFile(f: HTMLElement, reviewed: boolean): void {
    fileBox(f).checked = reviewed;
    f.classList.toggle('reviewed', reviewed);
    set('f:' + (f.dataset.fid ?? ''), reviewed);
    setCollapsed(f, reviewed);
  }

  function setFolded(g: HTMLElement, folded: boolean, highlight: boolean): void {
    g.classList.toggle('folded', folded);
    const toggle = g.querySelector('.gtoggle');
    if (toggle !== null) toggle.setAttribute('aria-expanded', folded ? 'false' : 'true');
    if (!folded && highlight) g.querySelectorAll<HTMLElement>('.file:not(.collapsed)').forEach(highlightFile);
  }

  function allFilesReviewed(g: HTMLElement): boolean {
    const files = filesOf(g);
    return files.length > 0 && files.every(function (f) { return fileBox(f).checked; });
  }

  // A section's Reviewed box is its own: checking it closes the section and its
  // files without marking any file reviewed. The last file reviewed checks it,
  // and a file un-reviewed unchecks it.
  function markSection(g: HTMLElement, reviewed: boolean): void {
    set('g:' + (g.dataset.gid ?? ''), reviewed);
    g.classList.toggle('done', reviewed);
    groupBox(g).checked = reviewed;
    refreshProgress();
  }
  function closeSection(g: HTMLElement, fromReader: boolean): void {
    const inside = g.getBoundingClientRect().top < 0;
    filesOf(g).forEach(function (f) { setCollapsed(f, true); });
    setFolded(g, true, false);
    // Closed from inside it: bring its header back into view so the next section follows.
    if (fromReader && inside) g.scrollIntoView({ block: 'start' });
  }

  groups.forEach(function (g) {
    const gid = g.dataset.gid ?? '';
    const files = filesOf(g);
    const box = groupBox(g);
    const toggle = g.querySelector('.gtoggle');
    const expandAll = g.querySelector('.expand-all');
    const collapseAll = g.querySelector('.collapse-all');
    if (toggle === null || expandAll === null || collapseAll === null) throw new Error(`Section ${gid} is missing its controls`);
    files.forEach(function (f) {
      const reviewed = get('f:' + (f.dataset.fid ?? '')) === '1';
      fileBox(f).checked = reviewed;
      f.classList.toggle('reviewed', reviewed);
      if (reviewed) setCollapsed(f, true);
    });
    const reviewed = get('g:' + gid) === '1' || allFilesReviewed(g);
    markSection(g, reviewed);
    if (reviewed) closeSection(g, false);
    box.addEventListener('change', function () {
      markSection(g, box.checked);
      if (box.checked) {
        closeSection(g, true);
      } else {
        setFolded(g, false, false);
        files.forEach(function (f) { setCollapsed(f, fileBox(f).checked || f.dataset.collapsedDefault === '1'); });
      }
    });
    toggle.addEventListener('click', function () { setFolded(g, !g.classList.contains('folded'), true); });
    expandAll.addEventListener('click', function () { files.forEach(function (f) { setCollapsed(f, false); }); });
    collapseAll.addEventListener('click', function () { files.forEach(function (f) { setCollapsed(f, true); }); });
  });

  document.querySelectorAll<HTMLElement>('.file').forEach(function (f) {
    const box = fileBox(f);
    const toggle = f.querySelector('.ftoggle');
    const g = f.closest<HTMLElement>('.group');
    if (toggle === null || g === null) throw new Error(`File ${f.dataset.fid ?? ''} is missing its controls`);
    box.addEventListener('change', function () {
      const wasStuck = f.classList.contains('stuck');
      markFile(f, box.checked);
      // Marked reviewed from a header pinned over a long file: bring the
      // collapsed card into view instead of landing on what followed it.
      if (box.checked && wasStuck) f.scrollIntoView({ block: 'start' });
      if (box.checked && !g.classList.contains('done') && allFilesReviewed(g)) {
        markSection(g, true);
        closeSection(g, true);
      } else if (!box.checked && g.classList.contains('done')) {
        markSection(g, false);
      }
    });
    toggle.addEventListener('click', function () {
      setCollapsed(f, !f.classList.contains('collapsed'));
    });
  });

  // A file's note badge counts its concern notes still unchecked.
  function refreshNoteBadge(f: HTMLElement): void {
    const badge = f.querySelector<HTMLElement>('.badge.notes');
    if (badge === null) return;
    const boxes = Array.from(f.querySelectorAll<HTMLInputElement>('.note-box'));
    const open = boxes.filter(function (b) { return !b.checked; }).length;
    const total = Number(badge.dataset.total ?? '0');
    badge.textContent = open > 0 ? `${String(open)} of ${String(boxes.length)} open` : `${String(total)} note${total === 1 ? '' : 's'}`;
    badge.classList.toggle('open', open > 0);
  }
  document.querySelectorAll<HTMLInputElement>('.note-box').forEach(function (box) {
    const nid = box.dataset.nid ?? '';
    const item = box.closest('li');
    box.checked = get('n:' + nid) === '1';
    if (item !== null) item.classList.toggle('checked', box.checked);
    box.addEventListener('change', function () {
      set('n:' + nid, box.checked);
      if (item !== null) item.classList.toggle('checked', box.checked);
      const f = box.closest<HTMLElement>('.file');
      if (f !== null) refreshNoteBadge(f);
    });
  });
  document.querySelectorAll<HTMLElement>('.file').forEach(refreshNoteBadge);

  document.querySelectorAll<HTMLElement>('.copypath').forEach(function (b) {
    b.addEventListener('click', function (ev) {
      ev.stopPropagation();
      const path = b.dataset.path ?? '';
      function done(): void { b.classList.add('copied'); b.title = 'Copied'; setTimeout(function () { b.classList.remove('copied'); b.title = 'Copy file path'; }, 1500); }
      // The write fails where the Clipboard API is missing or refused: outside a
      // secure context, in an unfocused document, when permission is denied, or
      // on a host that blocks it, as some Claude Artifact views do, which also
      // suppress dialogs. So show the path beside the button, selected for the
      // reader to copy, rather than prompting or calling the deprecated
      // document.execCommand('copy'). It goes away when focus leaves it.
      function fallback(): void {
        const shown = b.nextElementSibling;
        if (shown instanceof HTMLInputElement && shown.classList.contains('copyfield')) {
          shown.focus();
          shown.select();
          return;
        }
        const field = document.createElement('input');
        field.className = 'copyfield';
        field.readOnly = true;
        field.value = path;
        field.size = Math.min(Math.max(path.length, 10), 60);
        field.setAttribute('aria-label', 'File path, selected to copy');
        field.addEventListener('blur', function () { field.remove(); });
        b.insertAdjacentElement('afterend', field);
        field.focus();
        field.select();
      }
      try { navigator.clipboard.writeText(path).then(done, fallback); } catch { fallback(); }
    });
  });

  // Pinned-header detection: a 1px sentinel sits at the top of each section and
  // each file card. Once it scrolls above its header's sticky offset the header
  // is pinned: a section header gains a shadow, and a file header, pinned just
  // below its section's, goes square so the card's borders run into it. Each
  // section measures its own header, since titles wrap to different heights.
  let stuckObservers: IntersectionObserver[] = [];
  function topOf(el: Element | null): number { return el !== null ? (parseFloat(getComputedStyle(el).top) || 0) : 0; }
  function observe(targets: Element[], offset: number, onChange: (target: Element, pinned: boolean) => void): void {
    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { onChange(e.target, !e.isIntersecting && e.boundingClientRect.top < offset + 1); });
    }, { rootMargin: `${String(-(offset + 1))}px 0px 0px 0px`, threshold: 0 });
    targets.forEach(function (t) { observer.observe(t); });
    stuckObservers.push(observer);
  }
  function setupStuck(): void {
    groups.forEach(function (g) {
      const head = g.querySelector<HTMLElement>('.ghead');
      if (head !== null) g.style.setProperty('--ghead-h', `${String(head.offsetHeight)}px`);
    });
    if (!('IntersectionObserver' in window)) return;
    stuckObservers.forEach(function (o) { o.disconnect(); });
    stuckObservers = [];
    groups.forEach(function (g) {
      const head = g.querySelector<HTMLElement>('.ghead');
      const top = topOf(head);
      const sentinel = g.querySelector('.gsentinel');
      if (sentinel !== null) observe([sentinel], top, function (_t, pinned) { g.classList.toggle('gstuck', pinned); });
      const sentinels = Array.from(g.querySelectorAll('.fsentinel'));
      if (sentinels.length === 0) return;
      observe(sentinels, top + (head !== null ? head.offsetHeight : 0), function (t, pinned) {
        const f = t.parentElement;
        if (f === null) throw new Error('A header sentinel sits outside its file');
        f.classList.toggle('stuck', pinned && !f.classList.contains('collapsed'));
      });
    });
  }
  setupStuck();
  void document.fonts.ready.then(setupStuck);
  let resizeTimer: ReturnType<typeof setTimeout> | undefined;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(setupStuck, 200);
  });

  // Clicking an identifier in the code marks every instance of it on the page,
  // so a definition's callers stand out. Clicking it again, clicking code that
  // is not an identifier, or pressing Escape clears the marks. A drag selects
  // text as usual and marks nothing.
  // Combining marks belong to the name they follow, as a vowel sign does in
  // Devanagari, and connector punctuation includes the underscore.
  const IDENTIFIER = /[\p{L}\p{M}\p{N}\p{Pc}$]+/gu;
  // A run of digits or marks alone, a number or an emoji's variation
  // selector, is not a name worth tracing.
  const NAME = /[\p{L}\p{Pc}$]/u;
  let token = '';
  function tokenAt(node: Node | null, offset: number): string {
    if (!(node instanceof Text)) return '';
    // The same match that marks the page finds the clicked name, so the two
    // always agree, a name with a letter beyond one UTF-16 unit included.
    for (const m of node.data.matchAll(IDENTIFIER)) {
      if (m.index > offset) break;
      if (offset <= m.index + m[0].length) return NAME.test(m[0]) ? m[0] : '';
    }
    return '';
  }
  function clearTokens(): void {
    document.querySelectorAll('mark.tok').forEach(function (m) {
      const parent = m.parentNode;
      if (parent === null) return;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();
    });
  }
  function markTokens(root: ParentNode): void {
    if (token === '') return;
    root.querySelectorAll('table.diff .src').forEach(function (src) {
      const walker = document.createTreeWalker(src, NodeFilter.SHOW_TEXT);
      const texts: Text[] = [];
      for (let n = walker.nextNode(); n !== null; n = walker.nextNode()) {
        if (n instanceof Text && n.data.includes(token) && n.parentElement?.closest('mark.tok') == null) texts.push(n);
      }
      texts.forEach(function (t) {
        const s = t.data;
        const parts: (string | Node)[] = [];
        let last = 0;
        for (const m of s.matchAll(IDENTIFIER)) {
          if (m[0] !== token) continue;
          const mark = document.createElement('mark');
          mark.className = 'tok';
          mark.textContent = token;
          parts.push(s.slice(last, m.index), mark);
          last = m.index + token.length;
        }
        if (parts.length === 0) return;
        parts.push(s.slice(last));
        t.replaceWith(...parts);
      });
    });
  }
  document.addEventListener('click', function (ev) {
    if (!(ev.target instanceof Element) || ev.target.closest('table.diff .src') === null) return;
    const selection = window.getSelection();
    if (selection?.isCollapsed !== true) return;
    const word = tokenAt(selection.anchorNode, selection.anchorOffset);
    clearTokens();
    token = word === token ? '' : word;
    markTokens(document);
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape' || token === '') return;
    clearTokens();
    token = '';
  });

  // Syntax highlighting: each hunk is highlighted as two whole texts (the
  // old side: context + removed lines; the new side: context + added lines)
  // so tokens that span lines survive, then split back into rows. Files that
  // start collapsed are highlighted when first opened.
  function splitLines(s: string): string[] {
    const out: string[] = [], open: string[] = [];
    let cur = '';
    const re = /<span class="[^"]*">|<\/span>|\n|[^<\n]+|</g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(s)) !== null) {
      const t = m[0];
      if (t === '\n') {
        cur += '</span>'.repeat(open.length);
        out.push(cur);
        cur = open.join('');
      } else if (t === '</span>') {
        open.pop(); cur += t;
      } else if (t.length > 1 && t.startsWith('<')) {
        open.push(t); cur += t;
      } else {
        cur += t;
      }
    }
    cur += '</span>'.repeat(open.length);
    out.push(cur);
    return out;
  }
  function hl(text: string, lang: string): string[] | null {
    const hljs = window.hljs;
    if (hljs === undefined) return null;
    try { return splitLines(hljs.highlight(text, { language: lang, ignoreIllegals: true }).value); }
    catch { return null; }
  }
  function highlightFile(f: HTMLElement): void {
    const hljs = window.hljs;
    if ((f.dataset.hl ?? '') !== '' || hljs === undefined) return;
    const lang = f.dataset.lang;
    if (lang === undefined || lang === '' || hljs.getLanguage(lang) === undefined) { f.dataset.hl = '1'; return; }
    f.querySelectorAll('table.diff').forEach(function (tbl) {
      const rows = Array.from(tbl.querySelectorAll('tr.add, tr.del, tr.ctx'));
      const oldT: string[] = [], newT: string[] = [], oldR: (Element | null)[] = [], newR: (Element | null)[] = [];
      rows.forEach(function (r) {
        const src = r.querySelector('.src'); const text = src !== null ? src.textContent : '';
        if (r.classList.contains('add')) { newR.push(src); newT.push(text); }
        else if (r.classList.contains('del')) { oldR.push(src); oldT.push(text); }
        else { newR.push(src); newT.push(text); oldR.push(null); oldT.push(text); }
      });
      const newH = newT.length > 0 ? hl(newT.join('\n'), lang) : [];
      const oldH = oldT.length > 0 ? hl(oldT.join('\n'), lang) : [];
      if (newH?.length === newR.length) newR.forEach(function (s, k) { const h = newH[k]; if (s !== null && h !== undefined) s.innerHTML = h; });
      if (oldH?.length === oldR.length) oldR.forEach(function (s, k) { const h = oldH[k]; if (s !== null && h !== undefined) s.innerHTML = h; });
    });
    f.dataset.hl = '1';
    // Highlighting rewrote the rows, and with them any marks of the clicked token.
    markTokens(f);
  }
  const hlQueue = Array.from(document.querySelectorAll<HTMLElement>('.group:not(.folded) .file:not(.collapsed)'));
  function pump(): void {
    const t0 = Date.now();
    while (hlQueue.length > 0 && Date.now() - t0 < 24) {
      const next = hlQueue.shift();
      if (next !== undefined) highlightFile(next);
    }
    if (hlQueue.length > 0) setTimeout(pump, 0);
  }
  if (window.hljs !== undefined) setTimeout(pump, 0);

  // A hash pointing into a folded section unfolds it, and one pointing at a
  // collapsed file opens it.
  function openHashTarget(): void {
    const h = location.hash.replace('#', '');
    if (h === '') return;
    const el = document.getElementById(h);
    if (el === null) return;
    const g = el.closest<HTMLElement>('.group');
    const unfold = g !== null && g !== el && g.classList.contains('folded');
    if (unfold) setFolded(g, false, true);
    if (el.classList.contains('file')) setCollapsed(el, false);
    if (unfold) el.scrollIntoView({ block: 'start' });
  }
  window.addEventListener('hashchange', openHashTarget);
  openHashTarget();
  refreshProgress();
}

const node = document.getElementById('summary-data');
if (node !== null) {
  renderSummary(JSON.parse(node.textContent !== '' ? node.textContent : '{}') as SummaryData);
  wireSummary();
} else {
  const src = document.getElementById('summary')?.dataset.src;
  fetch(src !== undefined && src !== '' ? src : 'data.json')
    .then(function (response) {
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
      return response.json() as Promise<SummaryData>;
    })
    .then(function (data) {
      renderSummary(data);
      wireSummary();
    }, function (error: unknown) {
      (document.getElementById('summary') ?? document.body).textContent =
        `This summary could not load its data: ${error instanceof Error ? error.message : String(error)}`;
    });
}
})();
