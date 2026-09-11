"""Launcher error handling tests; no installs, database, or running server required."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('wq_start', Path(__file__).with_name('start.py'))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

class LauncherErrors(unittest.TestCase):
    def test_child_failure_is_nonzero_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            failure = subprocess.CalledProcessError(1, ['npm', 'run', 'build'])
            with patch.object(launcher, 'ROOT', root), patch.object(launcher, 'main', side_effect=failure), contextlib.redirect_stderr(output):
                self.assertEqual(launcher.entrypoint(), 1)
            self.assertIn('CalledProcessError', (root / 'startup-error.log').read_text(encoding='utf-8'))
            self.assertIn('启动未完成', output.getvalue())

    def test_log_write_failure_does_not_hide_original_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            root = Path(directory) / 'folder-does-not-exist'
            with patch.object(launcher, 'ROOT', root), patch.object(launcher, 'main', side_effect=RuntimeError('missing Node')), contextlib.redirect_stderr(output):
                self.assertEqual(launcher.entrypoint(), 1)
            self.assertIn('missing Node', output.getvalue())
            self.assertIn('无法写入错误日志', output.getvalue())

    def test_user_interrupt_does_not_create_error_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(launcher, 'ROOT', root), patch.object(launcher, 'main', side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.entrypoint(), 130)
            self.assertFalse((root / 'startup-error.log').exists())

if __name__ == '__main__':
    unittest.main()
