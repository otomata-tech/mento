"""mento command line."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__, control, library
from .config import KEYS, SECRET_KEYS, Config, state_dir
from .devices import DeviceError
from .media import MediaError
from .push import PushError
from .session import Session
from .transcribe import ENGINES, TranscribeError
from .trim import TrimError

REC_HELP = "recording: id (folder name), a unique prefix or part of it, or 'last' (default)"
DATES = "Dates: YYYY-MM-DD or Nd (7d = last 7 days). Times: SS, MM:SS or H:MM:SS."

ERRORS = (library.UsageError, control.ControlError, TranscribeError, TrimError, PushError,
          MediaError, DeviceError)


def fmt(seconds):
    return library.fmt_duration(seconds)


# ── Recording ────────────────────────────────────────────────────────────────

def cmd_start(cfg, session, args):
    folder, state = control.start(session, cfg.recordings_dir)
    print(f"● Recording  {folder.name}")
    if not state.get("system_audio"):
        print("! No system audio device: only the mic is recorded "
              "(macOS: install BlackHole or Background Music)")


def cmd_pause(cfg, session, args):
    control.pause(session)
    print(f"❚❚ Paused ({fmt(session.elapsed())} recorded)")


def cmd_resume(cfg, session, args):
    control.resume(session)
    print("● Recording (resumed)")


def cmd_stop(cfg, session, args):
    folder, seconds = control.stop(session)
    print(f"■ Stopped ({fmt(seconds)})  {folder / 'audio.mp3'}")
    if args.trim:
        from .trim import trim
        trim(folder)


def cmd_status(cfg, session, args):
    st = session.read()
    status = session.status()
    data = {"state": status}
    if st:
        data.update(id=Path(st["folder"]).name, path=st["folder"], elapsed_s=session.elapsed(st))
        for key in ("warning", "error"):
            if st.get(key):
                data[key] = st[key]
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return
    labels = {"idle": "■ Not recording", "recording": "● Recording", "paused": "❚❚ Paused",
              "stopping": "… Encoding", "starting": "… Starting",
              "interrupted": "✗ Interrupted — the recorder died, run 'mento stop'",
              "failed": "✗ Failed — run 'mento stop'"}
    line = labels[status]
    if st:
        line += f": {data['id']} ({fmt(data['elapsed_s'])})"
    print(line)
    for key in ("warning", "error"):
        if key in data:
            print(f"  ! {data[key]}")


def cmd_tray(cfg, session, args):
    from .tray import main as tray_main
    return tray_main()


# ── Transcription ────────────────────────────────────────────────────────────

def cmd_transcribe(cfg, session, args):
    from .transcribe import transcribe
    target = args.rec
    kwargs = {}
    if target and Path(target).is_file():
        kwargs["file"] = Path(target)
    else:
        kwargs["folder"] = library.resolve(cfg.recordings_dir, target)
    path = transcribe(cfg, engine=args.engine, prompt=args.prompt or "", speakers=args.speakers or "",
                      language=args.language, model=args.model, **kwargs)
    print(f"✓ Transcript: {path}")


def cmd_trim(cfg, session, args):
    from .trim import trim
    folder = library.resolve(cfg.recordings_dir, args.rec)

    def confirm():
        if args.yes:
            return True
        if not sys.stdin.isatty():
            raise TrimError("use --yes to trim non-interactively")
        return not input("Cut here? [Y/n] ").strip().lower().startswith("n")

    cut = trim(folder, confirm=confirm)
    if cut is not None:
        print(f"✓ Trimmed at {fmt(cut)} (original kept as audio_full.mp3)")


# ── Library ──────────────────────────────────────────────────────────────────

def cmd_library(name):
    def run(cfg, session, args):
        if name in ("show", "cat", "rename") and getattr(args, "ref", None) is None:
            args.ref = "last"
        return getattr(library, f"do_{name}")(cfg.recordings_dir, args)
    return run


def cmd_path(cfg, session, args):
    print(library.resolve(cfg.recordings_dir, args.rec))


def cmd_rename(cfg, session, args):
    folder = library.resolve(cfg.recordings_dir, args.ref)
    st = session.read()
    if st and Path(st["folder"]) == folder:
        raise control.ControlError("cannot rename the recording in progress — stop it first")
    return library.do_rename(cfg.recordings_dir, args)


def cmd_open(cfg, session, args):
    folder = library.resolve(cfg.recordings_dir, args.rec)
    open_path(folder)


def open_path(path):
    if sys.platform == "win32":
        import os
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def cmd_push(cfg, session, args):
    from .push import push
    folder = library.resolve(cfg.recordings_dir, args.rec)
    rid = push(folder, cfg.get("MENTO_API_TOKEN"), base_url=cfg.get("MENTO_API_URL"),
               with_audio=args.with_audio)
    print(f"✓ Pushed to mento.cc: recording {rid}")


# ── Setup & install ──────────────────────────────────────────────────────────

def cmd_setup(cfg, session, args):
    print("mento setup — Enter keeps the current value\n")
    for key, label in KEYS.items():
        current = cfg.get(key)
        shown = current
        if key in SECRET_KEYS and len(current) > 14:
            shown = f"{current[:6]}…{current[-4:]}"
        value = input(f"{label} [{shown}]: ").strip()
        if value:
            cfg.values[key] = value
    print(f"\n✓ Saved to {cfg.save()}")


def cmd_install(cfg, session, args):
    from .install import install, uninstall
    (uninstall if args.uninstall else install)(print)


# ── Parser ───────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog="mento",
        description="Record meetings (mic left, system audio right) and transcribe them.",
        epilog=f"{DATES}\nDetails and examples: mento <command> --help",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name, func, help, description=None, epilog=None, aliases=()):
        p = sub.add_parser(name, help=help, description=description or help, epilog=epilog,
                           aliases=list(aliases), formatter_class=argparse.RawDescriptionHelpFormatter)
        p.set_defaults(func=func)
        return p

    def rec_arg(p, name="rec"):
        p.add_argument(name, nargs="?", help=REC_HELP)

    add("start", cmd_start, "start recording",
        "Start recording in the background: mic on the left channel, system audio on the right.")
    add("pause", cmd_pause, "pause the recording (devices released)")
    add("resume", cmd_resume, "resume a paused recording")
    p = add("stop", cmd_stop, "stop and encode audio.mp3",
            "Stop and encode audio.mp3. Does not transcribe: run 'mento transcribe' with\n"
            "the meeting's vocabulary and participants.")
    p.add_argument("--trim", action="store_true",
                   help="cut the trailing audio after the conversation (original kept)")
    p = add("status", cmd_status, "recording state and duration (pauses excluded)")
    p.add_argument("--json", action="store_true", help='{"state", "id", "path", "elapsed_s", "warning"}')
    add("tray", cmd_tray, "panel icon: start, pause, stop, recent recordings")

    p = add("transcribe", cmd_transcribe, "transcribe a recording or a media file",
            "Transcribe with speaker diarization into transcript.txt ([mm:ss] SPEAKER: text).\n"
            "Dual-track recordings pin LOCAL_SPEAKER to the mic. Settings are kept in meta.json.\n"
            "WhisperX runs locally (minutes of GPU: run it in background); Voxtral uses the Mistral API.",
            epilog="Example:\n  mento transcribe last --prompt \"Acme, DSN, Marie Dupont\" --speakers 3")
    p.add_argument("rec", nargs="?", help=f"{REC_HELP}, or a media file path")
    p.add_argument("--engine", choices=ENGINES, help="default: TRANSCRIBE_ENGINE (whisperx)")
    p.add_argument("--prompt", help="vocabulary (names, products, acronyms), added to WHISPER_INITIAL_PROMPT; "
                                    "30-50 terms max")
    p.add_argument("--speakers", help="participants, local speaker included: N or MIN-MAX (WhisperX)")
    p.add_argument("--language", help="force the language (default: WHISPER_LANGUAGE)")
    p.add_argument("--model", help="model (default: WHISPER_MODEL or VOXTRAL_MODEL)")

    p = add("trim", cmd_trim, "cut the trailing audio after the conversation",
            "Find the end of the conversation (first long silence after 5 min) and cut there;\n"
            "the original is kept as audio_full.mp3.", aliases=("cut",))
    rec_arg(p)
    p.add_argument("-y", "--yes", action="store_true", help="no confirmation")

    p = add("list", cmd_library("list"), "list recordings, newest first", aliases=("ls",), epilog=DATES)
    p.add_argument("--since", help="YYYY-MM-DD or Nd")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")

    p = add("show", cmd_library("show"), "details, transcription settings, talk time per speaker")
    rec_arg(p, "ref")
    p.add_argument("--json", action="store_true")

    p = add("cat", cmd_library("cat"), "print a transcript",
            epilog=f"{DATES}\nExample:\n  mento cat last --speaker SPEAKER_01 --from 10:00 --to 25:00")
    rec_arg(p, "ref")
    p.add_argument("--speaker")
    p.add_argument("--from", dest="from_", metavar="T")
    p.add_argument("--to", metavar="T")
    p.add_argument("--json", action="store_true")

    p = add("search", cmd_library("search"), "search every transcript",
            "Search every transcript, case and accents ignored. One hit per line:\n"
            "<id> [mm:ss] SPEAKER: text. Exits 1 when nothing matches.",
            epilog=f"{DATES}\nExample:\n  mento search \"budget\" --since 30d -C 2")
    p.add_argument("query")
    p.add_argument("--since")
    p.add_argument("--speaker")
    p.add_argument("-C", "--context", type=int, default=0, help="lines of context (hits marked >)")
    p.add_argument("--json", action="store_true")

    p = add("path", cmd_path, "print the recording's folder")
    rec_arg(p)
    p = add("rename", cmd_rename, "rename to <date>_NAME (a-z 0-9 _, 50 chars max)",
            epilog="Example:\n  mento rename last point_client_onboarding")
    p.add_argument("ref", help=REC_HELP)
    p.add_argument("slug", metavar="NAME")
    p = add("open", cmd_open, "open the recording's folder in the file manager")
    rec_arg(p)
    p = add("push", cmd_push, "store the recording on mento.cc (sharing, agent access)",
            "Create or update the recording on mento.cc (idempotent): metadata and transcript,\n"
            "and the mp3 with --with-audio. Summaries are written by the agent (MCP or oto).")
    rec_arg(p)
    p.add_argument("--with-audio", action="store_true", help="include audio.mp3")

    add("setup", cmd_setup, "configure tokens, recordings dir, speaker name, engine")
    p = add("install", cmd_install, "panel icon at login, app menu entry, Claude Code plugin",
            "Desktop integration (autostart of the panel icon, app menu entry) and the\n"
            "mento plugin in every Claude Code config dir found.")
    p.add_argument("--uninstall", action="store_true", help="remove all of it")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    cfg = Config.load()
    session = Session(state_dir())
    try:
        return args.func(cfg, session, args) or 0
    except ERRORS as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
