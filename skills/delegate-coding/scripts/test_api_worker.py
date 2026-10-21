"""Offline behavior tests; real Git and subprocesses, no model/network calls."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent / 'api_worker.py'
spec = importlib.util.spec_from_file_location('api_worker', SCRIPT)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

FAKE = r'''
import json, os, pathlib, subprocess, sys, time, tomllib
if '--version' in sys.argv:
    print('codex-offline-fake 1.0')
    sys.exit(0)
if '--help' in sys.argv:
    print('--json --output-last-message --sandbox')
    sys.exit(0)
if sys.argv[1:] == ['debug', 'models']:
    print((pathlib.Path(os.environ['CODEX_HOME']) / 'models.json').read_text())
    sys.exit(0)
workspace = pathlib.Path(sys.argv[sys.argv.index('--cd') + 1])
output = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
cfg = tomllib.loads((pathlib.Path(os.environ['CODEX_HOME']) / 'config.toml').read_text())
key_env = cfg['model_providers']['api_worker']['env_key']
key = os.environ[key_env]
task = json.loads(sys.stdin.read().split('Task specification:\n', 1)[1].split('\nDo not modify', 1)[0])
mode = task['title']
if mode == 'timeout':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    (output.parent / 'child.pid').write_text(str(child.pid))
    time.sleep(120)
if mode == 'incomplete':
    sys.exit(0)
if mode == 'fail':
    print(json.dumps({'type':'turn.failed'}), flush=True)
    sys.exit(7)
target = 'other.txt' if mode == 'scope' else 'allowed.txt'
(workspace / target).write_text('implemented\n')
if mode == 'stage':
    subprocess.run(['git', '-C', str(workspace), 'add', target], check=True)
if mode == 'commit':
    subprocess.run(['git', '-C', str(workspace), 'add', target], check=True)
    subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=offline', '-c', 'user.email=offline@example.invalid', 'commit', '-m', 'worker forbidden commit'], check=True, capture_output=True)
output.write_text('Implemented and checked. ' + key)
print(json.dumps({'type':'item.completed','key_echo':key}), flush=True)
print(key, file=sys.stderr, flush=True)
(output.parent / 'env-check.json').write_text(json.dumps({
    'home': os.environ['CODEX_HOME'],
    'openai_present': 'OPENAI_API_KEY' in os.environ,
    'parent_codex_present': 'CODEX_PARENT_SESSION' in os.environ,
    'ds_present': bool(os.environ.get('DEEPSEEK_API_KEY')),
    'profile_key_env': key_env,
    'profile_key_present': bool(key),
    'active': os.environ.get('CODING_API_WORKER_ACTIVE')
}))
print(json.dumps({'type':'turn.completed'}), flush=True)
'''


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root, self.repo = self.base / 'state', self.base / 'repo'
        self.root.mkdir()
        self.repo.mkdir()
        self.settings = {'schema_version':1, 'base_url':'https://api.deepseek.com', 'model':'deepseek-flash',
                         'context_window':128000, 'windows_sandbox':'elevated'}
        w.dump(self.root / 'worker.json', self.settings)
        self.fake = self.base / 'fake.py'
        self.fake.write_text(FAKE, encoding='utf-8')
        self.git('init', '-q')
        (self.repo / 'allowed.txt').write_text('original\n')
        (self.repo / 'other.txt').write_text('original\n')
        self.git('add', '.')
        self.git('-c', 'user.name=offline', '-c', 'user.email=offline@example.invalid', 'commit', '-qm', 'initial')
        self.key = 'sk-FAKE-OFFLINE-NOT-A-REAL-KEY'

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True).stdout

    def args(self, mode='normal', **kw):
        task = self.base / ('task-' + mode + '.json')
        w.dump(task, {'title':mode, 'goal':'Implement a bounded edit', 'allowed_paths':['allowed.txt'],
                      'constraints':['No commits'], 'acceptance':['Read allowed.txt and verify content']})
        values = dict(task=str(task), workspace=str(self.repo), codex=None, output_dir=str(self.base / ('out-' + mode)), timeout=10, dry_run=False)
        values.update(kw)
        return argparse.Namespace(**values)

    def invoke(self, args):
        with patch.object(w, 'resolve_codex', return_value=[sys.executable, str(self.fake)]), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY':self.key, 'OPENAI_API_KEY':'parent-secret', 'CODEX_PARENT_SESSION':'parent'}), \
             contextlib.redirect_stdout(io.StringIO()):
            return w.run(self.root, args)

    def test_profile_inside_workspace_rejected_before_credentials(self):
        nested = self.repo / 'ignored-profile'
        nested.mkdir()
        w.dump(nested / 'worker.json', self.settings)
        with patch.object(w, 'get_key') as key_reader, self.assertRaisesRegex(ValueError, 'outside'):
            w.run(nested, self.args())
        key_reader.assert_not_called()

    def test_completed_run_captures_diff_and_isolates_auth(self):
        args = self.args()
        self.assertEqual(self.invoke(args), 0)
        out = Path(args.output_dir)
        result = w.load(out / 'result.json')
        self.assertEqual(result['status'], 'ready_for_review')
        self.assertFalse(result['accepted_by_gpt'])
        self.assertEqual(result['changed_files'], ['allowed.txt'])
        self.assertIn('+implemented', (out / 'changes.patch').read_text())
        env = w.load(out / 'env-check.json')
        self.assertFalse(env['openai_present'])
        self.assertFalse(env['parent_codex_present'])
        self.assertEqual(env['home'], str(self.root / 'codex-home'))
        self.assertEqual(w.load(out / 'invocation.json')['codex_home'], str(self.root / 'codex-home'))
        for name in ['final.md', 'events.jsonl', 'stderr.log', 'invocation.json', 'task.json']:
            self.assertNotIn(self.key, (out / name).read_text())
        cfg = tomllib.loads((self.root / 'codex-home/config.toml').read_text())
        self.assertEqual(cfg['model_provider'], 'api_worker')
        self.assertEqual(cfg['windows']['sandbox'], 'elevated')
        self.assertEqual(cfg['sandbox_mode'], 'workspace-write')
        self.assertEqual(cfg['approval_policy'], 'never')
        self.assertFalse(cfg['sandbox_workspace_write']['network_access'])
        self.assertFalse(cfg['agents']['enabled'])
        self.assertFalse(cfg['model_providers']['api_worker']['requires_openai_auth'])
        self.assertFalse(list((self.root / 'locks').glob('*.lock')))

    def test_scope_violation_preserves_actual_work(self):
        args = self.args('scope')
        self.assertEqual(self.invoke(args), 3)
        self.assertEqual(w.load(Path(args.output_dir) / 'result.json')['scope_violations'], ['other.txt'])
        self.assertEqual((self.repo / 'other.txt').read_text(), 'implemented\n')

    def test_staged_or_committed_changes_require_review(self):
        for mode in ['stage', 'commit']:
            with self.subTest(mode=mode):
                args = self.args(mode)
                self.assertEqual(self.invoke(args), 3)
                self.assertEqual(w.load(Path(args.output_dir) / 'result.json')['status'], 'review_required')
                # Test fixture recovery only: explicitly named temporary test repo.
                self.git('reset', '--hard', '-q', 'HEAD~1' if mode == 'commit' else 'HEAD')

    def test_incomplete_and_failed_runs_not_accepted(self):
        for mode in ['incomplete', 'fail']:
            with self.subTest(mode=mode):
                args = self.args(mode)
                self.assertNotEqual(self.invoke(args), 0)
                self.assertFalse(w.load(Path(args.output_dir) / 'result.json')['accepted_by_gpt'])

    def test_timeout_kills_descendant_and_releases_lock(self):
        args = self.args('timeout', timeout=2)
        self.assertEqual(self.invoke(args), 124)
        out = Path(args.output_dir)
        self.assertEqual(w.load(out / 'result.json')['status'], 'timed_out')
        child_pid = int((out / 'child.pid').read_text())
        if os.name == 'nt':
            kernel = __import__('ctypes').WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [__import__('ctypes').c_uint32, __import__('ctypes').c_int, __import__('ctypes').c_uint32]
            kernel.OpenProcess.restype = __import__('ctypes').c_void_p
            handle = kernel.OpenProcess(0x1000, False, child_pid)
            if handle:
                code = __import__('ctypes').c_uint32()
                kernel.GetExitCodeProcess.argtypes = [__import__('ctypes').c_void_p, __import__('ctypes').POINTER(__import__('ctypes').c_uint32)]
                kernel.GetExitCodeProcess(handle, __import__('ctypes').byref(code))
                kernel.CloseHandle.argtypes = [__import__('ctypes').c_void_p]
                kernel.CloseHandle(handle)
                self.assertNotEqual(code.value, 259)  # STILL_ACTIVE
        self.assertFalse(list((self.root / 'locks').glob('*.lock')))

    def test_dirty_checkout_and_invalid_paths_rejected(self):
        (self.repo / 'allowed.txt').write_text('user work\n')
        with self.assertRaisesRegex(ValueError, 'not clean'):
            self.invoke(self.args())
        for name in ['../escape', 'C:/secret', '/absolute', 'src\\file']:
            with self.subTest(path=name), self.assertRaises(ValueError):
                w.validate_relative(name)

    def test_dry_run_needs_no_key_or_model_request(self):
        settings = {k:v for k, v in self.settings.items() if k != 'windows_sandbox'}
        w.dump(self.root / 'worker.json', settings)
        args = self.args('dry', dry_run=True)
        with patch.object(w, 'get_key', side_effect=AssertionError('Must not read key in dry-run')):
            self.assertEqual(self.invoke(args), 0)
        out = Path(args.output_dir)
        self.assertEqual(w.load(out / 'result.json')['status'], 'dry_run')
        self.assertFalse((out / 'final.md').exists())
        cfg = tomllib.loads((self.root / 'codex-home/config.toml').read_text())
        self.assertNotIn('windows', cfg)

    def test_windows_live_run_needs_explicit_backend_before_task_or_credentials(self):
        settings = {k:v for k, v in self.settings.items() if k != 'windows_sandbox'}
        w.dump(self.root / 'worker.json', settings)
        with patch.object(w.os, 'name', 'nt'), patch.object(w, 'get_key') as key_reader, \
             patch.object(w, 'read_task') as task_reader, self.assertRaisesRegex(ValueError, 'windows_sandbox'):
            w.run(self.root, self.args())
        key_reader.assert_not_called()
        task_reader.assert_not_called()
        self.assertFalse((self.root / 'locks').exists())

    def test_windows_backend_validation_and_config_are_explicit(self):
        for backend in w.WINDOWS_SANDBOX_BACKENDS:
            with self.subTest(backend=backend):
                settings = w.validate_settings({**self.settings, 'windows_sandbox':backend})
                target = self.base / ('home-' + backend)
                w.prepare_home(target, settings)
                cfg = tomllib.loads((target / 'config.toml').read_text())
                self.assertEqual(cfg['windows']['sandbox'], backend)
                self.assertEqual(cfg['sandbox_mode'], 'workspace-write')
                self.assertEqual(cfg['approval_policy'], 'never')
                self.assertFalse(cfg['sandbox_workspace_write']['network_access'])
        for backend in (None, '', True, [], {}, 'disabled', self.key):
            with self.subTest(backend_type=type(backend).__name__), self.assertRaises(ValueError) as caught:
                w.validate_settings({**self.settings, 'windows_sandbox':backend})
            self.assertNotIn(self.key, str(caught.exception))

    def test_legacy_settings_do_not_gain_implicit_backend(self):
        legacy = {k:v for k, v in self.settings.items() if k != 'windows_sandbox'}
        self.assertNotIn('windows_sandbox', w.validate_settings(legacy))
        with patch.object(w.os, 'name', 'posix'):
            w.require_windows_sandbox(w.validate_settings(legacy))

    def test_credentials_never_go_into_catalog_or_config(self):
        target = self.base / 'home'
        w.prepare_home(target, self.settings)
        cfg = tomllib.loads((target / 'config.toml').read_text())
        self.assertEqual(cfg['model_providers']['api_worker']['env_key'], 'DEEPSEEK_API_KEY')
        self.assertNotIn('experimental_bearer_token', cfg['model_providers']['api_worker'])
        self.assertEqual(w.load(target / 'models.json')['models'][0]['slug'], 'deepseek-flash')

    def test_custom_key_environment_and_provider_are_used(self):
        settings = {**self.settings, 'key_env':'VENDOR_ACCESS',
                    'provider_name':'Example Responses provider',
                    'base_url':'https://responses.example.invalid/v1', 'model':'example/code-model'}
        w.dump(self.root / 'worker.json', settings)
        args = self.args()
        with patch.dict(os.environ, {'VENDOR_ACCESS':self.key}):
            self.assertEqual(self.invoke(args), 0)
        out = Path(args.output_dir)
        cfg = tomllib.loads((self.root / 'codex-home/config.toml').read_text())
        self.assertEqual(cfg['model_providers']['api_worker']['env_key'], 'VENDOR_ACCESS')
        self.assertEqual(cfg['model_providers']['api_worker']['name'], 'Example Responses provider')
        self.assertEqual(cfg['shell_environment_policy']['filters']['VENDOR_ACCESS'], 'exclude')
        env = w.load(out / 'env-check.json')
        self.assertFalse(env['openai_present'])
        self.assertFalse(env['ds_present'])
        self.assertTrue(env['profile_key_present'])
        self.assertEqual(env['profile_key_env'], 'VENDOR_ACCESS')
        self.assertEqual(w.load(self.root / 'codex-home/models.json')['models'][0]['slug'], 'example/code-model')
        for name in ('final.md', 'stderr.log', 'events.jsonl', 'invocation.json'):
            self.assertNotIn(self.key, (out / name).read_text())

    def test_profiles_use_only_their_configured_key(self):
        settings = w.validate_settings({**self.settings, 'key_env':'VENDOR_ACCESS'})
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':self.key, 'VENDOR_ACCESS':'profile-specific-fake'}, clear=True):
            self.assertEqual(w.get_key(self.root, settings), 'profile-specific-fake')
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':self.key}, clear=True):
            self.assertEqual(w.get_key(self.root, settings), '')

    def test_environment_names_cannot_override_runtime_settings(self):
        for value in ('PATH', 'CODEX_HOME', 'CODING_API_WORKER_ACTIVE', 'BAD-NAME', 'x\nTOKEN', ''):
            with self.subTest(name=value), self.assertRaises(ValueError):
                w.validate_settings({**self.settings, 'key_env':value})

    def test_init_accepts_custom_profile(self):
        root = self.base / 'custom-profile'
        args = argparse.Namespace(base_url='https://responses.example.invalid/v1', model='example/model',
                                  context_window=64000, key_env='VENDOR_ACCESS', provider_name='Example API')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.init(root, args), 0)
        settings = w.load(root / 'worker.json')
        self.assertEqual(settings['key_env'], 'VENDOR_ACCESS')
        self.assertEqual(settings['provider_name'], 'Example API')
        self.assertNotIn('windows_sandbox', settings)

    def test_init_cli_accepts_explicit_windows_backend(self):
        root = self.base / 'sandbox-profile'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.main(['--worker-root', str(root), 'init', '--windows-sandbox', 'elevated']), 0)
        self.assertEqual(w.load(root / 'worker.json')['windows_sandbox'], 'elevated')

    def test_custom_profile_timeout_keeps_actual_status(self):
        w.dump(self.root / 'worker.json', {**self.settings, 'key_env':'VENDOR_ACCESS'})
        args = self.args('timeout', timeout=1)
        with patch.dict(os.environ, {'VENDOR_ACCESS':self.key}):
            self.assertEqual(self.invoke(args), 124)
        result = w.load(Path(args.output_dir) / 'result.json')
        self.assertEqual(result['status'], 'timed_out')
        self.assertFalse(result['accepted_by_gpt'])

    def test_init_refuses_overwrite_and_provider_urls_are_strict(self):
        original = (self.root / 'worker.json').read_bytes()
        with self.assertRaises(FileExistsError):
            w.init(self.root, argparse.Namespace(base_url='https://api.deepseek.com', model='deepseek-flash', context_window=128000))
        self.assertEqual(original, (self.root / 'worker.json').read_bytes())
        for url in ['http://api.deepseek.com', 'https://user:key@api.deepseek.com', 'https://api.deepseek.com?key=bad', 'https://api.deepseek.com/responses']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                w.validate_settings({**self.settings, 'base_url':url})

    def test_redaction_across_chunk_boundary(self):
        key = 'secret-value'
        data = io.BytesIO(b'a' * (1024 * 1024 - 5) + key.encode() + b'after\n')
        target = self.base / 'redacted.log'
        w.redact_stream(data, target, key)
        raw = target.read_bytes()
        self.assertNotIn(key.encode(), raw)
        self.assertIn(b'[REDACTED]after', raw)

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows-only')
    def test_dpapi_round_trip_is_encrypted(self):
        value = b'offline-fake-secret'
        try:
            encrypted = w.dpapi(value)
        except OSError as error:
            if error.errno == 2:
                self.skipTest('Current sandbox user has no loaded DPAPI profile; independent .NET DPAPI check also fails')
            raise
        self.assertNotIn(value, encrypted)
        self.assertEqual(w.dpapi(encrypted, decrypt=True), value)

    def test_timeout_still_works_if_worker_never_reads_large_stdin(self):
        out = self.base / 'blocked-stdin'
        out.mkdir()
        home = out / 'home'
        home.mkdir()
        start = time.monotonic()
        status, code, events = w.execute([sys.executable, '-c', 'import time; time.sleep(120)'],
                                         'x' * 100000, home, self.key, out, 1)
        self.assertEqual((status, code), ('timed_out', 124))
        self.assertLess(time.monotonic() - start, 8)

    def test_recursive_run_rejected(self):
        with patch.dict(os.environ, {'CODING_API_WORKER_ACTIVE':'1'}), self.assertRaisesRegex(ValueError, 'Recursive'):
            w.run(self.root, self.args())

    def test_doctor_checks_local_catalog_without_model_call(self):
        captured = io.StringIO()
        with patch.object(w, 'resolve_codex', return_value=[sys.executable, str(self.fake)]), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY':self.key}), contextlib.redirect_stdout(captured):
            self.assertEqual(w.doctor(self.root, None), 0)
        report = json.loads(captured.getvalue())
        self.assertTrue(report['offline'])
        self.assertTrue(report['checks']['catalog']['ok'])
        self.assertNotIn(self.key, captured.getvalue())

    def test_windows_doctor_missing_backend_is_not_ready(self):
        settings = {k:v for k, v in self.settings.items() if k != 'windows_sandbox'}
        w.dump(self.root / 'worker.json', settings)
        captured = io.StringIO()
        with patch.object(w.os, 'name', 'nt'), \
             patch.object(w, 'resolve_codex', return_value=[sys.executable, str(self.fake)]), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY':self.key}), contextlib.redirect_stdout(captured):
            self.assertEqual(w.doctor(self.root, None), 2)
        report = json.loads(captured.getvalue())
        self.assertFalse(report['ready_for_live_test'])
        self.assertFalse(report['checks']['windows_sandbox']['ok'])
        self.assertTrue(report['checks']['catalog']['ok'])
        self.assertNotIn(self.key, captured.getvalue())

    def test_unknown_settings_rejected_without_secret_leak(self):
        for extra in ({'api_key':self.key}, {'unknown-' + self.key:'value'}):
            with self.subTest(field=list(extra)[0]):
                with self.assertRaises(ValueError) as caught:
                    w.validate_settings({**self.settings, **extra})
                self.assertNotIn(self.key, str(caught.exception))
                self.assertNotIn(list(extra)[0], str(caught.exception))
                w.dump(self.root / 'worker.json', {**self.settings, **extra})
                captured = io.StringIO()
                with patch.object(w, 'resolve_codex', return_value=[sys.executable, str(self.fake)]), \
                     patch.object(w, 'get_key', side_effect=AssertionError('Invalid settings must not read credentials')), \
                     contextlib.redirect_stdout(captured):
                    self.assertEqual(w.doctor(self.root, None), 2)
                self.assertNotIn(self.key, captured.getvalue())
                self.assertNotIn(list(extra)[0], captured.getvalue())
                self.assertFalse(json.loads(captured.getvalue())['checks']['settings']['ok'])

    def test_non_object_settings_are_value_errors(self):
        for value in (None, [], self.key, 1, True):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError) as caught:
                w.validate_settings(value)
            self.assertNotIn(self.key, str(caught.exception))

    def test_non_string_or_malformed_base_urls_are_value_errors(self):
        for value in (None, 42, [], {}, b'https://api.example.invalid', 'https://[' + self.key + ']'):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError) as caught:
                w.validate_settings({**self.settings, 'base_url':value})
            self.assertNotIn(self.key, str(caught.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
