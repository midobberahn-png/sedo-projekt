"""Local development gate: isolated source, both Django series, native host build.

No GitHub runner, deployment, real database, mail sync or model startup. Reports
identify the tested commit and host; they are not a cross-platform release seal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import venv

import teststand

APP = Path(__file__).resolve().parents[1]
PROBES = ('entwurf', 'belegketten', 'indizes', 'belegarchiv', 'mahnwaechter',
          'abo_zahlungsziel', 'bedienrisiken', 'teststand', 'mail', 'incoming', 'spam', 'calendar')


def isolated_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('SEDO_', 'DJANGO_', 'PYTHON'))}
    env.update(SEDO_HOME=str(home), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               DJANGO_SECRET_KEY='ausschliesslich-isolierte-lokale-tests-kein-betriebsschluessel',
               DJANGO_ALLOWED_HOSTS='testserver,127.0.0.1,localhost')
    return env


def revision(app: Path) -> tuple[str, str]:
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=app, text=True).strip()
    if git('status', '--porcelain', '--untracked-files=no'):
        raise teststand.BuildError('Bitte Änderungen erst committen. Geprüft wird ein sauberer Git-Stand.')
    return git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')


def verify_revision(app: Path, expected: tuple[str, str]):
    if revision(app) != expected:
        raise teststand.BuildError('Git-Stand hat sich während der Prüfung geändert. Erneut prüfen.')


def validate_interpreter(info: dict, series: str):
    if info['python'] != list(teststand.PYTHON) or not info['django'].startswith(series + '.'):
        raise teststand.BuildError(f'Testumgebung benötigt Python 3.13.15 und Django {series}.x.')


class Evidence:
    def __init__(self, output: Path, commit: str, tree: str):
        output.mkdir(parents=True, exist_ok=False)
        self.output = output
        self.data = {
            'schema': 1, 'scope': 'local-development', 'status': 'running',
            'development_ready': False, 'commit': commit, 'tree': tree,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'host': {'os': platform.system(), 'machine': platform.machine(),
                     'python': platform.python_version()},
            'not_tested': [s for s in ('Darwin', 'Windows', 'Linux') if s != platform.system()],
            'steps': [],
        }
        self.save()

    def save(self):
        data = json.dumps(self.data, ensure_ascii=False, indent=2) + '\n'
        temporary = self.output / 'report.json.tmp'
        temporary.write_text(data, encoding='utf-8')
        temporary.replace(self.output / 'report.json')
        lines = [f"# Lokale Entwicklungsprüfung: {self.data['status']}", '',
                 f"Commit: `{self.data['commit']}`", f"Git-Baum: `{self.data['tree']}`", '',
                 f"Rechner: {self.data['host']['os']} / {self.data['host']['machine']}",
                 'Nicht auf diesem Stand geprüft: ' + ', '.join(self.data['not_tested']), '',
                 'Dieser Bericht ersetzt keine Abnahme auf einem anderen Betriebssystem.', '',
                 '| Prüfung | Ergebnis | Protokoll |', '|---|---|---|']
        for step in self.data['steps']:
            lines.append(f"| {step['name']} | {step['status']} | [{step['log']}]({step['log']}) |")
        if self.data.get('error'):
            lines.extend(['', self.data['error']])
        (self.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def run(self, name: str, command: list[str], cwd: Path, env: dict, timeout=1200):
        step = {'name': name, 'status': 'running', 'log': name + '.log', 'command': command}
        self.data['steps'].append(step)
        self.save()
        print('START', name, flush=True)
        started = time.monotonic()
        try:
            with (self.output / step['log']).open('w', encoding='utf-8') as log:
                subprocess.run(command, cwd=cwd, env=env, stdout=log,
                               stderr=subprocess.STDOUT, timeout=timeout, check=True)
            step['status'] = 'passed'
        except (OSError, subprocess.SubprocessError):
            step['status'] = 'failed'
            raise
        finally:
            step['seconds'] = round(time.monotonic() - started, 2)
            self.save()
        print('PASS ', name, flush=True)

    def finish(self, error: str | None = None):
        # A process interrupted mid-run can never leave a green report behind.
        passed = not error and bool(self.data['steps']) and all(
            s['status'] == 'passed' for s in self.data['steps'])
        self.data.update(status='passed' if passed else 'failed', development_ready=passed,
                         finished_at=datetime.now(timezone.utc).isoformat())
        if error:
            self.data['error'] = error
        self.save()


def regression(evidence: Evidence, source: Path, temp: Path, series: str, supplied: Path | None):
    prefix = 'django-' + series
    home = temp / (prefix + '-home')
    (home / 'data').mkdir(parents=True)
    env = isolated_env(home)
    if supplied:
        python = supplied.absolute()
    else:
        target = temp / (prefix + '-venv')
        venv.EnvBuilder(with_pip=True).create(target)
        python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        requirements = temp / 'regression-requirements.txt'
        requirements.write_text('\n'.join(line for line in (source / 'requirements.txt').read_text(
            encoding='utf-8').splitlines() if not line.strip().startswith('faster-whisper')) + '\n',
            encoding='utf-8')
        evidence.run(prefix + '-install', [str(python), '-m', 'pip', 'install',
                     f'Django~={series}.0', '-r', str(requirements)], source, env)
        evidence.run(prefix + '-dependencies', [str(python), '-m', 'pip', 'check'], source, env)
    inspect = ('import sys,json,django,importlib.metadata as m; '
               'print(json.dumps({"python":list(sys.version_info[:3]),"django":django.get_version(),'
               '"packages":sorted((d.metadata["Name"],d.version) for d in m.distributions())}))')
    evidence.run(prefix + '-environment', [str(python), '-I', '-c', inspect], source, env)
    info = json.loads((evidence.output / (prefix + '-environment.log')).read_text(encoding='utf-8'))
    validate_interpreter(info, series)
    evidence.run(prefix + '-tests', [str(python), 'manage.py', 'test', 'sedo', '--noinput'], source, env)
    for name in PROBES:
        output = evidence.output / (prefix + '-gegenproben-' + name)
        evidence.run(output.name, [str(python), 'tools/gegenproben_' + name + '.py',
                     '--output', str(output)], source, env)
    for name, script in [('sqlite', 'probe_mahnwaechter_sqlite'), ('index-50000', 'benchmark_invoice_indexes')]:
        evidence.run(prefix + '-' + name, [str(python), 'tools/' + script + '.py', '--output',
                     str(evidence.output / (prefix + '-' + name + '.json'))], source, env)
    evidence.run(prefix + '-migrationen', [str(python), 'manage.py', 'makemigrations',
                 '--check', '--dry-run'], source, env)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Neuer Ergebnisordner; Standard .local-checks/Zeit-Commit')
    parser.add_argument('--python52', type=Path, help='Optional: vorhandene Python-3.13.15/Django-5.2-Umgebung')
    parser.add_argument('--python60', type=Path, help='Optional: vorhandene Python-3.13.15/Django-6.0-Umgebung')
    args = parser.parse_args(argv)
    evidence = None
    try:
        if sys.version_info[:3] != teststand.PYTHON:
            raise teststand.BuildError('Bitte mit Python 3.13.15 starten.')
        identity = revision(APP)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        output = (args.output or APP / '.local-checks' / (stamp + '-' + identity[0][:12])).resolve()
        evidence = Evidence(output, *identity)
        with tempfile.TemporaryDirectory(prefix='sedo-local-checks-') as work:
            temp = Path(work)
            home = temp / 'home'
            (home / 'data').mkdir(parents=True)
            env = isolated_env(home)
            for name in ('source-a', 'source-b'):
                evidence.run(name, [sys.executable, 'tools/teststand.py', 'source', '--output',
                             str(output / name)], APP, env)
            first, = (output / 'source-a').glob('*.zip')
            second, = (output / 'source-b').glob('*.zip')
            if first.read_bytes() != second.read_bytes():
                raise teststand.BuildError('Quellpakete sind nicht byteidentisch.')
            manifest = teststand.stage_source(first, temp / 'source')
            if (manifest['commit'], manifest['tree']) != identity:
                raise teststand.BuildError('Quellpaket passt nicht zum gestarteten Git-Stand.')
            evidence.data['source_archive_sha256'] = teststand.sha(first.read_bytes())
            for series, supplied in [('5.2', args.python52), ('6.0', args.python60)]:
                regression(evidence, temp / 'source', temp, series, supplied)
            verify_revision(APP, identity)
            if platform.system() in ('Darwin', 'Windows'):
                evidence.run('native-build', [sys.executable, 'tools/teststand.py', 'build',
                             '--output', str(output / 'native')], APP, env, timeout=2400)
                native = json.loads((output / 'native/BUILD_MANIFEST.json').read_text(encoding='utf-8'))
                if (native['commit'], native['tree']) != identity or not native['self_check']['ok']:
                    raise teststand.BuildError('Nativer Build passt nicht zum geprüften Stand.')
            else:
                target = temp / 'release-venv'
                evidence.run('release-install', [sys.executable, 'tools/teststand.py', 'install',
                             '--output', str(target)], APP, env)
                python = str(target / 'bin/python')
                evidence.run('release-tests', [python, 'manage.py', 'test', 'sedo', '--noinput'], temp / 'source', env)
                env['SEDO_CHECK_REPORT'] = str(output / 'source-check.json')
                evidence.run('source-check', [python, 'tools/desktop_entry.py', '--check-strict'], temp / 'source', env)
            verify_revision(APP, identity)
        evidence.finish()
        print('Lokale Entwicklungsprüfung erfolgreich:', output / 'report.md', flush=True)
        print('Andere Betriebssysteme bleiben ungeprüft:', ', '.join(evidence.data['not_tested']))
        return 0
    except (teststand.BuildError, OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        message = f'Lokale Prüfung abgebrochen: {exc}'
        if evidence is not None:
            evidence.finish(message)
        print(message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
