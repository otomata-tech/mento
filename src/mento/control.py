"""Start, pause, resume and stop the background recorder."""

import datetime as dt
import subprocess
import sys
from pathlib import Path

from .library import set_meta
from .recorder import finalize

START_TIMEOUT = 15
STOP_TIMEOUT = 1800  # encoding a long meeting takes a while


class ControlError(Exception):
    pass


def spawn_recorder(folder):
    """Launches the recorder detached from this terminal; its log stays in the folder."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    log = open(Path(folder) / "recorder.log", "ab")
    subprocess.Popen([sys.executable, "-m", "mento.recorder", str(folder)],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)


def start(session, recordings_dir, spawn=spawn_recorder):
    status = session.status()
    if status in ("failed", "interrupted"):
        raise ControlError(f"previous recording {status} — run 'mento stop' first")
    if status != "idle":
        raise ControlError(f"already {status}: {Path(session.read()['folder']).name}")

    now = dt.datetime.now().astimezone()
    folder = Path(recordings_dir) / f"recording_{now:%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True)
    set_meta(folder, {"started_at": now.isoformat(timespec="seconds")})
    session.clear()
    spawn(folder)

    status = session.wait_for(lambda s: s not in ("idle", "starting"), START_TIMEOUT)
    if status == "failed":
        error = session.read().get("error", "unknown error")
        session.clear()
        raise ControlError(f"recording failed to start: {error}")
    if status != "recording":
        raise ControlError(f"recorder did not start (status: {status}), see {folder / 'recorder.log'}")
    return folder, session.read()


def pause(session):
    if session.status() != "recording":
        raise ControlError(f"not recording ({session.status()})")
    session.send("pause")
    if session.wait_for(lambda s: s != "recording", 10) != "paused":
        raise ControlError("recorder did not pause")


def resume(session):
    if session.status() != "paused":
        raise ControlError(f"not paused ({session.status()})")
    session.send("resume")
    if session.wait_for(lambda s: s != "paused", 10) != "recording":
        raise ControlError("recorder did not resume")


def stop(session, out=print):
    """Stops and encodes; returns (folder, duration_s). Recovers a dead recorder too."""
    status = session.status()
    if status == "idle":
        raise ControlError("no recording in progress")
    folder = Path(session.read()["folder"])

    if status in ("recording", "paused", "stopping"):
        if status != "stopping":
            session.send("stop")
        out("Encoding…")
        status = session.wait_for(lambda s: s not in ("recording", "paused", "stopping"), STOP_TIMEOUT)
        if status == "failed":
            error = session.read().get("error")
            session.clear()
            raise ControlError(f"stop failed: {error} (tracks kept in {folder})")
        if status == "idle":
            from .library import duration
            return folder, duration(folder)

    # interrupted (recorder died) or failed while recording: encode what was captured
    out(f"Recorder is gone ({status}) — encoding what was captured")
    seconds = finalize(folder)
    session.clear()
    return folder, seconds
