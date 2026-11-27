import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from projector import agent_console, console, inbox, runtime, watch
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
            write_json(folder / 'agent.json', {'schema_version': 1, 'host': 'codex',
                       'cwd': self.temporary.name, 'updated_at': watch.stamp(self.now)})
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
        self.assertEqual('stopped', self.console.runtimes['managed-session']['state'])
        self.assertIsInstance(self.console.sessions['managed-session'].updated_at, (int, float))
        self.console.open_panel('managed-session')
        self.assertIn('r return/resume', '\n'.join(line.text for line in self.console.lines(100)[0]))

    def test_a_new_prompt_uses_the_highlighted_sessions_directory(self):
        first = Path(self.temporary.name) / 'first'
        second = Path(self.temporary.name) / 'second'
        first.mkdir()
        second.mkdir()
        self.console.sessions = {
            'first': console.Session('first', 'claude', 'First', str(first)),
            'second': console.Session('second', 'other', 'Second', str(second)),
        }
        self.console.key('down')
        self.console.key('n')
        self.type('Start here')
        self.assertEqual('launch-prompt', self.console.field.purpose)
        self.assertEqual('Start here', self.console.field.text)
        self.assertEqual(str(second), self.console.launch_form['cwd'])
        self.assertEqual('claude', self.console.launch_form['backend'])
        self.console.sessions['second'].cwd = str(first)
        self.assertEqual(str(second), self.console.launch_form['cwd'])

    def test_the_prompt_can_launch_without_answering_any_setup_questions(self):
        target = runtime.environment(self.root)
        target.mkdir(parents=True)
        write_json(target / 'ready.json', {})
        executable = Path(self.temporary.name) / 'bin' / 'codex'
        executable.parent.mkdir()
        executable.write_text('#!/bin/sh\nprintf "codex-cli 0.172.0\\n"\n')
        executable.chmod(0o755)
        self.managed()
        with patch.dict(os.environ, {'PATH': str(executable.parent)}):
            self.console.key('n')
            prompt='--help "hi" $HOME\n\ncdmnoxs are prompt text'
            self.type(prompt)
            self.console.key('\x04')
        self.assertEqual('', self.console.status)
        launch = runtime.launches(self.root)[0]
        self.assertEqual(('codex', '', prompt, 'queued'),
                         tuple(launch[key] for key in ('backend', 'name', 'prompt', 'state')))
        self.assertTrue(Path(launch['cwd']).samefile(self.temporary.name))
        self.assertNotIn('pid', launch)

    def test_settings_preserve_the_draft_and_offer_single_key_provider_and_model_choices(self):
        self.managed()
        folder = watch.session_folder(self.root, 'managed-session')
        write_json(folder / 'agent.json', {'schema_version': 1, 'host': 'codex', 'model': 'example-model'})
        self.console.key('n')
        self.type('first\nsecond')
        self.console.key('left')
        cursor = self.console.field.cursor
        for key in ('up', 'c', 's'):
            self.console.key(key)
        self.assertEqual('launch-prompt', self.console.field.purpose)
        self.assertEqual(('claude', 'sonnet'),
                         tuple(self.console.launch_form[key] for key in ('backend', 'model')))
        for key in ('up', 'x'):
            self.console.key(key)
        self.assertEqual('', self.console.launch_form['model'])
        self.assertIn('example-model', '\n'.join(line.text for line in self.console.lines(100)[0]))
        self.console.key('1')
        for key in ('up', 'd'):
            self.console.key(key)
        destination = Path(self.temporary.name) / 'working directory '
        destination.mkdir()
        self.replace_field(str(destination))
        self.console.key('enter')
        for key in ('up', 'n'):
            self.console.key(key)
        self.type('parser')
        self.console.key('enter')
        self.assertEqual(('first\nsecond', cursor), (self.console.field.text, self.console.field.cursor))
        with patch.object(runtime, 'launch') as launch:
            self.console.key('ctrl-d')
        launch.assert_called_once_with(self.root, 'codex', str(destination.resolve()), 'first\nsecond',
                                       name='parser', model='example-model',
                                       parent=os.environ.get('PROJECTOR_SESSION', ''))

    def test_cancelled_settings_and_failed_launches_keep_the_prompt_editable(self):
        self.managed()
        self.console.key('n')
        self.type('Keep this draft')
        for key in ('up', 'd'):
            self.console.key(key)
        self.replace_field('missing-directory')
        self.console.key('enter')
        self.assertIn('existing working directory', self.console.status)
        self.assertEqual('launch-cwd', self.console.field.purpose)
        self.console.key('esc')
        self.assertEqual('Keep this draft', self.console.field.text)
        self.assertEqual(self.temporary.name, self.console.launch_form['cwd'])
        for key in ('up', 'm', 't'):
            self.console.key(key)
        self.type('custom-model')
        self.console.key('enter')
        with patch.object(runtime, 'launch', side_effect=runtime.UsageError('Try again')):
            self.console.key('ctrl-d')
        self.assertEqual('Try again', self.console.status)
        self.assertEqual('Keep this draft', self.console.field.text)
        self.assertEqual('custom-model', self.console.launch_form['model'])
        for key in ('up', 'm', '0'):
            self.console.key(key)
        self.assertEqual('', self.console.launch_form['model'])
        self.console.key('esc')
        self.assertIsNone(self.console.field)
        self.assertEqual([], runtime.launches(self.root))
        self.console.key('n')
        self.assertEqual('', self.console.field.text)

    def test_an_empty_session_list_still_opens_a_prompt_in_the_current_directory(self):
        self.console.key('n')
        self.assertEqual('launch-prompt', self.console.field.purpose)
        self.assertEqual(str(Path.cwd()), self.console.launch_form['cwd'])

    def test_a_long_multiline_prompt_keeps_its_cursor_visible_in_a_small_terminal(self):
        self.managed()
        self.console.key('n')
        self.type('first line\n' + 'a long prompt ' * 20 + '\nlast line')
        self.assertTrue(self.console.field.inline)
        for width, room in ((40, 8), (20, 4), (1, 1)):
            shown = self.console.viewport(width, room)
            self.assertLessEqual(len(shown), room)
            cursors = [line for line in shown if line.cursor is not None]
            self.assertEqual(1, len(cursors))
            self.assertLess(cursors[0].cursor, width)
            self.assertTrue(all(len(line.text) <= width for line in shown))
        self.console.key('home')
        shown = self.console.viewport(40, 8)
        self.assertTrue(any('Prompt: first line' in line.text and line.cursor is not None for line in shown))

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

    def test_a_typed_answer_returns_the_actual_text_without_colliding_with_five_options(self):
        self.managed()
        item = inbox.ask('managed-session', 'Where should tests run?', 'Local', ['CI', 'Both', 'Later', 'Never'])
        self.now += 2
        self.console.tick()
        self.console.open_panel('managed-session')
        self.console.open_drawer('item:' + item['ref'])
        controls = self.console.item_controls(item)
        self.assertEqual(len(controls), len({control.key for control in controls}))
        self.console.key('t')
        self.type('An isolated sandbox')
        self.console.key('enter')
        self.assertEqual('An isolated sandbox', inbox.find('managed-session', item['ref'])['answer']['text'])

    def test_redrawing_a_managed_conversation_reads_no_execution_database(self):
        self.managed()
        self.console.open_panel('managed-session')
        self.console.tick()
        with patch.object(Store, 'connection', side_effect=AssertionError('Database read during redraw')):
            self.console.lines(100)
            self.console.viewport(100, 30)
