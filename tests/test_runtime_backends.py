import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from projector.core import EnvironmentError
from projector.runtime_backends import Claude, Codex

PEER = r'''
import json, sys

def emit(value):
    print(json.dumps(value), flush=True)

pending = None
for line in sys.stdin:
    value = json.loads(line)
    method = value.get('method')
    if method == 'initialize':
        emit({'id': value['id'], 'result': {}})
    elif method == 'thread/start':
        emit({'id': value['id'], 'result': {'thread': {'id': 'native-thread'},
             'model': 'native-default', 'approvalPolicy': 'on-request', 'sandbox': {'type': 'readOnly'}}})
    elif method == 'thread/resume':
        emit({'id': value['id'], 'error': {'code': -32600, 'message': 'active writer'}})
    elif method == 'turn/start':
        pending = value
        emit({'id': 'approval', 'method': 'item/commandExecution/requestApproval',
              'params': {'threadId': 'native-thread', 'command': 'a protected action'}})
    elif method == 'policy/read':
        emit({'id': value['id'], 'result': {'allowed': False}})
    elif value.get('id') == 'approval' and pending:
        decision = value.get('result', {}).get('decision', 'error')
        text = pending['params']['input'][0]['text'] + ':' + decision
        emit({'id': pending['id'], 'result': {'turn': {'id': 'native-turn', 'status': 'inProgress'}}})
        emit({'method': 'item/agentMessage/delta', 'params': {'threadId': 'another-thread', 'delta': 'wrong session'}})
        emit({'method': 'item/agentMessage/delta', 'params': {'threadId': 'native-thread', 'turnId': 'native-turn',
              'itemId': 'answer', 'delta': text}})
        emit({'method': 'item/completed', 'params': {'threadId': 'native-thread', 'turnId': 'native-turn',
              'item': {'id': 'answer', 'type': 'agentMessage', 'text': text}}})
        emit({'method': 'future/notification', 'params': {'threadId': 'native-thread'}})
        emit({'method': 'turn/completed', 'params': {'threadId': 'native-thread',
              'turn': {'id': 'native-turn', 'status': 'completed'}}})
'''


@unittest.skipIf(os.name == 'nt', 'The terminal runtime uses POSIX executables')
class CodexAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.executable = self.folder / 'fake codex'
        self.executable.write_text('#!' + sys.executable + '\n' + PEER)
        self.executable.chmod(0o755)

    def client(self, request, **options):
        return Codex({'cwd': str(self.folder), 'executable': str(self.executable), **options}, request)

    async def test_server_requests_can_finish_before_their_parent_request_and_streams_do_not_duplicate_text(self):
        async def request(method, params):
            policy = await client.call('policy/read', {})
            return {'decision': 'accept' if policy['allowed'] else 'decline'}
        client = self.client(request)
        await client.open()
        try:
            text='--help "literal" $HOME\nsecond'
            self.assertEqual('native-turn', await asyncio.wait_for(client.submit(text, 'command-one'), 2))
            chunks=[]
            while True:
                event=await asyncio.wait_for(client.events.get(), 2)
                if event['type'] == 'text':
                    chunks.append(event['text'])
                if event['type'] == 'turn_end':
                    self.assertEqual(('native-turn', 'completed'), (event['turn'], event['outcome']))
                    break
            self.assertEqual(text+':decline', ''.join(chunks))
        finally:
            await client.close()
        self.assertIsNotNone(client.process.returncode)

    async def test_a_native_rpc_error_closes_the_process_and_does_not_fork_a_missing_resume(self):
        async def request(method, params):
            raise AssertionError('No server request expected')
        client=self.client(request, session='saved-thread')
        with self.assertRaisesRegex(EnvironmentError, 'active writer'):
            await asyncio.wait_for(client.open(), 2)
        self.assertIsNotNone(client.process.returncode)

    async def test_malformed_protocol_output_fails_pending_calls_without_leaking_the_process(self):
        self.executable.write_text('#!'+sys.executable+'\nprint("not-json", flush=True)\n')
        async def request(method, params):
            raise AssertionError('No server request expected')
        client=self.client(request)
        with self.assertRaisesRegex(EnvironmentError, 'Invalid Codex stream'):
            await asyncio.wait_for(client.open(), 2)
        self.assertIsNotNone(client.process.returncode)


class ClaudeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_ended_native_stream_reports_disconnect_instead_of_hanging_the_worker(self):
        class ClosedClient:
            async def receive_messages(self):
                if False:
                    yield
        backend = Claude({}, None)
        backend.client = ClosedClient()
        await backend.read()
        event = await asyncio.wait_for(backend.events.get(), .1)
        self.assertEqual('disconnected', event['type'])
