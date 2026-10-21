"""The signed Windows updater must remain available after online switching."""
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest import TestCase
from unittest.mock import Mock, patch

import desktop
from sedo.windows_updates import Update


class FakeWindow:
    def __init__(self):
        self.destroyed = False
        self.dialogs = []

    def evaluate_js(self, expression):
        self.dialogs.append(expression)
        return expression.startswith('confirm(')

    def run_js(self, expression):
        pass

    def destroy(self):
        self.destroyed = True


class ImmediateThread:
    def __init__(self, *, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def fake_webview():
    webview = types.ModuleType('webview')
    webview.__path__ = []
    webview.settings = {}
    menu = types.ModuleType('webview.menu')
    menu.MenuSeparator = lambda: ('separator',)
    menu.MenuAction = lambda name, callback: ('action', name, callback)
    menu.Menu = lambda name, entries: ('menu', name, entries)
    webview.menu = menu
    return {'webview': webview, 'webview.menu': menu}


def fake_backup(*, result=Path('backup.zip')):
    module = types.ModuleType('sedo.backup_utils')
    module.create_backup = Mock(return_value=result)
    module.inspect_backup = Mock(return_value={'integrity': 'ok'})
    return module


class OnlineWindowsUpdateTests(TestCase):
    def _actions(self, window, *, online):
        with patch.object(desktop.os, 'name', 'nt'), patch.object(desktop.sys, 'frozen', True,
                                                                  create=True):
            return desktop._windows_update_actions(window, online=online)

    def test_online_window_shows_windows_update_action(self):
        window = FakeWindow()
        webview = fake_webview()
        webview['webview'].create_window = Mock(return_value=window)
        webview['webview'].start = Mock()
        with patch.dict(sys.modules, webview), patch.object(desktop, '_windows_update_actions',
                                                            return_value=[('update',)]) as actions:
            self.assertEqual(desktop._run_online('https://rechnung.example.test'), 0)
        actions.assert_called_once_with(window, online=True)
        help_menu = webview['webview'].start.call_args.kwargs['menu'][1]
        self.assertIn(('update',), help_menu[2])

    def test_online_update_skips_local_backup_and_creates_log_directory(self):
        window = FakeWindow()
        with patch.dict(sys.modules, fake_webview()):
            action = self._actions(window, online=True)[-1][2]
        update = Update('3.21.0.8', 'https://example.test/setup.exe', '0' * 64, 123)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            installer = home / 'updates' / 'setup.exe'
            backup = fake_backup()
            with patch.dict(sys.modules, {'sedo.backup_utils': backup}), \
                    patch.object(desktop, '_user_home', return_value=home), \
                    patch.object(desktop.threading, 'Thread', ImmediateThread), \
                    patch('sedo.windows_updates.check', return_value=update), \
                    patch('sedo.windows_updates.download', return_value=installer) as download, \
                    patch('sedo.windows_updates.start_installer') as start:
                action()
            backup.create_backup.assert_not_called()
            download.assert_called_once_with(update, home / 'updates')
            start.assert_called_once_with(installer, Path(sys.executable),
                                          home / 'logs' / 'windows-update-3.21.0.8.log')
            self.assertTrue((home / 'logs').is_dir())
            self.assertTrue(window.destroyed)
            self.assertTrue(any('Firmenserver' in item for item in window.dialogs))

    def test_local_update_still_requires_checked_backup(self):
        window = FakeWindow()
        with patch.dict(sys.modules, fake_webview()):
            action = self._actions(window, online=False)[-1][2]
        update = Update('3.21.0.8', 'https://example.test/setup.exe', '0' * 64, 123)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            backup = fake_backup()
            with patch.dict(sys.modules, {'sedo.backup_utils': backup}), \
                    patch.dict(os.environ, {'SEDO_HOME': str(home)}), \
                    patch.object(desktop.threading, 'Thread', ImmediateThread), \
                    patch('sedo.windows_updates.check', return_value=update), \
                    patch('sedo.windows_updates.download', return_value=home / 'setup.exe'), \
                    patch('sedo.windows_updates.start_installer') as start:
                action()
            backup.create_backup.assert_called_once_with('pre_windows_update')
            backup.inspect_backup.assert_called_once_with('backup.zip')
            self.assertTrue(start.called)
            self.assertTrue(window.destroyed)
