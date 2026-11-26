from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

from projector import artifact, artifact_panel, cli, console, inbox, refs


class ArtifactPanelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'commit.gpgsign', 'false')
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.root / 'source'
        artifact.init('explorer', self.source, 'Cache explorer', 'Explore saved data')
        self.save()
        artifact_panel.attach(self.repo, 'origin', 'one', 'explorer')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def save(self):
        return artifact.publish(artifact.snapshot(self.source), self.repo, 'origin', push=False)

    def listed(self, session='one'):
        return artifact_panel.listing(session)

    def clean_up_preview(self):
        registry = next((artifact_panel.watch.state_root() / 'artifact-previews').glob('*/preview.json'))
        pid = json.loads(registry.read_text())['pid']
        self.addCleanup(lambda: os.kill(pid, signal.SIGTERM))

    def panel(self, session, checkout=None):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(['artifact', 'panel', '--session', session, '--json',
                             '--checkout', str(checkout or self.repo)])
        self.assertEqual(0, code)
        return json.loads(output.getvalue())['artifacts']

    def test_sessions_only_list_artifacts_they_publish_or_explicitly_attach(self):
        self.assertEqual([], self.panel('unrelated'))
        tip = refs.ref_tip(self.repo, artifact.REF)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(['artifact', 'publish', '--dir', str(self.source), '--local',
                                         '--checkout', str(self.repo), '--session', 'publisher']))
        self.assertEqual(tip, refs.ref_tip(self.repo, artifact.REF))
        self.assertEqual(['explorer'], [row['id'] for row in self.panel('publisher')])
        self.assertEqual([], self.panel('unrelated'))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(['artifact', 'attach', 'explorer', '--checkout', str(self.repo),
                                         '--session', 'unrelated']))
        self.assertEqual(['explorer'], [row['id'] for row in self.panel('unrelated')])
        self.assertEqual(['explorer'], [row['id'] for row in self.panel('publisher', self.root)])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(['artifact', 'dismiss', 'explorer', '--checkout', str(self.repo),
                                         '--session', 'publisher']))
            self.assertEqual(0, cli.main(['artifact', 'publish', '--dir', str(self.source), '--local',
                                         '--checkout', str(self.repo), '--session', 'publisher']))
        self.assertEqual([], self.panel('publisher'))
        self.assertEqual(['explorer'], [row['id'] for row in self.panel('unrelated')])

    def test_a_session_keeps_its_artifact_after_the_publishing_worktree_is_removed(self):
        self.git('switch', '--orphan', 'fixture')
        self.git('commit', '--allow-empty', '-qm', 'Start fixture')
        worktree = self.root / 'publishing-worktree'
        self.git('worktree', 'add', '--detach', str(worktree), 'HEAD')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(['artifact', 'publish', '--dir', str(self.source), '--local',
                                         '--checkout', str(worktree), '--session', 'publisher']))
        self.git('worktree', 'remove', str(worktree))
        rows = self.panel('publisher')
        self.assertEqual(['explorer'], [row['id'] for row in rows])
        self.assertEqual(str(self.repo.resolve()), rows[0]['checkout'])

    def test_attached_artifacts_keep_dismissals_through_revisions_only_in_their_session(self):
        artifact_panel.attach(self.repo, 'origin', 'two', 'explorer')
        first = self.listed()[0]
        tip = refs.ref_tip(self.repo, artifact.REF)
        artifact_panel.dismiss(self.repo, 'origin', 'one', 'explorer')
        self.assertEqual([], self.listed())
        self.assertEqual(tip, refs.ref_tip(self.repo, artifact.REF))
        self.assertEqual('Cache explorer', self.listed('two')[0]['title'])
        meta = json.loads((self.source / 'artifact.json').read_text())
        meta['title'] = 'Revised explorer'
        (self.source / 'artifact.json').write_text(json.dumps(meta))
        self.save()
        self.assertEqual([], self.listed())
        self.assertEqual('Revised explorer', self.listed('two')[0]['title'])
        artifact_panel.attach(self.repo, 'origin', 'one', 'explorer', restore=True)
        self.assertEqual(first['key'], self.listed()[0]['key'])
        self.assertEqual('Revised explorer', self.listed()[0]['title'])

    def test_cached_metadata_does_not_load_payloads_and_invalid_latest_keeps_valid_neighbors(self):
        (self.source / 'index.html').write_text('<h1>Revised explorer</h1>')
        self.save()
        read = artifact.GitReader.read
        paths = []

        def recorded(reader, path, limit):
            paths.append(path)
            self.assertNotIn('/files/', path)
            return read(reader, path, limit)

        with mock.patch.object(artifact.GitReader, 'read', recorded):
            self.assertEqual('explorer', self.listed()[0]['id'])
        with mock.patch.object(artifact, 'GitReader', side_effect=AssertionError('unchanged refs were rescanned')):
            self.assertEqual('explorer', self.listed()[0]['id'])
        parent = refs.ref_tip(self.repo, artifact.REF)
        tree = refs.write_tree(parent, {'artifacts/broken/latest.json': b'{"version":"bad"}'},
                               run=lambda *args, **kwargs: refs.git(*args, root=self.repo, **kwargs))
        commit = self.git('commit-tree', tree, '-p', parent, '-m', 'Add invalid neighbor')
        self.git('update-ref', artifact.REF, commit)
        self.assertEqual(['explorer'], [row['id'] for row in self.listed()])
        self.assertTrue(paths)

    def test_local_open_serves_relative_binary_files_reuses_the_preview_and_checks_the_host(self):
        meta = json.loads((self.source / 'artifact.json').read_text())
        meta['files'].append('data.bin')
        (self.source / 'artifact.json').write_text(json.dumps(meta))
        (self.source / 'data.bin').write_bytes(b'\x00\xff\x00\r\n')
        self.save()
        opened = subprocess.run([sys.executable, '-m', 'projector', 'artifact', 'open', 'explorer',
                                 '--checkout', str(self.repo), '--print-url'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(0, opened.returncode, opened.stderr)
        url = opened.stdout.strip()
        self.clean_up_preview()
        with urllib.request.urlopen(url) as response:
            self.assertIn(b'Cache explorer', response.read())
        with urllib.request.urlopen(url.removesuffix('index.html') + 'data.bin') as response:
            self.assertEqual(b'\x00\xff\x00\r\n', response.read())
        self.assertEqual(url, artifact_panel.open_url(self.repo, 'origin', 'explorer'))
        for method in ('GET', 'HEAD'):
            request = urllib.request.Request(url, method=method, headers={'Host': 'untrusted.example'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(404, error.exception.code)

    def test_local_preview_directory_redirects_keep_the_token_and_query(self):
        meta = json.loads((self.source / 'artifact.json').read_text())
        meta['files'].append('docs/index.html')
        (self.source / 'artifact.json').write_text(json.dumps(meta))
        (self.source / 'docs').mkdir()
        (self.source / 'docs/index.html').write_text('<h1>Nested artifact</h1>')
        self.save()
        base = artifact_panel.open_url(self.repo, 'origin', 'explorer').removesuffix('index.html')
        self.clean_up_preview()
        for method in ('GET', 'HEAD'):
            with self.subTest(method=method):
                request = urllib.request.Request(base + 'docs?mode=compact', method=method)
                with urllib.request.urlopen(request) as response:
                    self.assertEqual((200, base + 'docs/?mode=compact'), (response.status, response.url))

    def test_console_opens_and_dismisses_an_artifact_without_changing_its_saved_versions(self):
        state = artifact_panel.watch.state_root()
        app = console.Console(state)
        app.sessions['one'] = console.Session('one', 'codex', 'One', artifacts=self.listed())
        previous = list(app.sessions['one'].artifacts)
        app.open_panel('one')
        app.key('enter')
        self.assertIn('Artifacts', '\n'.join(line.text for line in app.panel_lines(100)[0]))
        with mock.patch.object(artifact_panel, 'open_url', return_value='https://example.com/artifacts/explorer/'):
            with mock.patch.object(app, 'open_url') as opened:
                app.key('o')
                opened.assert_called_once_with('https://example.com/artifacts/explorer/')
        tip = refs.ref_tip(self.repo, artifact.REF)
        app.key('8')
        self.assertEqual([], self.listed())
        self.assertEqual([], app.sessions['one'].artifacts)
        self.assertEqual(tip, refs.ref_tip(self.repo, artifact.REF))
        app.apply(console.Found(artifacts={'one': previous}))
        self.assertEqual([], app.sessions['one'].artifacts)

    def test_console_keeps_the_artifact_url_visible_when_no_browser_opens(self):
        url = 'https://example.com/artifacts/explorer/'
        for remote in ('0', '1'):
            with self.subTest(remote=remote), mock.patch.dict(os.environ, {'PROJECTOR_REMOTE': remote, 'BROWSER': ''}):
                app = console.Console(artifact_panel.watch.state_root(), open_url=lambda url: False)
                app.sessions['one'] = console.Session('one', 'codex', 'One', artifacts=self.listed())
                app.open_panel('one')
                app.key('enter')
                with mock.patch.object(artifact_panel, 'open_url', return_value=url):
                    app.key('o')
                lines = [line.text for line in app.panel_lines(100)[0]]
                self.assertIn(url, lines)
                self.assertEqual(remote == '1', any('Sent to your clipboard' in line for line in lines))
                self.assertEqual(url if remote == '1' else None, app.take_clipboard())

    def test_console_discusses_the_saved_version_with_its_description(self):
        state = artifact_panel.watch.state_root()
        app = console.Console(state)
        manifest = self.source / 'artifact.json'
        meta = json.loads(manifest.read_text())
        meta.update(title='Cache\nexplorer', description='Explore\nsaved data')
        manifest.write_text(json.dumps(meta))
        self.save()
        (self.source / 'artifact.json').unlink()
        listed = self.listed()
        version = listed[0]['version']
        app.sessions['one'] = console.Session('one', 'claude', 'One', artifacts=listed)
        app.open_panel('one')
        self.assertIn('Explore saved data', [line.text.strip() for line in app.panel_lines(100)[0]])
        app.key('enter')
        app.key('7')
        listed[0]['version'] = 'f' * 64
        for letter in 'Make the controls clearer':
            app.key(letter)
        app.key('enter')
        self.assertEqual([
            f'Artifact explorer@{version}: Cache explorer; Description: Explore saved data; '
            f'Checkout: {self.repo.resolve()}; Remote: origin. Make the controls clearer',
        ], [item['messages'][0]['text'] for item in inbox.load_items(inbox.session_dir('one'))])

    def test_an_idle_preview_stops_and_removes_its_extracted_files(self):
        work = self.root / 'preview'
        artifact.write_source(artifact.snapshot(self.source), work / 'files')
        result = subprocess.run([sys.executable, '-c',
                                 'from pathlib import Path; import sys; '
                                 'from projector.artifact_panel import serve; '
                                 'serve(Path(sys.argv[1]), "test-token", idle=0.01)', str(work)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(work.exists())

    def test_a_fetched_published_artifact_opens_its_configured_site_without_a_preview_process(self):
        tip = refs.ref_tip(self.repo, artifact.REF)
        self.git('update-ref', refs.tracking_ref('origin', artifact.REF), tip)
        self.git('update-ref', '-d', artifact.REF)
        self.assertFalse(self.listed()[0]['preview'])
        with mock.patch.object(artifact, 'repository', return_value='example/repo'):
            with mock.patch.object(artifact_panel.summary, 'push_decision', return_value=('https://example.com/tools/', '')):
                self.assertEqual('https://example.com/tools/artifacts/explorer/',
                                 artifact_panel.open_url(self.repo, 'origin', 'explorer'))
        self.assertFalse((artifact_panel.watch.state_root() / 'artifact-previews').exists())

    def test_cli_dismiss_and_restore_change_only_the_selected_session(self):
        artifact_panel.attach(self.repo, 'origin', 'two', 'explorer')
        arguments = ['--checkout', str(self.repo), '--session', 'one']
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(['artifact', 'dismiss', 'explorer', *arguments]))
            self.assertEqual([], self.listed())
            self.assertEqual(1, len(self.listed('two')))
            self.assertEqual(0, cli.main(['artifact', 'restore', 'explorer', *arguments]))
        self.assertEqual(1, len(self.listed()))

    def test_a_stopped_managed_session_lists_opens_and_discusses_its_artifacts(self):
        from projector import agent_console
        state = artifact_panel.watch.state_root()
        app = agent_console.Console(state)
        managed = {'one': {'cwd': str(self.repo), 'backend': 'codex', 'state': 'stopped'}}
        with mock.patch.object(console, 'live_sessions', return_value={}):
            with mock.patch.object(agent_console.runtime, 'sessions', return_value=managed):
                with mock.patch.object(agent_console.runtime, 'launches', return_value=[]):
                    found = app.load(console.Work(inbox=True, rows=True, signatures={}))
        app.apply(found)
        self.assertEqual('explorer', app.sessions['one'].artifacts[0]['id'])
        app.open_panel('one')
        app.key('enter')
        with mock.patch.object(artifact_panel, 'open_url', return_value='https://example.com/artifacts/explorer/'):
            with mock.patch.object(app, 'open_url') as opened:
                with mock.patch.object(agent_console.runtime, 'control') as control:
                    app.key('o')
                    opened.assert_called_once_with('https://example.com/artifacts/explorer/')
                    control.assert_not_called()
        version = app.sessions['one'].artifacts[0]['version']
        app.key('7')
        for letter in 'Explain this view':
            app.key(letter)
        app.key('enter')
        self.assertEqual([
            f'Artifact explorer@{version}: Cache explorer; Description: Explore saved data; '
            f'Checkout: {self.repo.resolve()}; Remote: origin. Explain this view',
        ], [item['messages'][0]['text'] for item in inbox.load_items(inbox.session_dir('one'))])

    def test_an_artifact_keeps_its_session_visible_without_leaking_to_a_neighbor(self):
        app = console.Console(artifact_panel.watch.state_root())
        with mock.patch.object(console, 'live_sessions', return_value={'one': 'codex', 'two': 'codex'}):
            with mock.patch.object(console.agents, 'record', return_value={}):
                with mock.patch.object(app.titles, 'read', return_value={}):
                    found = app.load(console.Work(inbox=True, rows=True, signatures={}))
        app.apply(found)
        self.assertEqual(['one'], list(app.sessions))
        self.assertEqual(['explorer'], [row['id'] for row in app.sessions['one'].artifacts])
