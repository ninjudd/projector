import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from projector import inbox, runtime, watch


@unittest.skipIf(os.name == 'nt', 'Native terminal leases use POSIX file descriptors')
class NativeHandoffTests(unittest.TestCase):
    def test_interrupt_and_wrapper_exit_do_not_release_a_native_terminals_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'watch'
            folder = watch.session_folder(root, 'native-fixture')
            executable = Path(temporary) / 'native'
            executable.write_text('#!' + sys.executable + '''
import json, os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_IGN)
print(json.dumps({"pid": os.getpid()}), flush=True)
sys.stdin.readline()
''')
            executable.chmod(0o755)
            with inbox.locked(folder):
                inbox.write_json(folder / 'runtime.json', {'session': 'native-fixture', 'owner': 'native',
                    'backend': 'codex', 'executable': str(executable), 'cwd': temporary})
            env = {**os.environ, 'PYTHONPATH': str(Path(runtime.__file__).parent.parent)}
            helper = subprocess.Popen([sys.executable, '-m', 'projector.native_handoff', str(root), 'native-fixture'],
                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, text=True)
            try:
                self.assertTrue(select.select([helper.stdout], [], [], 5)[0], 'Native terminal did not start')
                self.assertGreater(json.loads(helper.stdout.readline())['pid'], 0)
                os.killpg(helper.pid, signal.SIGINT)
                time.sleep(.3)
                self.assertIsNone(helper.poll(), 'Ctrl-C exited the wrapper before its terminal')
                with runtime.worker_lock(folder, 'runtime-native.lock') as held:
                    self.assertIsNone(held)
                helper.terminate()
                helper.wait(timeout=5)
                with runtime.worker_lock(folder, 'runtime-native.lock') as held:
                    self.assertIsNone(held, 'The child lost the inherited lease when its wrapper exited')
                helper.stdin.write('exit\n')
                helper.stdin.flush()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with runtime.worker_lock(folder, 'runtime-native.lock') as held:
                        if held is not None:
                            break
                    time.sleep(.02)
                else:
                    self.fail('The native lease remained held after the child exited')
            finally:
                try:
                    os.killpg(helper.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                helper.wait(timeout=5)
                for stream in (helper.stdin, helper.stdout, helper.stderr):
                    stream.close()
