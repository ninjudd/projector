"use strict";
// Projector PR summary renderer: builds the page from summary data, embedded in
// #summary-data or fetched from data.json beside the page, then wires collapsing,
// reviewed state, highlighting, and sticky headers.
(function () {
    function renderSummary(data) {
        const pr = data.pr;
        const repoUrl = `https://github.com/${pr.repo}`;
        const prUrl = `${repoUrl}/pull/${String(pr.number)}`;
        const prRef = `${pr.repo.slice(pr.repo.lastIndexOf('/') + 1)}#${String(pr.number)}`;
        const baseRef = pr.baseRef !== undefined && pr.baseRef !== '' ? pr.baseRef : null;
        const filesByPath = {};
        data.files.forEach(function (f) { filesByPath[f.path] = f; });
        function esc(s) {
            return (s == null ? '' : String(s)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }
        function num(n) { return n.toLocaleString('en-US'); }
        function short(sha) { return (sha ?? '').slice(0, 9); }
        function lineNumber(n) { return n === null || n === 0 ? '' : String(n); }
        function ext(href, text, cls) {
            return `<a${cls !== undefined && cls !== '' ? ` class="${cls}"` : ''} href="${esc(href)}" target="_blank" rel="noopener">${text}</a>`;
        }
        // The build checks every file a group names against the diff.
        function fileAt(path) {
            const f = filesByPath[path];
            if (f === undefined)
                throw new Error(`The summary names ${path}, which is not in its diff`);
            return f;
        }
        const COPY_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path class="ic-copy" fill="currentColor" d="M0 6.75C0 5.78.78 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .14.11.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25Zm5-5C5 .78 5.78 0 6.75 0h7.5C15.22 0 16 .78 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .14.11.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z"/><path class="ic-ok" fill="currentColor" d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z"/></svg>';
        const CHIPS = { context: 'context', verify: 'verify', flag: 'concern' };
        function kindOf(c) { return c.kind === 'flag' || c.kind === 'context' ? c.kind : 'verify'; }
        // A note's checkbox state is keyed by where it sits and what it says, so a
        // note carried unchanged to a later head stays checked and a rewritten one
        // starts over.
        function noteId(scope, text) {
            const key = `${scope}\u0000${text}`;
            let h = 0x811c9dc5;
            for (let i = 0; i < key.length; i++)
                h = Math.imul(h ^ key.charCodeAt(i), 0x01000193) >>> 0;
            return h.toString(16).padStart(8, '0');
        }
        function notesList(checks, cls, scope) {
            if (checks.length === 0)
                return '';
            return `<ul class="notes ${cls}">` + checks.map(function (c) {
                const k = kindOf(c);
                const label = k === 'flag' ? 'Handled' : 'Verified';
                const box = k === 'context' ? '<span class="nbox"></span>'
                    : `<input type="checkbox" class="nbox note-box" data-nid="${noteId(scope, c.text)}" aria-label="${label}" title="${label}">`;
                return `<li class="${k}">${box}<span class="chip ${k}">${CHIPS[k] ?? k}</span><span class="ntext">${c.text}</span></li>`;
            }).join('') + '</ul>';
        }
        function renderFile(spec) {
            const f = fileAt(spec.path);
            const collapsed = spec.collapsed ?? (f.kind === 'generated' || f.kind === 'test' || f.kind === 'docs');
            const slash = f.path.lastIndexOf('/');
            const dir = slash >= 0 ? f.path.slice(0, slash + 1) : '';
            const base = f.path.slice(slash + 1);
            const checks = spec.checks ?? [];
            // A note with a line sits under that line of the diff; the rest sit at the top of the file.
            const atLine = {};
            const loose = [];
            checks.forEach(function (c) {
                if (c.line === undefined) {
                    loose.push(c);
                    return;
                }
                const key = `${c.side === 'old' ? 'old' : 'new'}:${String(c.line)}`;
                (atLine[key] ?? (atLine[key] = [])).push(c);
            });
            function notesAt(l) {
                const keys = l[0] === 'a' ? [`new:${String(l[2])}`] : l[0] === 'd' ? [`old:${String(l[1])}`] : [`new:${String(l[2])}`, `old:${String(l[1])}`];
                const found = keys.flatMap(function (k) { return atLine[k] ?? []; });
                return found.length > 0 ? `<tr class="noterow"><td class="ln" colspan="2"></td><td class="nte">${notesList(found, 'inotes', f.path)}</td></tr>` : '';
            }
            let badges = checks.length > 0 ? `<span class="badge notes" data-total="${String(checks.length)}">${String(checks.length)} note${checks.length === 1 ? '' : 's'}</span>` : '';
            if (f.new === true)
                badges += '<span class="badge new">new</span>';
            if (f.deleted === true)
                badges += '<span class="badge deleted">deleted</span>';
            if (f.kind !== '')
                badges += `<span class="badge ${f.kind}">${f.kind}</span>`;
            const hunks = f.hunks.map(function (h) {
                const rows = [`<tr class="hunk"><td class="ln" colspan="2"></td><td class="code">${esc(h.header)}</td></tr>`];
                h.lines.forEach(function (l) {
                    const t = l[0];
                    if (t === 'm') {
                        rows.push(`<tr class="meta"><td class="ln" colspan="2"></td><td class="code">${esc(l[3])}</td></tr>`);
                        return;
                    }
                    const cls = t === 'a' ? 'add' : t === 'd' ? 'del' : 'ctx';
                    const sign = t === 'a' ? '+' : t === 'd' ? '-' : ' ';
                    rows.push(`<tr class="${cls}"><td class="ln">${lineNumber(l[1])}</td><td class="ln">${lineNumber(l[2])}</td><td class="code"><span class="sign">${sign}</span><span class="src">${esc(l[3])}</span></td></tr>`);
                    rows.push(notesAt(l));
                });
                return `<table class="diff">${rows.join('')}</table>`;
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
                (spec.note !== undefined && spec.note !== '' ? `<p class="fnote">${spec.note}</p>` : '') +
                notesList(loose, 'fnotes', f.path) +
                `<div class="fbody" id="${f.id}-body">${hunks}</div>` +
                '</article>';
        }
        function renderGroup(g, i) {
            let adds = 0, dels = 0;
            g.files.forEach(function (s) { const f = fileAt(s.path); adds += f.adds; dels += f.dels; });
            const gid = esc(g.id);
            const intro = (g.intro ?? []).map(function (p) { return `<p>${p}</p>`; }).join('');
            const checks = g.checks ?? [];
            const notes = checks.length > 0 ? `<div class="gnotes"><h4>Review notes</h4>${notesList(checks, 'gnotelist', `group:${g.id}`)}</div>` : '';
            const list = g.files.map(function (s) { const f = fileAt(s.path); return `<li><a href="#${f.id}">${esc(f.path.split('/').pop())}</a></li>`; }).join('');
            return `<section class="group" id="${gid}" data-gid="${gid}">` +
                '<div class="gsentinel" aria-hidden="true"></div>' +
                '<header class="ghead">' +
                `<h2 class="gtitle"><button class="gtoggle" type="button" aria-expanded="true" aria-controls="${gid}-body" title="Collapse or expand">` +
                `<span class="chev" aria-hidden="true"></span><span class="gnum">${String(i + 1)}</span><span class="gname">${g.title}</span></button></h2>` +
                `<span class="gstats">${String(g.files.length)} file${g.files.length === 1 ? '' : 's'} · <span class="plus">+${num(adds)}</span> <span class="minus">−${num(dels)}</span></span>` +
                `<label class="reviewed"><input type="checkbox" class="reviewed-box" id="${gid}-reviewed"> Reviewed</label>` +
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
        function renderOverview() {
            const o = data.overview ?? {};
            const s = data.stats;
            const summary = (o.summary ?? []).map(function (p) { return `<p>${p}</p>`; }).join('');
            let out = `<div class="card prose"><h3>What this PR does</h3>${summary}` +
                '<p><b>How to read this.</b> Each section opens with what it does and why. Review notes come in three kinds: <span class="chip context inline">context</span> to hold in mind while you read, <span class="chip verify inline">verify</span> for an invariant worth tracing, and <span class="chip flag inline">concern</span> for something that may be wrong or risky and needs a decision. A note about one file sits at the top of that file, and a note about one line sits under that line in the diff. Check off verify and concern notes as you settle them; each file header counts the ones still open. Mark each file Reviewed as you go. Marking a section Reviewed closes it and its files, and a section is marked for you once all its files are. Checkboxes are remembered in this browser only.</p></div>';
            const tiles = [[num(s.files), 'files'], [`<span class="plus">+${num(s.adds)}</span> <span class="minus">−${num(s.dels)}</span>`, 'lines'],
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
            out += '<div class="legend"><span><span class="badge new">new</span> new file</span><span><span class="badge generated">generated</span> generated output</span><span><span class="badge test">test</span> collapsed by default</span><span><span class="badge docs">docs</span> collapsed by default</span><span>Click a file or section header to expand or collapse it; "Reviewed" collapses it and remembers that.</span></div>';
            return out;
        }
        function headsList() {
            const heads = data.heads ?? [];
            if (heads.length < 2)
                return '';
            return '<div class="prmeta">Versions: ' + heads.map(function (h) {
                return h.current ? `<b class="mono">${short(h.head)}</b>` : `<a href="${esc(h.url)}">${short(h.head)}</a>`;
            }).join(' · ') + '</div>';
        }
        function projectsList() {
            const projects = data.projects ?? [];
            if (projects.length === 0)
                return '';
            return '<div class="prmeta">Projects: ' + projects.map(function (p) {
                return `<a href="${esc(p.url)}">${esc(p.title)}</a>`;
            }).join(' · ') + '</div>';
        }
        function renderPage() {
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
                `<div class="prblock">${ext(prUrl, esc(prRef), 'prref')}` +
                `<div class="prtitle" title="${esc(pr.title)}">${esc(pr.title)}</div>` +
                `<div class="prmeta">${ext(`${repoUrl}/commit/${pr.head}`, short(pr.head))} → ${esc(baseRef ?? 'base')} · ${ext(`${prUrl}/files`, 'files')}</div>${headsList()}${projectsList()}</div>` +
                `<div class="extra">${extra}</div>` +
                `<div class="progress"><span>Reviewed</span><b id="progress-count">0 / ${String(data.groups.length)}</b></div><div class="bar"><i id="progress-bar"></i></div>` +
                `<ol>${nav}</ol></nav></aside>` +
                `<main><div class="overview" id="overview">${renderOverview()}</div>` +
                `<div class="groups">${data.groups.map(renderGroup).join('')}</div>` +
                `<footer>Generated from the diff at head <span class="mono">${short(pr.head)}</span> on ${esc(data.generatedAt ?? '')} by Projector's <span class="mono">summarize-changes</span> skill. Syntax colours come from highlight.js; green and red row tints mark added and removed lines.</footer>` +
                '</main></div></div>';
        }
        const root = document.getElementById('summary') ?? document.body;
        root.innerHTML = renderPage();
        window.__SUMMARY_KEY__ = `summary:${pr.repo}#${String(pr.number)}:`;
    }
    function wireSummary() {
        const storedKey = window.__SUMMARY_KEY__;
        const KEY = storedKey !== undefined && storedKey !== '' ? storedKey : 'summary:';
        function get(k) { try {
            return localStorage.getItem(KEY + k);
        }
        catch {
            return null;
        } }
        function set(k, v) { try {
            if (v)
                localStorage.setItem(KEY + k, '1');
            else
                localStorage.removeItem(KEY + k);
        }
        catch { /* without storage the state lasts only this visit */ } }
        const groups = Array.from(document.querySelectorAll('.group'));
        const progressEl = document.getElementById('progress-count');
        const barEl = document.getElementById('progress-bar');
        function refreshProgress() {
            let done = 0;
            groups.forEach(function (g) {
                const isDone = g.classList.contains('done');
                if (isDone)
                    done++;
                const dot = document.querySelector(`.ncheck[data-gid="${g.dataset.gid ?? ''}"]`);
                if (dot !== null)
                    dot.classList.toggle('done', isDone);
            });
            if (progressEl !== null)
                progressEl.textContent = `${String(done)} / ${String(groups.length)}`;
            if (barEl !== null)
                barEl.style.width = `${String(groups.length > 0 ? (100 * done / groups.length) : 0)}%`;
        }
        function filesOf(g) { return Array.from(g.querySelectorAll('.file')); }
        function fileBox(f) {
            const box = f.querySelector('.file-box');
            if (box === null)
                throw new Error(`File ${f.dataset.fid ?? ''} has no Reviewed box`);
            return box;
        }
        function groupBox(g) {
            const box = g.querySelector('.reviewed-box');
            if (box === null)
                throw new Error(`Section ${g.dataset.gid ?? ''} has no Reviewed box`);
            return box;
        }
        function setCollapsed(f, collapsed) {
            f.classList.toggle('collapsed', collapsed);
            if (collapsed)
                f.classList.remove('stuck');
            const btn = f.querySelector('.ftoggle');
            if (btn !== null)
                btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            if (!collapsed)
                highlightFile(f);
        }
        // Reviewing a file collapses it; un-reviewing one opens it to be read again.
        function markFile(f, reviewed) {
            fileBox(f).checked = reviewed;
            f.classList.toggle('reviewed', reviewed);
            set('f:' + (f.dataset.fid ?? ''), reviewed);
            setCollapsed(f, reviewed);
        }
        function setFolded(g, folded, highlight) {
            g.classList.toggle('folded', folded);
            const toggle = g.querySelector('.gtoggle');
            if (toggle !== null)
                toggle.setAttribute('aria-expanded', folded ? 'false' : 'true');
            if (!folded && highlight)
                g.querySelectorAll('.file:not(.collapsed)').forEach(highlightFile);
        }
        function allFilesReviewed(g) {
            const files = filesOf(g);
            return files.length > 0 && files.every(function (f) { return fileBox(f).checked; });
        }
        // A section's Reviewed box is its own: checking it closes the section and its
        // files without marking any file reviewed. The last file reviewed checks it,
        // and a file un-reviewed unchecks it.
        function markSection(g, reviewed) {
            set('g:' + (g.dataset.gid ?? ''), reviewed);
            g.classList.toggle('done', reviewed);
            groupBox(g).checked = reviewed;
            refreshProgress();
        }
        function closeSection(g, fromReader) {
            const inside = g.getBoundingClientRect().top < 0;
            filesOf(g).forEach(function (f) { setCollapsed(f, true); });
            setFolded(g, true, false);
            // Closed from inside it: bring its header back into view so the next section follows.
            if (fromReader && inside)
                g.scrollIntoView({ block: 'start' });
        }
        groups.forEach(function (g) {
            const gid = g.dataset.gid ?? '';
            const files = filesOf(g);
            const box = groupBox(g);
            const toggle = g.querySelector('.gtoggle');
            const expandAll = g.querySelector('.expand-all');
            const collapseAll = g.querySelector('.collapse-all');
            if (toggle === null || expandAll === null || collapseAll === null)
                throw new Error(`Section ${gid} is missing its controls`);
            files.forEach(function (f) {
                const reviewed = get('f:' + (f.dataset.fid ?? '')) === '1';
                fileBox(f).checked = reviewed;
                f.classList.toggle('reviewed', reviewed);
                if (reviewed)
                    setCollapsed(f, true);
            });
            const reviewed = get('g:' + gid) === '1' || allFilesReviewed(g);
            markSection(g, reviewed);
            if (reviewed)
                closeSection(g, false);
            box.addEventListener('change', function () {
                markSection(g, box.checked);
                if (box.checked) {
                    closeSection(g, true);
                }
                else {
                    setFolded(g, false, false);
                    files.forEach(function (f) { setCollapsed(f, fileBox(f).checked || f.dataset.collapsedDefault === '1'); });
                }
            });
            toggle.addEventListener('click', function () { setFolded(g, !g.classList.contains('folded'), true); });
            expandAll.addEventListener('click', function () { files.forEach(function (f) { setCollapsed(f, false); }); });
            collapseAll.addEventListener('click', function () { files.forEach(function (f) { setCollapsed(f, true); }); });
        });
        document.querySelectorAll('.file').forEach(function (f) {
            const box = fileBox(f);
            const toggle = f.querySelector('.ftoggle');
            const g = f.closest('.group');
            if (toggle === null || g === null)
                throw new Error(`File ${f.dataset.fid ?? ''} is missing its controls`);
            box.addEventListener('change', function () {
                const wasStuck = f.classList.contains('stuck');
                markFile(f, box.checked);
                // Marked reviewed from a header pinned over a long file: bring the
                // collapsed card into view instead of landing on what followed it.
                if (box.checked && wasStuck)
                    f.scrollIntoView({ block: 'start' });
                if (box.checked && !g.classList.contains('done') && allFilesReviewed(g)) {
                    markSection(g, true);
                    closeSection(g, true);
                }
                else if (!box.checked && g.classList.contains('done')) {
                    markSection(g, false);
                }
            });
            toggle.addEventListener('click', function () {
                setCollapsed(f, !f.classList.contains('collapsed'));
            });
        });
        // A file's note badge counts its verify and concern notes still unchecked.
        function refreshNoteBadge(f) {
            const badge = f.querySelector('.badge.notes');
            if (badge === null)
                return;
            const boxes = Array.from(f.querySelectorAll('.note-box'));
            const open = boxes.filter(function (b) { return !b.checked; }).length;
            const total = Number(badge.dataset.total ?? '0');
            badge.textContent = open > 0 ? `${String(open)} of ${String(boxes.length)} open` : `${String(total)} note${total === 1 ? '' : 's'}`;
            badge.classList.toggle('open', open > 0);
        }
        document.querySelectorAll('.note-box').forEach(function (box) {
            const nid = box.dataset.nid ?? '';
            const item = box.closest('li');
            box.checked = get('n:' + nid) === '1';
            if (item !== null)
                item.classList.toggle('checked', box.checked);
            box.addEventListener('change', function () {
                set('n:' + nid, box.checked);
                if (item !== null)
                    item.classList.toggle('checked', box.checked);
                const f = box.closest('.file');
                if (f !== null)
                    refreshNoteBadge(f);
            });
        });
        document.querySelectorAll('.file').forEach(refreshNoteBadge);
        document.querySelectorAll('.copypath').forEach(function (b) {
            b.addEventListener('click', function (ev) {
                ev.stopPropagation();
                const path = b.dataset.path ?? '';
                function done() { b.classList.add('copied'); b.title = 'Copied'; setTimeout(function () { b.classList.remove('copied'); b.title = 'Copy file path'; }, 1500); }
                // The write fails where the Clipboard API is missing or refused: outside a
                // secure context, in an unfocused document, when permission is denied, or
                // on a host that blocks it, as some Claude Artifact views do, which also
                // suppress dialogs. So show the path beside the button, selected for the
                // reader to copy, rather than prompting or calling the deprecated
                // document.execCommand('copy'). It goes away when focus leaves it.
                function fallback() {
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
                try {
                    navigator.clipboard.writeText(path).then(done, fallback);
                }
                catch {
                    fallback();
                }
            });
        });
        // Pinned-header detection: a 1px sentinel sits at the top of each section and
        // each file card. Once it scrolls above its header's sticky offset the header
        // is pinned: a section header gains a shadow, and a file header, pinned just
        // below its section's, goes square so the card's borders run into it. Each
        // section measures its own header, since titles wrap to different heights.
        let stuckObservers = [];
        function topOf(el) { return el !== null ? (parseFloat(getComputedStyle(el).top) || 0) : 0; }
        function observe(targets, offset, onChange) {
            const observer = new IntersectionObserver(function (entries) {
                entries.forEach(function (e) { onChange(e.target, !e.isIntersecting && e.boundingClientRect.top < offset + 1); });
            }, { rootMargin: `${String(-(offset + 1))}px 0px 0px 0px`, threshold: 0 });
            targets.forEach(function (t) { observer.observe(t); });
            stuckObservers.push(observer);
        }
        function setupStuck() {
            groups.forEach(function (g) {
                const head = g.querySelector('.ghead');
                if (head !== null)
                    g.style.setProperty('--ghead-h', `${String(head.offsetHeight)}px`);
            });
            if (!('IntersectionObserver' in window))
                return;
            stuckObservers.forEach(function (o) { o.disconnect(); });
            stuckObservers = [];
            groups.forEach(function (g) {
                const head = g.querySelector('.ghead');
                const top = topOf(head);
                const sentinel = g.querySelector('.gsentinel');
                if (sentinel !== null)
                    observe([sentinel], top, function (_t, pinned) { g.classList.toggle('gstuck', pinned); });
                const sentinels = Array.from(g.querySelectorAll('.fsentinel'));
                if (sentinels.length === 0)
                    return;
                observe(sentinels, top + (head !== null ? head.offsetHeight : 0), function (t, pinned) {
                    const f = t.parentElement;
                    if (f === null)
                        throw new Error('A header sentinel sits outside its file');
                    f.classList.toggle('stuck', pinned && !f.classList.contains('collapsed'));
                });
            });
        }
        setupStuck();
        void document.fonts.ready.then(setupStuck);
        let resizeTimer;
        window.addEventListener('resize', function () {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(setupStuck, 200);
        });
        // Syntax highlighting: each hunk is highlighted as two whole texts (the
        // old side: context + removed lines; the new side: context + added lines)
        // so tokens that span lines survive, then split back into rows. Files that
        // start collapsed are highlighted when first opened.
        function splitLines(s) {
            const out = [], open = [];
            let cur = '';
            const re = /<span class="[^"]*">|<\/span>|\n|[^<\n]+|</g;
            let m;
            while ((m = re.exec(s)) !== null) {
                const t = m[0];
                if (t === '\n') {
                    cur += '</span>'.repeat(open.length);
                    out.push(cur);
                    cur = open.join('');
                }
                else if (t === '</span>') {
                    open.pop();
                    cur += t;
                }
                else if (t.length > 1 && t.startsWith('<')) {
                    open.push(t);
                    cur += t;
                }
                else {
                    cur += t;
                }
            }
            cur += '</span>'.repeat(open.length);
            out.push(cur);
            return out;
        }
        function hl(text, lang) {
            const hljs = window.hljs;
            if (hljs === undefined)
                return null;
            try {
                return splitLines(hljs.highlight(text, { language: lang, ignoreIllegals: true }).value);
            }
            catch {
                return null;
            }
        }
        function highlightFile(f) {
            const hljs = window.hljs;
            if ((f.dataset.hl ?? '') !== '' || hljs === undefined)
                return;
            const lang = f.dataset.lang;
            if (lang === undefined || lang === '' || hljs.getLanguage(lang) === undefined) {
                f.dataset.hl = '1';
                return;
            }
            f.querySelectorAll('table.diff').forEach(function (tbl) {
                const rows = Array.from(tbl.querySelectorAll('tr.add, tr.del, tr.ctx'));
                const oldT = [], newT = [], oldR = [], newR = [];
                rows.forEach(function (r) {
                    const src = r.querySelector('.src');
                    const text = src !== null ? src.textContent : '';
                    if (r.classList.contains('add')) {
                        newR.push(src);
                        newT.push(text);
                    }
                    else if (r.classList.contains('del')) {
                        oldR.push(src);
                        oldT.push(text);
                    }
                    else {
                        newR.push(src);
                        newT.push(text);
                        oldR.push(null);
                        oldT.push(text);
                    }
                });
                const newH = newT.length > 0 ? hl(newT.join('\n'), lang) : [];
                const oldH = oldT.length > 0 ? hl(oldT.join('\n'), lang) : [];
                if (newH?.length === newR.length)
                    newR.forEach(function (s, k) { const h = newH[k]; if (s !== null && h !== undefined)
                        s.innerHTML = h; });
                if (oldH?.length === oldR.length)
                    oldR.forEach(function (s, k) { const h = oldH[k]; if (s !== null && h !== undefined)
                        s.innerHTML = h; });
            });
            f.dataset.hl = '1';
        }
        const hlQueue = Array.from(document.querySelectorAll('.group:not(.folded) .file:not(.collapsed)'));
        function pump() {
            const t0 = Date.now();
            while (hlQueue.length > 0 && Date.now() - t0 < 24) {
                const next = hlQueue.shift();
                if (next !== undefined)
                    highlightFile(next);
            }
            if (hlQueue.length > 0)
                setTimeout(pump, 0);
        }
        if (window.hljs !== undefined)
            setTimeout(pump, 0);
        // A hash pointing into a folded section unfolds it, and one pointing at a
        // collapsed file opens it.
        function openHashTarget() {
            const h = location.hash.replace('#', '');
            if (h === '')
                return;
            const el = document.getElementById(h);
            if (el === null)
                return;
            const g = el.closest('.group');
            const unfold = g !== null && g !== el && g.classList.contains('folded');
            if (unfold)
                setFolded(g, false, true);
            if (el.classList.contains('file'))
                setCollapsed(el, false);
            if (unfold)
                el.scrollIntoView({ block: 'start' });
        }
        window.addEventListener('hashchange', openHashTarget);
        openHashTarget();
        refreshProgress();
    }
    const node = document.getElementById('summary-data');
    if (node !== null) {
        renderSummary(JSON.parse(node.textContent !== '' ? node.textContent : '{}'));
        wireSummary();
    }
    else {
        const src = document.getElementById('summary')?.dataset.src;
        fetch(src !== undefined && src !== '' ? src : 'data.json')
            .then(function (response) {
            if (!response.ok)
                throw new Error(`HTTP ${String(response.status)}`);
            return response.json();
        })
            .then(function (data) {
            renderSummary(data);
            wireSummary();
        }, function (error) {
            (document.getElementById('summary') ?? document.body).textContent =
                `This summary could not load its data: ${error instanceof Error ? error.message : String(error)}`;
        });
    }
})();
