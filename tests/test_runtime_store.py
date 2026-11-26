import tempfile
import unittest
from pathlib import Path

from projector.core import EnvironmentError
from projector.runtime_store import Store


class RuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.store = Store(self.folder)

    def test_replayed_delivery_keeps_one_command_and_its_original_order(self):
        self.store.enqueue('a', 'first', ['message', 'm-one'])
        self.store.enqueue('b', 'second', ['message', 'm-two'])
        self.store.enqueue('a', 'a duplicate copy', ['message', 'm-one'])
        self.assertEqual([('a', 'first'), ('b', 'second')],
                         [(row['id'], row['text']) for row in Store(self.folder).commands()])

    def test_recovery_never_requeues_an_input_that_might_have_been_accepted(self):
        for ident in ('queued', 'written', 'accepted', 'finished'):
            self.store.enqueue(ident, ident, [])
        for ident in ('written', 'accepted', 'finished'):
            self.store.begin(ident, 'generation')
        self.store.accepted('accepted', 'generation', 'native-turn')
        self.store.finish('finished', 'generation', 'completed')
        Store(self.folder).recover()
        self.assertEqual(['queued', 'uncertain', 'uncertain', 'finished'],
                         [row['state'] for row in self.store.commands()])
        self.assertEqual('native-turn', self.store.command('accepted')['turn'])

    def test_stale_worker_callbacks_cannot_finish_a_new_generation(self):
        self.store.enqueue('one', 'work', [])
        self.store.begin('one', 'old')
        self.store.recover()
        self.store.resolve('one', retry=True)
        self.store.begin('one', 'new')
        self.assertFalse(self.store.accepted('one', 'old', 'wrong-turn'))
        self.assertFalse(self.store.finish('one', 'old', 'completed'))
        self.assertEqual(('submitting', 'new', None),
                         tuple(self.store.command('one')[key] for key in ('state', 'generation', 'turn')))

    def test_a_duplicate_turn_end_records_one_terminal_result(self):
        self.store.enqueue('one', 'work', [])
        self.store.begin('one', 'generation')
        self.store.accepted('one', 'generation', 'native-turn')
        self.assertTrue(self.store.finish('one', 'generation', 'failed', 'disk full'))
        self.assertFalse(self.store.finish('one', 'generation', 'completed'))
        outcomes = [row['payload'] for row in self.store.events() if row['kind'] == 'turn_end']
        self.assertEqual([{'outcome': 'failed', 'error': 'disk full'}], outcomes)

    def test_only_unsent_input_can_be_edited_or_removed(self):
        self.store.enqueue('one', 'original', [])
        self.store.edit('one', 'edited\nmultiline')
        self.assertEqual('edited\nmultiline', self.store.begin('one', 'generation')['text'])
        with self.assertRaises(EnvironmentError):
            self.store.edit('one', None)
        self.store.enqueue('two', 'remove me', [])
        self.store.edit('two', None)
        self.assertEqual([], self.store.commands(('queued',)))

    def test_reconnect_reads_only_new_events_including_unknown_kinds(self):
        self.store.event('text', {'text': 'first'})
        cursor = self.store.events()[-1]['sequence']
        self.store.event('future-event', {'native': True})
        self.store.event('text', {'text': 'last'})
        self.assertEqual(['future-event', 'text'], [row['kind'] for row in Store(self.folder).events(cursor)])

    def test_damaged_execution_state_is_reported_and_preserved(self):
        for content in (b'broken\x00database', b''):
            self.store.path.write_bytes(content)
            with self.assertRaisesRegex(EnvironmentError, 'execution state'):
                Store(self.folder)
            self.assertEqual(content, self.store.path.read_bytes())

    def test_display_retention_keeps_pending_commands_and_a_bounded_tail(self):
        self.store.enqueue('pending', 'Do not lose this input.', ['message', 'fixture'])
        for number in range(6000):
            self.store.event('tool', {'item': {'id': str(number), 'name': 'Read', 'output': 'a' * 9000}})
        events = self.store.events()
        self.assertLessEqual(sum(event['size'] for event in events), 8388608)
        self.assertEqual('5999', events[-1]['payload']['item']['id'])
        self.assertEqual('queued', self.store.command('pending')['state'])

    def test_concurrent_console_controls_cannot_silently_replace_a_pending_action(self):
        self.store.request_control('first', 'interrupt')
        second = Store(self.folder)
        with self.assertRaisesRegex(EnvironmentError, 'control is pending'):
            second.request_control('second', 'stop')
        self.assertEqual('interrupt', self.store.value('control')['action'])
        self.store.set(control_result={'id': 'first', 'error': ''})
        second.request_control('second', 'stop')
        self.assertEqual('stop', self.store.value('control')['action'])
