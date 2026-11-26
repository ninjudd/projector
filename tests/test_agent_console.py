import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from projector import agent_console, inbox, runtime, watch
from projector.inbox import write_json
from projector.runtime_store import Store


class AgentConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'XDG_STATE_HOME': self.temporary.name}).start()
        self.root = watch.state_root()
        self.now = time.time()
        self.console = agent_console.Console(self.root, clock=lambda: self.now)

    def type(self, text):
        for char in text:
            self.console.key('enter' if char == '\n' else char)

    def replace_field(self, text):
        self.console.key('home')
        for _ in self.console.field.text:
            self.console.key('delete')
        self.type(text)

    def managed(self, name='managed-session', state='ready'):
        folder = watch.session_folder(self.root, name)
        with inbox.locked(folder):
            write_json(folder / 'runtime.json', {'schema_version': 1, 'session': name, 'backend': 'codex',
                'owner': 'managed', 'state': state, 'cwd': self.temporary.name, 'renewed_at': time.time()})
        Store(folder)
        self.console.tick()
        return folder

    def test_a_registered_generic_process_keeps_its_inbox_without_managed_controls(self):
        folder = watch.session_folder(self.root, 'generic-session')
        watch.renew(self.root, 'generic-session', 'stream', time.time())
        write_json(folder / 'agent.json', {'schema_version': 1, 'name': 'build-runner',
                   'host': 'other', 'cwd': self.temporary.name, 'summary': 'Build process'})
        inbox.ask('generic-session', 'Use the cached result?', 'Yes', ['No'])
        self.console.tick()
        self.assertIn('generic-session', self.console.sessions)
        self.console.open_panel('generic-session')
        text='\n'.join(line.text for line in self.console.lines(100)[0])
        self.assertIn('Use the cached result?', text)
        self.assertNotIn('i interrupt', text)
        self.console.key('x')
        self.assertEqual({}, runtime.record(folder))

    def test_stopped_managed_sessions_remain_available_to_resume(self):
        self.managed(state='stopped')
        self.assertEqual('stopped', self.console.sessions['managed-session'].summary)
        self.console.open_panel('managed-session')
        self.assertIn('r return/resume', '\n'.join(line.text for line in self.console.lines(100)[0]))

    def test_the_launch_form_preserves_multiline_prompts_and_does_not_spawn_a_shell(self):
        target = runtime.environment(self.root)
        target.mkdir(parents=True)
        write_json(target / 'ready.json', {})
        executable = Path(self.temporary.name) / 'bin' / 'codex'
        executable.parent.mkdir()
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o755)
        with patch.dict(os.environ, {'PATH': str(executable.parent)}):
            self.console.key('n')
            self.console.key('tab')
            self.console.key('enter')
            self.replace_field(self.temporary.name)
            self.console.key('enter')
            self.type('parser')
            self.console.key('enter')
            self.console.key('enter')
            prompt='--help "hi" $HOME\n\nsecond paragraph'
            self.type(prompt)
            self.console.key('\x04')
        self.assertEqual('', self.console.status)
        launch = runtime.launches(self.root)[0]
        self.assertEqual(('codex', 'parser', prompt, 'queued'),
                         tuple(launch[key] for key in ('backend', 'name', 'prompt', 'state')))
        self.assertTrue(Path(launch['cwd']).samefile(self.temporary.name))
        self.assertNotIn('pid', launch)

    def test_chat_records_input_in_the_same_inbox_as_other_person_to_session_messages(self):
        self.managed()
        self.console.open_panel('managed-session')
        self.console.key('c')
        self.type('first\nsecond')
        self.console.key('\x04')
        items = inbox.load_items(inbox.session_dir('managed-session'))
        self.assertEqual(['first\nsecond'], [item['messages'][0]['text'] for item in items])

    def test_stream_refresh_appends_only_new_text_and_keeps_a_terminal_failure_visible(self):
        folder = self.managed()
        self.console.open_panel('managed-session')
        store = Store(folder)
        store.event('text', {'text': 'first '})
        self.console.tick()
        store.event('text', {'text': 'second'})
        store.event('turn_end', {'outcome': 'failed', 'error': 'disk full'})
        self.now += 0.3
        self.console.tick()
        self.console.tick()
        chat = self.console.chats['managed-session']
        self.assertEqual([('agent', 'first second'), ('status', 'failed: disk full')], chat)
        self.assertIn('failed: disk full', '\n'.join(line.text for line in self.console.lines(100)[0]))

    def test_retrying_uncertain_input_requires_an_explicit_confirmation(self):
        folder = self.managed()
        self.console.open_panel('managed-session')
        store = Store(folder)
        store.enqueue('uncertain', 'may have changed files', [])
        store.begin('uncertain', 'old-worker')
        store.recover()
        self.console.key('R')
        self.type('1')
        self.console.key('enter')
        self.type('yes')
        self.console.key('enter')
        self.assertEqual('uncertain', store.command('uncertain')['state'])
        self.replace_field('RETRY')
        self.console.key('enter')
        self.assertEqual('queued', store.command('uncertain')['state'])
