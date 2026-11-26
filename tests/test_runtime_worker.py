import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from projector import agents, codex_watch, inbox, runtime, watch
from projector.runtime_store import Store
from projector.core import EnvironmentError, UsageError
from projector.runtime_worker import Worker


class FakeBackend:
    def __init__(self):
        self.events = asyncio.Queue()
        self.submitted = []
        self.closed = False

    async def submit(self, text, command):
        self.submitted.append(text)
        return 'native-turn'

    async def interrupt(self):
        self.events.put_nowait({'type': 'turn_end', 'turn': 'native-turn', 'outcome': 'completed'})

    async def close(self):
        self.closed = True


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'XDG_STATE_HOME': self.temporary.name}).start()
        self.root = watch.state_root()
        self.session = 'managed-fixture'
        launch = {'id': 'launch-fixture', 'backend': 'codex', 'cwd': self.temporary.name, 'prompt': 'initial'}
        self.worker = Worker(self.root, launch)
        self.worker.session = self.session
        self.worker.folder = watch.session_folder(self.root, self.session)
        self.worker.store = Store(self.worker.folder)
        self.worker.backend = FakeBackend()
        self.worker.last_heartbeat = time.time()
        self.worker.state = 'ready'
        self.worker.snapshot()

    async def test_multiline_input_uses_the_existing_inbox_and_reaches_one_native_turn(self):
        text = '-v "hi" $HOME\nsecond paragraph\n\nlast'
        item = runtime.send(self.root, self.session, text)
        self.worker.backend.events.put_nowait({'type': 'text', 'text': 'answer', 'item': 'message'})
        await self.worker.tick()
        self.assertEqual([text], self.worker.backend.submitted)
        self.assertTrue(inbox.find(self.session, item['ref']).get('deliveredAt'))
        command = self.worker.store.commands()[0]
        self.assertEqual(('accepted', 'native-turn'), (command['state'], command['turn']))
        self.worker.backend.events.put_nowait({'type': 'turn_end', 'turn': 'native-turn', 'outcome': 'completed'})
        await self.worker.tick()
        self.assertEqual('completed', self.worker.store.commands()[0]['outcome'])
        self.assertEqual([text], self.worker.backend.submitted)

    async def test_interrupt_does_not_mark_success_or_start_queued_followups(self):
        self.worker.store.enqueue('first', 'first input', [])
        self.worker.store.enqueue('second', 'second input', [])
        self.worker.backend.events.put_nowait({'type': 'text', 'text': 'partial'})
        await self.worker.tick()
        await self.worker.act('interrupt')
        await self.worker.tick()
        self.assertEqual(['first input'], self.worker.backend.submitted)
        self.assertEqual(('finished', 'interrupted'), tuple(self.worker.store.command('first')[key] for key in ('state', 'outcome')))
        self.assertEqual('queued', self.worker.store.command('second')['state'])
        self.assertTrue(self.worker.paused)

    async def test_a_late_result_for_an_old_turn_cannot_finish_the_current_input(self):
        self.worker.store.enqueue('first', 'work', [])
        self.worker.backend.events.put_nowait({'type': 'turn_end', 'turn': 'old-turn', 'outcome': 'completed'})
        await self.worker.tick()
        self.assertEqual('accepted', self.worker.store.command('first')['state'])
        self.assertEqual('first', self.worker.current)

    async def test_native_approval_waits_for_the_inbox_answer_and_does_not_start_a_chat_turn(self):
        posted = asyncio.Event()
        original = inbox.ask
        def ask(*args, **kwargs):
            result = original(*args, **kwargs)
            posted.set()
            return result
        with patch.object(inbox, 'ask', side_effect=ask):
            task = asyncio.create_task(self.worker.request('item/commandExecution/requestApproval', {'command': 'touch output'}))
            await asyncio.wait_for(posted.wait(), 1)
            ref = next(iter(self.worker.requests))
            inbox.answer(self.session, ref, 'a')
            self.assertEqual({'decision': 'decline'}, await asyncio.wait_for(task, 1))
        self.assertTrue(inbox.find(self.session, ref).get('deliveredAt'))
        self.worker.ingest()
        self.assertEqual([], self.worker.store.commands())
        self.assertEqual([], self.worker.backend.submitted)

    async def test_a_managed_session_excludes_native_queue_and_panel_delivery(self):
        folder = self.worker.folder
        inbox.write_json(folder / 'codex.json', {'schema_version': 1, 'enabled': True, 'expires_at': time.time()+3600})
        self.assertEqual('managed', watch.delivery(self.root, self.session, time.time()))
        self.assertFalse(codex_watch.active(folder, time.time()))
        with self.assertRaisesRegex(UsageError, 'managed worker'):
            watch.take(self.root, self.session, time.time())
        with self.assertRaisesRegex(UsageError, 'managed worker'):
            watch.renew(self.root, self.session, 'mod', time.time())
        event = codex_watch.Event('A durable agent reply', ['message', 'm-test'])
        watch.append_events(folder, [event])
        self.assertEqual([{'key': event.key, 'line': str(event)}], codex_watch.queued(folder))

    async def test_handoff_refuses_an_active_turn_and_releases_the_backend_when_idle(self):
        self.worker.current = 'working'
        with self.assertRaisesRegex(EnvironmentError, 'Interrupt'):
            await self.worker.act('handoff')
        self.assertFalse(self.worker.backend.closed)
        self.worker.current = None
        backend = self.worker.backend
        await self.worker.act('handoff')
        self.assertTrue(backend.closed)
        self.assertEqual(('handed-off', 'native'), (self.worker.state, runtime.record(self.worker.folder)['owner']))

    async def test_a_second_worker_cannot_acquire_the_live_workers_session(self):
        with runtime.worker_lock(self.worker.folder) as first:
            self.assertIsNotNone(first)
            with runtime.worker_lock(self.worker.folder) as second:
                self.assertIsNone(second)
        with runtime.worker_lock(self.worker.folder) as next_owner:
            self.assertIsNotNone(next_owner)

    async def test_a_condition_on_an_approval_denies_the_original_action_and_preserves_the_note(self):
        task = asyncio.create_task(self.worker.request('claude/approval', {'tool': 'Write'}))
        await asyncio.sleep(0)
        ref = next(iter(self.worker.requests))
        inbox.answer(self.session, ref, 'b', note='Only write inside the checkout.')
        result = await asyncio.wait_for(task, 1)
        self.assertEqual({'decision': 'decline', 'message': 'Only write inside the checkout.'}, result)
        self.worker.ingest()
        self.assertIn('Only write inside the checkout.', self.worker.store.commands()[0]['text'])

    async def test_native_questions_keep_the_question_id_and_the_persons_note(self):
        task = asyncio.create_task(self.worker.request('item/tool/requestUserInput', {'questions': [
            {'id': 'target', 'question': 'Where should tests run?', 'options': [{'label': 'Local'}]}]}))
        await asyncio.sleep(0)
        ref = next(iter(self.worker.requests))
        inbox.answer(self.session, ref, 'a', note='Use the temporary fixture.')
        self.assertEqual({'answers': {'target': {'answers': ['Local\n\nUse the temporary fixture.']}}},
                         await asyncio.wait_for(task, 1))

    async def test_interrupt_expires_a_native_request_before_a_new_turn_can_use_its_answer(self):
        task = asyncio.create_task(self.worker.request('claude/approval', {'tool': 'Write'}))
        await asyncio.sleep(0)
        ref = next(iter(self.worker.requests))
        self.worker.interrupted = True
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertTrue(inbox.find(self.session, ref)['answer']['withdrawn'])
        self.assertEqual(set(), self.worker.requests)

    async def test_closing_a_pending_request_keeps_the_workers_failure_visible(self):
        self.worker.current = 'active-command'
        task = asyncio.create_task(self.worker.request('claude/approval', {'tool': 'Write'}))
        await asyncio.sleep(0)
        self.worker.state = 'failed'
        self.worker.snapshot(error='Native transport disconnected')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        current = runtime.record(self.worker.folder)
        self.assertEqual(('failed', 'Native transport disconnected'), (current['state'], current['error']))

    async def test_continuous_text_flushes_without_waiting_for_a_pause_in_native_output(self):
        self.worker.store.enqueue('stream', 'work', [])
        self.worker.last_flush -= 1
        self.worker.backend.events.put_nowait({'type': 'text', 'text': 'visible now'})
        await self.worker.tick()
        self.assertEqual(['visible now'], [event['payload']['text'] for event in self.worker.store.events()
                                         if event['kind'] == 'text'])
        self.worker.next_ingest = time.monotonic() + 10
        with patch.object(self.worker, 'ingest', side_effect=AssertionError('Rescanned inbox for each delta')), \
                patch.object(self.worker.store, 'accepted', side_effect=AssertionError('Rewrote acceptance for each delta')):
            self.worker.backend.events.put_nowait({'type': 'text', 'text': ' more'})
            await self.worker.tick()

    async def test_restart_withdraws_dead_native_approvals_and_preserves_uncertain_input(self):
        item = inbox.ask(self.session, 'Allow the old tool?', 'Deny', ['Allow once'])
        self.worker.store.set(requests=[item['ref']])
        self.worker.store.enqueue('possibly-accepted', 'Do not replay', [])
        self.worker.store.begin('possibly-accepted', 'dead-worker')
        self.worker.stopping = True
        with patch.object(agents, 'register', return_value=({}, '')):
            await self.worker.run_owned()
        self.assertTrue(inbox.find(self.session, item['ref'])['answer']['withdrawn'])
        self.assertEqual('uncertain', self.worker.store.command('possibly-accepted')['state'])
        self.assertTrue(self.worker.paused)
        self.assertEqual([], self.worker.backend.submitted)

    async def test_restart_keeps_an_interrupted_queue_paused_and_expires_old_controls(self):
        self.worker.paused = True
        self.worker.snapshot()
        self.worker.store.enqueue('later', 'Wait for explicit send', [])
        self.worker.store.request_control('old-stop', 'stop')
        self.worker.stopping = True
        self.worker.paused = False
        with patch.object(agents, 'register', return_value=({}, '')):
            await self.worker.run_owned()
        self.assertTrue(self.worker.paused)
        self.assertEqual('queued', self.worker.store.command('later')['state'])
        self.assertEqual('old-stop', self.worker.store.value('control_result')['id'])
        self.worker.store.request_control('new-send', 'send')
