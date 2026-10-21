"""Ein Datenordner wird höchstens von einem SEDO-Serverprozess geöffnet."""
from pathlib import Path
import os


def acquire(home):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    handle = (home / '.desktop.lock').open('a+b')
    try:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError('SEDO verwendet diesen Datenordner bereits. Bitte den laufenden Prozess prüfen.') from exc
    return handle
