"""Autostart stays local, idempotent and separate from tests/builds."""
import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.models import AppSetting
from sedo.services import bonsai_runtime as runtime
from sedo.services.ai_local import AIError

CFG = {'enabled': True, 'autostart': True, 'runtime_dir': '',
       'base_url': 'http://127.0.0.1:8080/v1', 'text_model': ''}


class RuntimeTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir()
        for name in ['start_llama_server.sh','start_llama_server.ps1']:
            (self.root/'scripts'/name).write_text('# inert test fixture\n')
        self.cfg={**CFG,'runtime_dir':str(self.root)}
        self.lock=MagicMock()
        self.patcher=patch.object(runtime,'acquire',return_value=self.lock)
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def test_running_server_reused_without_launch_even_if_folder_missing(self):
        with patch.object(runtime,'_port_open',return_value=True), \
             patch.object(runtime,'check_local_model',return_value='bonsai'), \
             patch.object(runtime.subprocess,'Popen') as spawn:
            runtime._start({**self.cfg,'runtime_dir':'/missing/folder'})
        spawn.assert_not_called()
        self.lock.close.assert_called_once()
        self.assertIn('bereit',runtime.runtime_status())

    def test_launch_is_local_without_shell_or_sedo_secrets(self):
        with patch.object(runtime,'_port_open',return_value=False), \
             patch.object(runtime,'check_local_model',return_value='bonsai'), \
             patch.object(runtime.subprocess,'Popen') as spawn, \
             patch.dict(os.environ,{'SEDO_AI_API_KEY':'secret','DJANGO_SECRET_KEY':'secret','BONSAI_HOST':'0.0.0.0'}):
            runtime._start(self.cfg)
        args,kw=spawn.call_args
        self.assertIn(str(self.root/'scripts'/('start_llama_server.ps1' if os.name=='nt' else 'start_llama_server.sh')),args[0])
        self.assertFalse(kw.get('shell',False))
        self.assertEqual(kw['env']['BONSAI_HOST'],'127.0.0.1')
        self.assertEqual(kw['env']['PORT'],'8080')
        self.assertNotIn('SEDO_AI_API_KEY',kw['env'])
        self.assertNotIn('DJANGO_SECRET_KEY',kw['env'])
        self.lock.close.assert_called_once()

    def test_loading_server_waits_without_second_process(self):
        with patch.object(runtime,'_port_open',return_value=True), \
             patch.object(runtime,'check_local_model',side_effect=[AIError('lädt'), 'bonsai']) as check, \
             patch.object(runtime.time,'sleep'), patch.object(runtime.subprocess,'Popen') as spawn:
            runtime._start(self.cfg)
        self.assertEqual(check.call_count,2)
        spawn.assert_not_called()

    def test_start_failure_does_not_kill_any_process(self):
        process=MagicMock();process.poll.return_value=1
        with patch.object(runtime,'_port_open',return_value=False), \
             patch.object(runtime,'check_local_model',side_effect=AIError('not ready')), \
             patch.object(runtime.subprocess,'Popen',return_value=process):
            with self.assertRaisesMessage(AIError,'konnte nicht gestartet'):
                runtime._start(self.cfg)
        process.kill.assert_not_called();process.terminate.assert_not_called()
        self.lock.close.assert_called_once()

    def test_occupied_unready_port_times_out_without_launch(self):
        with patch.object(runtime,'_port_open',return_value=True), patch.object(runtime.subprocess,'Popen') as spawn:
            with self.assertRaisesMessage(AIError,'noch nicht bereit'):
                runtime._start(self.cfg,wait_seconds=0)
        spawn.assert_not_called()

    def test_missing_installation_does_not_download_or_launch(self):
        with patch.object(runtime,'_port_open',return_value=False), patch.object(runtime.subprocess,'Popen') as spawn:
            with self.assertRaisesMessage(AIError,'Startskript nicht gefunden'):
                runtime._start({**self.cfg,'runtime_dir':str(self.root/'absent')})
        spawn.assert_not_called()

    def test_other_sedo_start_lock_prevents_duplicate(self):
        with patch.object(runtime,'acquire',side_effect=RuntimeError('locked')), \
             patch.object(runtime.subprocess,'Popen') as spawn:
            runtime._start(self.cfg)
        spawn.assert_not_called()
        self.assertIn('anderen SEDO-Sitzung',runtime.runtime_status())

    def test_cloud_address_cannot_start_runtime(self):
        with patch.object(runtime.subprocess,'Popen') as spawn:
            with self.assertRaises(AIError):
                runtime._start({**self.cfg,'base_url':'https://cloud.example/v1'})
        spawn.assert_not_called()

    def test_windows_launcher_preserves_execution_policy(self):
        command=runtime._launch_command(self.root,windows=True)
        self.assertIn('-File',command)
        self.assertNotIn('-ExecutionPolicy',command)
        self.assertTrue(command[-1].endswith('start_llama_server.ps1'))

    def test_start_options_disabled_and_concurrent(self):
        with patch.object(runtime,'get_ai_config',return_value={**CFG,'autostart':False}), \
             patch.object(runtime.threading,'Thread') as thread:
            self.assertFalse(runtime.start_bonsai_background())
        thread.assert_not_called()
        with patch.object(runtime,'get_ai_config',return_value={**CFG,'enabled':False}), \
             patch.object(runtime.threading,'Thread') as thread:
            self.assertFalse(runtime.start_bonsai_background(manual=True))
        thread.assert_not_called()
        worker=MagicMock();worker.is_alive.return_value=True
        with patch.object(runtime,'get_ai_config',return_value=CFG),patch.object(runtime,'_worker',worker), \
             patch.object(runtime.threading,'Thread') as thread:
            self.assertFalse(runtime.start_bonsai_background())
        thread.assert_not_called()

    def test_background_thread_handles_error_without_breaking_app(self):
        with patch.object(runtime,'get_ai_config',return_value=CFG),patch.object(runtime,'_worker',None), \
             patch.object(runtime.threading,'Thread') as thread:
            self.assertTrue(runtime.start_bonsai_background())
            task=thread.call_args.kwargs['target']
            self.assertTrue(thread.call_args.kwargs['daemon'])
            thread.return_value.start.assert_called_once()
        with patch.object(runtime,'_start',side_effect=AIError('Test-Startfehler')):
            task()
        self.assertEqual(runtime.runtime_status(),'Test-Startfehler')

    def test_runtime_dir_requires_absolute_path(self):
        self.assertEqual(runtime.validate_runtime_dir(''),'')
        self.assertEqual(runtime.validate_runtime_dir(str(self.root)),str(self.root))
        for value in ['relative/folder', str(self.root)+'\nx']:
            with self.assertRaises(AIError):runtime.validate_runtime_dir(value)

    def test_configuration_failure_does_not_prevent_desktop_start(self):
        with patch.object(runtime,'get_ai_config',side_effect=RuntimeError('database')), \
             patch.object(runtime.threading,'Thread') as thread:
            self.assertFalse(runtime.start_bonsai_background())
        thread.assert_not_called()
        self.assertIn('ohne KI bedienbar',runtime.runtime_status())


class StartupSettingsTests(TestCase):
    def test_autostart_defaults_and_admin_controls(self):
        from sedo.services.ai_local import get_ai_config
        self.assertTrue(get_ai_config()['autostart'])
        user=get_user_model().objects.create_user('autostart-test',is_staff=True)
        self.client.force_login(user)
        response=self.client.post(reverse('sedo:settings'),{'action':'save_ai','ai_local_enabled':'on',
                'ai_local_autostart':'on','ai_local_base_url':CFG['base_url'],'ai_local_runtime_dir':''},follow=True)
        self.assertContains(response,'Bonsai beim Start der SEDO-App automatisch mitstarten')
        self.assertEqual(AppSetting.get('ai_local_autostart'),'1')
        with patch('sedo.views.start_bonsai_background',return_value=True) as start:
            response=self.client.post(reverse('sedo:settings'),{'action':'start_ai'},follow=True)
        start.assert_called_once_with(manual=True)
        self.assertContains(response,'Bonsai wird im Hintergrund gestartet')
        user.is_staff=False;user.save()
        with patch('sedo.views.start_bonsai_background') as start:
            self.assertEqual(self.client.post(reverse('sedo:settings'),{'action':'start_ai'}).status_code,403)
        start.assert_not_called()

