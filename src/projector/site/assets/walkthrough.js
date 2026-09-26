"use strict";
// Projector PR walkthrough renderer: builds the page from walkthrough data, embedded in
// #walkthrough-data or fetched from data.json beside the page, then wires collapsing,
// viewed and reviewed state, highlighting, and sticky headers.
(function () {
    function renderWalkthrough(data) {
        const pr = data.pr;
        const repoUrl = 'https://github.com/' + pr.repo;
        const prUrl = repoUrl + '/pull/' + pr.number;
        const prRef = pr.repo.split('/').pop() + '#' + pr.number;
        const filesByPath = {};
        data.files.forEach(function (f) { filesByPath[f.path] = f; });
        function esc(s) {
            return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }
        function num(n) { return Number(n).toLocaleString('en-US'); }
        function short(sha) { return String(sha || '').slice(0, 9); }
        function ext(href, text, cls) {
            return '<a' + (cls ? ' class="' + cls + '"' : '') + ' href="' + esc(href) + '" target="_blank" rel="noopener">' + text + '</a>';
        }
        const COPY_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path class="ic-copy" fill="currentColor" d="M0 6.75C0 5.78.78 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .14.11.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25Zm5-5C5 .78 5.78 0 6.75 0h7.5C15.22 0 16 .78 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .14.11.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z"/><path class="ic-ok" fill="currentColor" d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z"/></svg>';
        function renderFile(spec) {
            const f = filesByPath[spec.path];
            const collapsed = spec.collapsed != null ? spec.collapsed : (f.kind === 'generated' || f.kind === 'test' || f.kind === 'docs');
            const slash = f.path.lastIndexOf('/');
            const dir = slash >= 0 ? f.path.slice(0, slash + 1) : '';
            const base = f.path.slice(slash + 1);
            let badges = '';
            if (f.new)
                badges += '<span class="badge new">new</span>';
            if (f.deleted)
                badges += '<span class="badge deleted">deleted</span>';
            if (f.kind)
                badges += '<span class="badge ' + f.kind + '">' + f.kind + '</span>';
            const hunks = f.hunks.map(function (h) {
                const rows = ['<tr class="hunk"><td class="ln" colspan="2"></td><td class="code">' + esc(h.header) + '</td></tr>'];
                h.lines.forEach(function (l) {
                    const t = l[0];
                    if (t === 'm') {
                        rows.push('<tr class="meta"><td class="ln" colspan="2"></td><td class="code">' + esc(l[3]) + '</td></tr>');
                        return;
                    }
                    const cls = t === 'a' ? 'add' : t === 'd' ? 'del' : 'ctx';
                    const sign = t === 'a' ? '+' : t === 'd' ? '-' : ' ';
                    rows.push('<tr class="' + cls + '"><td class="ln">' + (l[1] || '') + '</td><td class="ln">' + (l[2] || '') + '</td><td class="code"><span class="sign">' + sign + '</span><span class="src">' + esc(l[3]) + '</span></td></tr>');
                });
                return '<table class="diff">' + rows.join('') + '</table>';
            }).join('') || '<p class="fnote">No content changes.</p>';
            return '<article class="file' + (collapsed ? ' collapsed' : '') + '" id="' + f.id + '" data-fid="' + f.id + '" data-lang="' + esc(f.lang) + '">' +
                '<div class="fsentinel" aria-hidden="true"></div>' +
                '<header class="fhead">' +
                '<button class="ftoggle" type="button" aria-expanded="' + (collapsed ? 'false' : 'true') + '" aria-controls="' + f.id + '-body" title="Collapse or expand">' +
                '<span class="chev" aria-hidden="true"></span>' +
                '<span class="fpath"><span class="dir">' + esc(dir) + '</span><span class="base">' + esc(base) + '</span></span>' +
                '</button>' +
                '<button class="copypath" type="button" data-path="' + esc(f.path) + '" title="Copy file path" aria-label="Copy file path">' + COPY_ICON + '</button>' +
                '<span class="fmeta">' + badges +
                '<span class="stat"><span class="plus">+' + f.adds + '</span> <span class="minus">−' + f.dels + '</span></span>' +
                ext(prUrl + '/files#' + f.anchor, 'PR', 'flink') +
                ext(repoUrl + '/blob/' + pr.head + '/' + f.path, 'file', 'flink') +
                '<label class="viewed"><input type="checkbox" class="viewed-box" id="' + f.id + '-viewed"> viewed</label>' +
                '</span>' +
                '</header>' +
                (spec.note ? '<p class="fnote">' + spec.note + '</p>' : '') +
                '<div class="fbody" id="' + f.id + '-body">' + hunks + '</div>' +
                '</article>';
        }
        function renderGroup(g, i) {
            let adds = 0, dels = 0;
            g.files.forEach(function (s) { adds += filesByPath[s.path].adds; dels += filesByPath[s.path].dels; });
            const intro = (g.intro || []).map(function (p) { return '<p>' + p + '</p>'; }).join('');
            const concepts = (g.concepts && g.concepts.length) ? '<div class="concepts"><h4>Hold these in mind</h4><ul>' + g.concepts.map(function (c) { return '<li>' + c + '</li>'; }).join('') + '</ul></div>' : '';
            const checks = (g.checks && g.checks.length) ? '<div class="checks"><h4>What to check</h4><ul>' + g.checks.map(function (c) {
                const k = c.kind === 'flag' ? 'flag' : 'verify';
                return '<li class="' + k + '"><span class="chip ' + k + '">' + (k === 'flag' ? 'look here' : 'verify') + '</span><span>' + c.text + '</span></li>';
            }).join('') + '</ul></div>' : '';
            const list = g.files.map(function (s) { const f = filesByPath[s.path]; return '<li><a href="#' + f.id + '">' + esc(f.path.split('/').pop()) + '</a></li>'; }).join('');
            return '<section class="group" id="' + esc(g.id) + '" data-gid="' + esc(g.id) + '">' +
                '<header class="ghead"><div class="gnum">' + (i + 1) + '</div><div class="gtitle"><h2>' + g.title + '</h2>' +
                (g.kicker ? '<p class="kicker">' + g.kicker + '</p>' : '') +
                '<p class="gstats">' + g.files.length + ' file' + (g.files.length === 1 ? '' : 's') + ' · <span class="plus">+' + num(adds) + '</span> <span class="minus">−' + num(dels) + '</span></p></div>' +
                '<label class="reviewed"><input type="checkbox" class="reviewed-box" id="' + esc(g.id) + '-reviewed"> Reviewed</label></header>' +
                '<div class="prose">' + intro + '</div>' + concepts + checks +
                '<div class="filebar"><span class="filebar-label">Files in this group</span><ul class="filelist">' + list + '</ul>' +
                '<span class="filebar-actions"><button type="button" class="linkbtn expand-all">Expand all</button><button type="button" class="linkbtn collapse-all">Collapse all</button></span></div>' +
                '<div class="files">' + g.files.map(renderFile).join('') + '</div>' +
                '</section>';
        }
        function renderOverview() {
            const o = data.overview || {};
            const s = data.stats;
            const summary = (o.summary || []).map(function (p) { return '<p>' + p + '</p>'; }).join('');
            let out = '<div class="card prose"><h3>What this PR does</h3>' + summary +
                '<p><b>How to read this.</b> Each group opens with what it does and why, things to hold in mind, and a checklist. Items marked <span class="chip flag inline">look here</span> deserve a decision; <span class="chip verify inline">verify</span> items are invariants worth tracing. Checkboxes and collapsed files are remembered in this browser only.</p></div>';
            const tiles = [[num(s.files), 'files'], ['<span class="plus">+' + num(s.adds) + '</span> <span class="minus">−' + num(s.dels) + '</span>', 'lines'],
                [num(s.hand), 'hand-written lines'], [num(s.test), 'test lines'], [num(s.generated), 'generated (collapsed)'], [num(s.docs), 'documentation lines']];
            out += '<div class="card"><h3>Shape of the change</h3><div class="statgrid">' +
                tiles.map(function (t) { return '<div class="stat-tile"><div class="v">' + t[0] + '</div><div class="l">' + t[1] + '</div></div>'; }).join('') +
                '</div></div>';
            const cards = o.cards || [];
            if (cards.length) {
                out += '<div class="twocol">' + cards.map(function (c) {
                    return '<div class="card"' + (c.id ? ' id="' + esc(c.id) + '"' : '') + '><h3>' + c.title + '</h3>' + c.html + '</div>';
                }).join('') + '</div>';
            }
            out += '<div class="legend"><span><span class="badge new">new</span> new file</span><span><span class="badge generated">generated</span> generated output</span><span><span class="badge test">test</span> collapsed by default</span><span><span class="badge docs">docs</span> collapsed by default</span><span>Click a file header to expand or collapse it; "viewed" collapses it and remembers that.</span></div>';
            return out;
        }
        function headsList() {
            const heads = data.heads || [];
            if (heads.length < 2)
                return '';
            return '<div class="prmeta">Versions: ' + heads.map(function (h) {
                return h.current ? '<b class="mono">' + short(h.head) + '</b>' : '<a href="' + esc(h.url) + '">' + short(h.head) + '</a>';
            }).join(' · ') + '</div>';
        }
        function projectsList() {
            const projects = data.projects || [];
            if (!projects.length)
                return '';
            return '<div class="prmeta">Projects: ' + projects.map(function (p) {
                return '<a href="' + esc(p.url) + '">' + esc(p.title) + '</a>';
            }).join(' · ') + '</div>';
        }
        function renderPage() {
            const nav = data.groups.map(function (g, i) {
                return '<li><a href="#' + esc(g.id) + '"><span class="nnum">' + (i + 1) + '</span><span class="ntitle">' + g.title + '</span><span class="ncheck" data-gid="' + esc(g.id) + '"></span></a></li>';
            }).join('');
            const extra = '<a href="#overview">Overview</a>' + (data.overview && data.overview.cards || []).filter(function (c) { return c.id; }).map(function (c) {
                return '<a href="#' + esc(c.id) + '">' + c.title + '</a>';
            }).join('');
            const title = esc(data.name || ('Review of ' + prRef));
            return '<div class="wrap">' +
                '<div class="prbar">' + ext(prUrl, esc(prRef), 'prref') + '<span class="prname">' + title + '</span></div>' +
                '<header class="top"><div><div class="eyebrow">' + ext(prUrl, esc(prRef)) + ' · head <span class="mono">' + short(pr.head) + '</span> on ' + esc(pr.baseRef || 'base') + '</div><h1>' + title + '</h1></div>' +
                '<div class="sub">' + (data.indexUrl ? '<a href="' + esc(data.indexUrl) + '">All reviews</a> · ' : '') + ext(prUrl, 'Open on GitHub') + ' · ' + ext(prUrl + '/files', 'Files tab') + ' · ' + ext(repoUrl + '/compare/' + encodeURIComponent(pr.baseRef || 'main') + '...' + pr.head, 'Compare') + '</div></header>' +
                '<div class="layout"><aside class="side"><nav class="nav" aria-label="Groups">' +
                '<div class="prblock">' + ext(prUrl, esc(prRef), 'prref') +
                '<div class="prtitle" title="' + esc(pr.title) + '">' + esc(pr.title) + '</div>' +
                '<div class="prmeta">' + ext(repoUrl + '/commit/' + pr.head, short(pr.head)) + ' → ' + esc(pr.baseRef || 'base') + ' · ' + ext(prUrl + '/files', 'files') + '</div>' + headsList() + projectsList() + '</div>' +
                '<div class="progress"><span>Reviewed</span><b id="progress-count">0 / ' + data.groups.length + '</b></div><div class="bar"><i id="progress-bar"></i></div>' +
                '<ol>' + nav + '</ol><div class="extra">' + extra + '</div></nav></aside>' +
                '<main><div class="overview" id="overview">' + renderOverview() + '</div>' +
                '<div class="groups">' + data.groups.map(renderGroup).join('') + '</div>' +
                '<footer>Generated from the diff at head <span class="mono">' + short(pr.head) + '</span> on ' + esc(data.generatedAt || '') + ' by Projector\'s <span class="mono">walkthrough-pr</span> skill. Syntax colours come from highlight.js; green and red row tints mark added and removed lines.</footer>' +
                '</main></div></div>';
        }
        const root = document.getElementById('walkthrough') || document.body;
        root.innerHTML = renderPage();
        window.__WALKTHROUGH_KEY__ = 'walkthrough:' + pr.repo + '#' + pr.number + ':';
    }
    function wireWalkthrough() {
        const KEY = window.__WALKTHROUGH_KEY__ || 'walkthrough:';
        function get(k) { try {
            return localStorage.getItem(KEY + k);
        }
        catch (e) {
            return null;
        } }
        function set(k, v) { try {
            if (v)
                localStorage.setItem(KEY + k, '1');
            else
                localStorage.removeItem(KEY + k);
        }
        catch (e) { } }
        const groups = Array.from(document.querySelectorAll('.group'));
        const progressEl = document.getElementById('progress-count');
        const barEl = document.getElementById('progress-bar');
        function refreshProgress() {
            let done = 0;
            groups.forEach(function (g) {
                const isDone = g.classList.contains('done');
                if (isDone)
                    done++;
                const dot = document.querySelector('.ncheck[data-gid="' + g.dataset.gid + '"]');
                if (dot)
                    dot.classList.toggle('done', isDone);
            });
            if (progressEl)
                progressEl.textContent = done + ' / ' + groups.length;
            if (barEl)
                barEl.style.width = (groups.length ? (100 * done / groups.length) : 0) + '%';
        }
        groups.forEach(function (g) {
            const box = g.querySelector('.reviewed-box');
            const saved = get('g:' + g.dataset.gid) === '1';
            box.checked = saved;
            g.classList.toggle('done', saved);
            box.addEventListener('change', function () {
                g.classList.toggle('done', box.checked);
                set('g:' + g.dataset.gid, box.checked);
                refreshProgress();
            });
            g.querySelector('.expand-all').addEventListener('click', function () {
                g.querySelectorAll('.file').forEach(function (f) { setCollapsed(f, false); });
            });
            g.querySelector('.collapse-all').addEventListener('click', function () {
                g.querySelectorAll('.file').forEach(function (f) { setCollapsed(f, true); });
            });
        });
        function setCollapsed(f, collapsed) {
            f.classList.toggle('collapsed', collapsed);
            if (collapsed)
                f.classList.remove('stuck');
            const btn = f.querySelector('.ftoggle');
            if (btn)
                btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            if (!collapsed)
                highlightFile(f);
        }
        document.querySelectorAll('.file').forEach(function (f) {
            const fid = f.dataset.fid;
            const box = f.querySelector('.viewed-box');
            const viewed = get('f:' + fid) === '1';
            box.checked = viewed;
            if (viewed)
                setCollapsed(f, true);
            box.addEventListener('change', function () {
                set('f:' + fid, box.checked);
                const wasStuck = f.getBoundingClientRect().top < 0;
                setCollapsed(f, box.checked);
                // Marked viewed from a header stuck at the top of a long file: bring
                // the collapsed card into view instead of landing on what followed it.
                if (box.checked && wasStuck)
                    f.scrollIntoView({ block: 'start' });
            });
            f.querySelector('.ftoggle').addEventListener('click', function () {
                setCollapsed(f, !f.classList.contains('collapsed'));
            });
        });
        document.querySelectorAll('.copypath').forEach(function (b) {
            b.addEventListener('click', function (ev) {
                ev.stopPropagation();
                const path = b.dataset.path || '';
                function done() { b.classList.add('copied'); b.title = 'Copied'; setTimeout(function () { b.classList.remove('copied'); b.title = 'Copy file path'; }, 1500); }
                function fallback() {
                    const ta = document.createElement('textarea');
                    ta.value = path;
                    ta.setAttribute('readonly', '');
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    try {
                        if (document.execCommand('copy'))
                            done();
                    }
                    catch (e) { }
                    document.body.removeChild(ta);
                }
                try {
                    navigator.clipboard.writeText(path).then(done, fallback);
                }
                catch (e) {
                    fallback();
                }
            });
        });
        // Pinned-header detection: a 1px sentinel sits at the top of each card; once
        // it has scrolled above the header's sticky offset, the header is pinned and
        // its corners go square so the card's side borders run straight into it.
        let stuckObserver = null;
        function setupStuck() {
            if (!('IntersectionObserver' in window))
                return;
            if (stuckObserver)
                stuckObserver.disconnect();
            const sample = document.querySelector('.file:not(.collapsed) .fhead') || document.querySelector('.fhead');
            const offset = sample ? (parseFloat(getComputedStyle(sample).top) || 0) : 0;
            const observer = new IntersectionObserver(function (entries) {
                entries.forEach(function (e) {
                    const f = e.target.parentElement;
                    const pinned = !e.isIntersecting && e.boundingClientRect.top < offset + 1 && !f.classList.contains('collapsed');
                    f.classList.toggle('stuck', pinned);
                });
            }, { rootMargin: (-(offset + 1)) + 'px 0px 0px 0px', threshold: 0 });
            document.querySelectorAll('.fsentinel').forEach(function (el) { observer.observe(el); });
            stuckObserver = observer;
        }
        setupStuck();
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
                    for (let i = 0; i < open.length; i++)
                        cur += '</span>';
                    out.push(cur);
                    cur = open.join('');
                }
                else if (t === '</span>') {
                    open.pop();
                    cur += t;
                }
                else if (t.length > 1 && t.charAt(0) === '<') {
                    open.push(t);
                    cur += t;
                }
                else {
                    cur += t;
                }
            }
            for (let j = 0; j < open.length; j++)
                cur += '</span>';
            out.push(cur);
            return out;
        }
        function hl(text, lang) {
            try {
                return splitLines(window.hljs.highlight(text, { language: lang, ignoreIllegals: true }).value);
            }
            catch (e) {
                return null;
            }
        }
        function highlightFile(f) {
            const hljs = window.hljs;
            if (f.dataset.hl || !hljs)
                return;
            const lang = f.dataset.lang;
            if (!lang || !hljs.getLanguage(lang)) {
                f.dataset.hl = '1';
                return;
            }
            f.querySelectorAll('table.diff').forEach(function (tbl) {
                const rows = Array.from(tbl.querySelectorAll('tr.add, tr.del, tr.ctx'));
                const oldT = [], newT = [], oldR = [], newR = [];
                rows.forEach(function (r) {
                    const src = r.querySelector('.src');
                    const text = src ? src.textContent || '' : '';
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
                const newH = newT.length ? hl(newT.join('\n'), lang) : [];
                const oldH = oldT.length ? hl(oldT.join('\n'), lang) : [];
                if (newH && newH.length === newR.length)
                    newR.forEach(function (s, k) { if (s)
                        s.innerHTML = newH[k]; });
                if (oldH && oldH.length === oldR.length)
                    oldR.forEach(function (s, k) { if (s)
                        s.innerHTML = oldH[k]; });
            });
            f.dataset.hl = '1';
        }
        const hlQueue = Array.from(document.querySelectorAll('.file:not(.collapsed)'));
        function pump() {
            const t0 = Date.now();
            while (hlQueue.length && Date.now() - t0 < 24)
                highlightFile(hlQueue.shift());
            if (hlQueue.length)
                setTimeout(pump, 0);
        }
        if (window.hljs)
            setTimeout(pump, 0);
        // A hash pointing at a collapsed file opens it.
        function openHashTarget() {
            const h = location.hash.replace('#', '');
            if (!h)
                return;
            const el = document.getElementById(h);
            if (el && el.classList.contains('file'))
                setCollapsed(el, false);
        }
        window.addEventListener('hashchange', openHashTarget);
        openHashTarget();
        refreshProgress();
    }
    const node = document.getElementById('walkthrough-data');
    if (node) {
        renderWalkthrough(JSON.parse(node.textContent || '{}'));
        wireWalkthrough();
    }
    else {
        const mount = document.getElementById('walkthrough');
        fetch((mount && mount.dataset.src) || 'data.json')
            .then(function (response) {
            if (!response.ok)
                throw new Error('HTTP ' + response.status);
            return response.json();
        })
            .then(function (data) {
            renderWalkthrough(data);
            wireWalkthrough();
        }, function (error) {
            (document.getElementById('walkthrough') || document.body).textContent =
                'This walkthrough could not load its data: ' + error.message;
        });
    }
})();
