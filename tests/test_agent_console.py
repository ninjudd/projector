import io
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
            self.console.key('ctrl-j' if char == '\n' else char)

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
        self.console.key('up')
        self.assertIn('r Resume', self.console.footer()[0].text)

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
            self.console.key('enter')
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
            self.console.key('enter')
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
            self.console.key('enter')
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

    def test_new_session_hint_stays_at_the_bottom_and_changes_with_the_active_view(self):
        from tests.test_console import FakeCurses, FakeWindow
        self.assertIn('n new agent session', self.console.footer(100)[0].text)
        self.assertNotIn('↑↓', self.console.footer(100)[0].text)
        self.managed()
        window = FakeWindow(20, 100)
        screen = agent_console.Screen(window, self.console, FakeCurses(), io.StringIO())
        screen.draw()
        self.assertEqual([19], [row for row, text in window.rows.items() if 'n new agent session' in text])
        self.assertIn('↑↓', self.console.footer(100)[0].text)
        self.console.open_panel('managed-session')
        self.assertNotIn('new agent session', self.console.footer(100)[0].text)
        self.assertIn('Enter send', self.console.footer(100)[0].text)
        self.console.go_home()
        self.console.key('n')
        self.assertNotIn('new agent session', self.console.footer(100)[0].text)
        self.console.key('up')
        self.assertIn('d directory', self.console.footer(100)[0].text)

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
        self.type('first\nsecond')
        self.console.key('enter')
        items = inbox.load_items(inbox.session_dir('managed-session'))
        self.assertEqual(['first\nsecond'], [item['messages'][0]['text'] for item in items])

    def test_completion_changes_the_live_prompt_without_adding_a_status_message_or_losing_the_draft(self):
        folder = self.managed(state='running')
        self.console.open_panel('managed-session')
        self.type('A follow-up')
        active = self.console.field.prompt
        store = Store(folder)
        store.event('text', {'text': 'All done.'})
        store.event('turn_end', {'outcome': 'completed'})
        current = runtime.record(folder)
        write_json(folder / 'runtime.json', {**current, 'state': 'ready'})
        self.now += 2
        self.console.tick()
        self.assertNotEqual(active, self.console.field.prompt)
        self.assertEqual('A follow-up', self.console.field.text)
        shown = '\n'.join(line.text for line in self.console.lines(100)[0])
        self.assertIn('All done.', shown)
        self.assertNotIn('completed', shown)
        self.assertNotIn('agent:', shown)

    def test_user_messages_have_a_background_and_agent_replies_have_no_speaker_labels(self):
        from tests.test_console import ColorCurses, FakeWindow
        folder = self.managed()
        self.console.open_panel('managed-session')
        store = Store(folder)
        store.event('user', {'text': 'Question\n\nSecond paragraph'})
        store.event('text', {'text': 'An answer.'})
        self.now += 0.3
        self.console.tick()
        lines = self.console.runtime_lines('managed-session', 40, True)
        curses = ColorCurses(256)
        with patch.dict(os.environ, {'NO_COLOR': ''}):
            window = FakeWindow(len(lines), 41)
            screen = agent_console.Screen(window, self.console, curses)
        for y, line in enumerate(lines):
            screen.put(y, line, 41)
        user = [(text, style) for text, style in window.drawn if 'Question' in text or 'Second paragraph' in text]
        reply = next((text, style) for text, style in window.drawn if 'An answer.' in text)
        self.assertEqual(2, len(user))
        for text, attributes in user:
            self.assertNotIn('you:', text)
            self.assertEqual(40, len(text))
            self.assertGreaterEqual(curses.backgrounds[attributes >> 8], 0)
        self.assertNotIn('agent:', reply[0])
        self.assertEqual(0, reply[1])

    def test_long_chat_input_keeps_the_cursor_visible_when_editing_earlier_lines(self):
        self.managed()
        self.console.open_panel('managed-session')
        self.type('first line\n' + 'long input ' * 100 + '\nlast line')
        self.console.key('home')
        shown = self.console.viewport(40, 10)
        self.assertTrue(any(line.cursor is not None and 'first line' in line.text for line in shown))
        self.assertTrue(all(line.cursor is None or line.cursor < 40 for line in shown))
        self.assertTrue(all(len(line.text) <= 40 for line in shown if line.cursor is not None))

    def test_completed_turns_keep_separate_replies_without_a_completed_status_line(self):
        folder = self.managed()
        self.console.open_panel('managed-session')
        store = Store(folder)
        for text in ('First reply.', 'Second reply.'):
            store.event('text', {'text': text})
            store.event('turn_end', {'outcome': 'completed'})
        self.now += 0.3
        self.console.tick()
        texts = [line.text for line in self.console.runtime_lines('managed-session', 80, True)]
        self.assertIn('First reply.', texts)
        self.assertIn('Second reply.', texts)
        self.assertFalse(any('completed' in text for text in texts))

    def test_native_handoff_restores_keyboard_modes_even_when_the_native_cli_fails(self):
        from tests.test_console import FakeCurses, FakeWindow
        self.managed(state='handed-off')
        for fails in (False, True):
            with self.subTest(fails=fails):
                output = io.StringIO()
                screen = agent_console.Screen(FakeWindow(30), self.console, FakeCurses, output)
                screen.input_mode(True)
                self.console.pending_native = 'managed-session'
                def native(command):
                    self.assertTrue(output.getvalue().endswith('\x1b[?2004l\x1b[<u'))
                    if fails:
                        raise OSError('Native CLI failed')
                    return 0
                with patch.dict(os.environ, {'TMUX': ''}), patch.object(agent_console.native_handoff, 'wait', native), \
                        patch.object(runtime, 'control'), patch.multiple(FakeCurses, create=True,
                        def_prog_mode=lambda: None, endwin=lambda: None, reset_prog_mode=lambda: None):
                    screen.draw()
                self.assertTrue(output.getvalue().endswith('\x1b[>1u\x1b[?2004h'))

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

    def test_checking_a_message_uses_its_identity_and_resending_needs_confirmation(self):
        folder = self.managed()
        store = Store(folder)
        for ident in ('first', 'second'):
            store.enqueue(ident, ident + ' message', [])
            store.begin(ident, 'old-worker')
        store.recover()
        self.console.open_panel('managed-session')
        self.now += 0.3
        self.console.tick()
        for key in ('up', 'k', 'down', 'down', 'down', 'enter'):
            self.console.key(key)
        shown = '\n'.join(line.text for line in self.console.lines(100)[0])
        self.assertIn('second message', shown)
        self.assertIn('Already reached the agent', shown)
        with patch.object(runtime, 'control') as control:
            for key in ('s', 'enter'):
                self.console.key(key)
            control.assert_not_called()
            for key in ('s', 's'):
                self.console.key(key)
        control.assert_called_once_with(self.root, 'managed-session', 'retry:second')
        self.assertEqual('chat', self.console.field.purpose)

    def test_a_message_checked_by_another_console_cannot_be_resubmitted_from_stale_buttons(self):
        folder = self.managed()
        store = Store(folder)
        store.enqueue('first', 'Check this message', [])
        store.begin('first', 'old-worker')
        store.recover()
        self.console.open_panel('managed-session')
        self.now += 0.3
        self.console.tick()
        for key in ('up', 'k', 's'):
            self.console.key(key)
        store.resolve('first', retry=False)
        with patch.object(runtime, 'control') as control:
            self.console.key('s')
        control.assert_not_called()
        self.assertIn('no longer needs checking', self.console.status)

    def test_chat_is_ready_by_default_and_sending_keeps_the_composer_open(self):
        self.managed()
        self.console.open_panel('managed-session')
        self.assertIsNotNone(self.console.field)
        self.assertEqual('chat', self.console.field.purpose)
        for text in ('first message', 'c is ordinary text'):
            self.type(text)
            self.console.key('enter')
            self.assertEqual('', self.console.field.text)
        items = inbox.load_items(inbox.session_dir('managed-session'))
        self.assertEqual(['first message', 'c is ordinary text'], [item['messages'][0]['text'] for item in items])
        shown = '\n'.join(line.text for line in self.console.lines(100)[0])
        for label in ('Conversation', 'interrupt', 'retry uncertain', 'Approvals:', 'Input number:'):
            self.assertNotIn(label, shown)

    def test_opening_chat_and_launch_options_keeps_every_body_line_in_place(self):
        self.managed()
        for launch in (False, True):
            with self.subTest(launch=launch):
                if launch:
                    self.console.go_home()
                    self.console.key('n')
                else:
                    self.console.open_panel('managed-session')
                self.type('Keep this prompt where it is')
                before = [line.text for line in self.console.viewport(80, 12)]
                self.console.key('up')
                self.assertEqual(before, [line.text for line in self.console.viewport(80, 12)])
                self.assertEqual(1, len(self.console.footer(80)))
                self.assertIn('Native' if not launch else 'directory', self.console.footer(80)[0].text)
                self.console.key('down')
                self.assertEqual('Keep this prompt where it is', self.console.field.text)

    def test_history_scrolls_while_the_draft_stays_in_place(self):
        self.managed()
        self.console.chats['managed-session'] = [('agent', f'Output line {at}') for at in range(50)]
        self.console.open_panel('managed-session')
        self.type('My draft')
        before = self.console.viewport(60, 15)
        self.console.key('pageup')
        after = self.console.viewport(60, 15)
        self.assertNotEqual([line.text for line in before[:-1]], [line.text for line in after[:-1]])
        self.assertEqual(before[-1].text, after[-1].text)
        self.assertEqual(self.console.field.text, 'My draft')

    def test_clicking_an_unavailable_inline_action_keeps_the_draft_and_shows_the_error(self):
        from tests.test_console import FakeWindow, MouseCurses
        self.managed()
        self.console.open_panel('managed-session')
        self.type('Keep my draft')
        self.console.key('up')
        curses = MouseCurses()
        window = FakeWindow(15, 100)
        screen = agent_console.Screen(window, self.console, curses, io.StringIO())
        screen.draw()
        y, text = next((y, text) for y, text in window.rows.items() if 'o Native' in text)
        with patch.object(runtime, 'control', side_effect=runtime.UsageError('Wait for the turn to finish')):
            curses.events.append((0, text.index('o Native'), y, 0, curses.BUTTON1_RELEASED))
            window.pressed.append(curses.KEY_MOUSE)
            screen.read_key()
        self.assertEqual('Wait for the turn to finish', self.console.status)
        self.assertEqual('Keep my draft', self.console.drafts['managed-session'].text)

    def test_editing_a_waiting_message_preserves_the_unsent_chat_draft(self):
        folder = self.managed()
        store = Store(folder)
        store.enqueue('waiting', 'old message', [])
        self.console.open_panel('managed-session')
        self.now += 0.3
        self.console.tick()
        self.type('Unsent draft')
        for key in ('up', 'q', 'enter'):
            self.console.key(key)
        self.replace_field('Updated message')
        self.console.key('enter')
        self.assertEqual('Updated message', store.command('waiting')['text'])
        self.assertEqual('Unsent draft', self.console.field.text)

    def test_canceling_a_resend_keeps_the_card_and_escape_restores_the_draft(self):
        folder = self.managed()
        store = Store(folder)
        store.enqueue('held', 'Check this message', [])
        store.begin('held', 'old-worker')
        store.recover()
        self.console.open_panel('managed-session')
        self.now += 0.3
        self.console.tick()
        self.type('My draft')
        for key in ('up', 'k', 's', '0'):
            self.console.key(key)
        self.assertEqual(agent_console.MESSAGE + 'held', self.console.drawer)
        self.assertIsNone(self.console.recovery_retry)
        self.console.key('esc')
        self.assertEqual(('chat', 'My draft'), (self.console.field.purpose, self.console.field.text))
        self.assertEqual('uncertain', store.command('held')['state'])

    def test_canceling_a_waiting_message_edit_returns_to_the_chat_draft(self):
        folder = self.managed()
        store = Store(folder)
        store.enqueue('waiting', 'Original message', [])
        self.console.open_panel('managed-session')
        self.now += 0.3
        self.console.tick()
        self.type('My draft')
        for key in ('up', 'q', 'enter'):
            self.console.key(key)
        self.replace_field('Do not save this')
        self.console.key('esc')
        self.assertIsNotNone(self.console.field)
        self.assertEqual(('chat', 'My draft'), (self.console.field.purpose, self.console.field.text))
        self.assertEqual('Original message', store.command('waiting')['text'])

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
