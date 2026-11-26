import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projector import inbox, runtime, watch
from projector.core import EnvironmentError, UsageError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'XDG_STATE_HOME': self.temporary.name}).start()
        self.root = watch.state_root()
        self.folder = Path(self.temporary.name)
        self.bin = self.folder / 'native'
        self.bin.mkdir()
        self.executable = self.bin / 'codex'
        self.executable.write_text('#!/bin/sh\nprintf "codex-cli 0.172.0\\n"\n')
        self.executable.chmod(0o755)
        patch.dict(os.environ, {'PATH': str(self.bin)}).start()

    def test_upgrading_preserves_the_source_and_environment_of_a_stopped_session(self):
        source = self.folder / 'package'
        source.mkdir()
        module = source / 'runtime.py'
        module.write_text('snapshot = "original"\n')
        def create(path):
            (path / 'bin').mkdir()
            (path / 'bin' / 'python').write_text('fixture interpreter')
        with patch.object(runtime, '__file__', str(module)), patch.object(runtime.venv, 'EnvBuilder') as builder:
            builder.return_value.create.side_effect = create
            run = lambda *args, **kwargs: SimpleNamespace(returncode=0)
            original = runtime.setup(self.root, run=run)
            ident = runtime.launch(self.root, 'codex', str(self.folder), 'work')
            module.write_text('snapshot = "upgraded"\n')
            upgraded = runtime.setup(self.root, run=run)
        self.assertNotEqual(original, upgraded)
        self.assertEqual('snapshot = "original"\n', (original / 'app/projector/runtime.py').read_text())
        self.assertEqual('snapshot = "upgraded"\n', (upgraded / 'app/projector/runtime.py').read_text())
        session = watch.session_folder(self.root, 'stopped-session')
        with inbox.locked(session):
            inbox.write_json(session / 'runtime.json', {'session': 'stopped-session', 'launch': ident,
                                                       'owner': 'managed', 'state': 'stopped'})
        runtime.control(self.root, 'stopped-session', 'resume')
        launch = runtime.launches(self.root)[0]
        self.assertEqual((str(original), 'stopped-session', 'queued'),
                         (launch['environment'], launch['session'], launch['state']))

    def test_an_old_native_cli_fails_before_creating_a_launch_record(self):
        self.executable.write_text('#!/bin/sh\nprintf "codex-cli 0.1.0\\n"\n')
        with self.assertRaisesRegex(EnvironmentError, 'update the native CLI'):
            runtime.launch(self.root, 'codex', str(self.folder), 'work')
        self.assertEqual([], runtime.launches(self.root))

    def test_invalid_launch_settings_create_no_orphan_execution_record(self):
        target = runtime.environment(self.root)
        target.mkdir(parents=True)
        inbox.write_json(target / 'ready.json', {})
        for prompt, settings in [('work', {'name': 'two words'}), ('work', {'model': 'a model'}),
                                 ('', {}), ('contains\0NUL', {})]:
            with self.assertRaises(UsageError):
                runtime.launch(self.root, 'codex', str(self.folder), prompt, **settings)
        self.assertEqual([], runtime.launches(self.root))

    def test_a_starting_launch_cannot_be_retried_or_removed_while_its_worker_owns_it(self):
        target = runtime.environment(self.root)
        target.mkdir(parents=True)
        inbox.write_json(target / 'ready.json', {})
        ident = runtime.launch(self.root, 'codex', str(self.folder), 'work')
        launch = self.root / 'launches' / ident
        with runtime.worker_lock(launch):
            for retry in (True, False):
                with self.assertRaisesRegex(UsageError, 'still starting'):
                    runtime.change_launch(self.root, ident, retry=retry)
        runtime.change_launch(self.root, ident, retry=False)
        self.assertEqual('cancelled', runtime.launches(self.root)[0]['state'])

    def test_returning_from_native_control_waits_for_the_inherited_terminal_lease(self):
        session = watch.session_folder(self.root, 'native-session')
        with inbox.locked(session):
            inbox.write_json(session / 'runtime.json', {'session': 'native-session', 'owner': 'native',
                                                       'state': 'handed-off'})
        with runtime.worker_lock(session, 'runtime-native.lock'):
            with self.assertRaisesRegex(UsageError, 'Close the native terminal'):
                runtime.control(self.root, 'native-session', 'resume')
        self.assertEqual('native', runtime.record(session)['owner'])
