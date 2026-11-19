#!/usr/bin/env python3
"""Read side of the recordings library, plus the helpers the shell relies on.

A recording is a folder of the recordings dir holding audio.mp3, the parts
of a recording in progress (audio.partNN.mp3) or transcript.txt. Its id is
the folder name. meta.json keeps what the shell learned about it: start
time, duration, transcription settings.

Usage: recordings.py --root DIR <command> ...
  resolve [REF]                 absolute path of a recording (default: last)
  list [--since D] [--limit N] [--json]
  show REF [--json]
  cat REF [--speaker S] [--from T] [--to T] [--json]
  search QUERY [--since D] [--speaker S] [-C N] [--json]
  rename REF SLUG               keeps the date prefix, prints the new path
  meta-set FOLDER key=str key:=json ...
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

LINE_RE = re.compile(r"^\[(\d+):(\d{2})\] (.+?): (.*)$")
DATE_RE = re.compile(r"^(?:recording_)?(\d{8})(?:_(\d{6}))?")
SLUG_RE = re.compile(r"^[a-z0-9_]{1,50}$")
PART_GLOB = "audio.part*.mp3"


class UsageError(Exception):
    pass


# ── Recordings ───────────────────────────────────────────────────────────────

def is_recording(folder):
    return folder.is_dir() and (
        (folder / "audio.mp3").exists()
        or (folder / "transcript.txt").exists()
        or any(folder.glob(PART_GLOB))
    )


def all_recordings(root):
    recs = [p for p in root.iterdir() if is_recording(p)]
    return sorted(recs, key=started_at, reverse=True)


def read_meta(folder):
    path = folder / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def write_meta(folder, meta):
    path = folder / "meta.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def started_at(folder):
    meta = read_meta(folder)
    if "started_at" in meta:
        return dt.datetime.fromisoformat(meta["started_at"]).astimezone().replace(tzinfo=None)
    m = DATE_RE.match(folder.name)
    if m:
        return dt.datetime.strptime(m.group(1) + (m.group(2) or "000000"), "%Y%m%d%H%M%S")
    return dt.datetime.fromtimestamp(folder.stat().st_mtime)


def date_label(folder):
    """ISO date, with the time when it is known (not in renamed folders)."""
    when = started_at(folder)
    m = DATE_RE.match(folder.name)
    if "started_at" not in read_meta(folder) and not (m and m.group(2)):
        return when.date().isoformat()
    return when.isoformat(timespec="minutes")


def duration(folder):
    """Seconds of audio.mp3, cached in meta.json; None while still recording."""
    meta = read_meta(folder)
    if "duration_s" in meta:
        return meta["duration_s"]
    audio = folder / "audio.mp3"
    if not audio.exists():
        return None
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    meta["duration_s"] = round(float(out))
    write_meta(folder, meta)
    return meta["duration_s"]


def state(folder):
    if (folder / "transcript.txt").exists():
        return "transcribed"
    if (folder / "audio.mp3").exists():
        return "audio"
    return "in_progress"


def resolve(root, ref):
    """A recording from its path, id, unique id prefix or unique substring."""
    recs = all_recordings(root)
    if not ref or ref == "last":
        if not recs:
            raise UsageError("no recordings yet")
        return recs[0]
    path = Path(ref)
    if path.is_absolute():
        if not is_recording(path):
            raise UsageError(f"not a recording: {ref}")
        return path
    for match in (lambda r: r.name == ref,
                  lambda r: r.name.startswith(ref),
                  lambda r: ref in r.name):
        found = [r for r in recs if match(r)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = "\n  ".join(r.name for r in found)
            raise UsageError(f"'{ref}' matches several recordings:\n  {names}")
    raise UsageError(f"no recording matches '{ref}'")


# ── Transcript ───────────────────────────────────────────────────────────────

def read_lines(folder):
    path = folder / "transcript.txt"
    if not path.exists():
        return []
    lines = []
    for raw in path.read_text().splitlines():
        m = LINE_RE.match(raw)
        if m:
            t = int(m.group(1)) * 60 + int(m.group(2))
            lines.append({"t": t, "time": fmt_time(t),
                          "speaker": m.group(3), "text": m.group(4)})
    return lines


def speaker_stats(lines):
    """Lines and approximate talk time (until the next line) per speaker."""
    stats = {}
    for i, line in enumerate(lines):
        s = stats.setdefault(line["speaker"], {"name": line["speaker"], "lines": 0, "seconds": 0})
        s["lines"] += 1
        if i + 1 < len(lines):
            s["seconds"] += lines[i + 1]["t"] - line["t"]
    return sorted(stats.values(), key=lambda s: -s["seconds"])


def fold(text):
    """Lowercase without accents, so 'reunion' finds 'Réunion'."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold()


# ── Formatting ───────────────────────────────────────────────────────────────

def fmt_time(seconds):
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_duration(seconds):
    if seconds is None:
        return "-"
    h, rest = divmod(int(seconds), 3600)
    return f"{h}h{rest // 60:02d}" if h else f"{rest // 60}m{rest % 60:02d}"


def parse_time(value):
    """'75', '1:15' or '1:01:15' → seconds."""
    parts = value.split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise UsageError(f"invalid time: {value} (expected SS, MM:SS or H:MM:SS)")
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def parse_since(value):
    """'2026-11-01' or '7d' → datetime."""
    if re.fullmatch(r"\d+d", value):
        return dt.datetime.now() - dt.timedelta(days=int(value[:-1]))
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        raise UsageError(f"invalid date: {value} (expected YYYY-MM-DD or Nd)") from None


def summary(folder):
    lines = read_lines(folder)
    return {
        "id": folder.name,
        "path": str(folder),
        "date": date_label(folder),
        "duration_s": duration(folder),
        "state": state(folder),
        "trimmed": (folder / "audio_full.mp3").exists(),
        "speakers": len({line["speaker"] for line in lines}),
    }


def emit_json(data):
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_resolve(root, args):
    print(resolve(root, args.ref))


def cmd_list(root, args):
    recs = all_recordings(root)
    if args.since:
        since = parse_since(args.since)
        recs = [r for r in recs if started_at(r) >= since]
    if args.limit:
        recs = recs[:args.limit]
    rows = [summary(r) for r in recs]
    if args.json:
        return emit_json(rows)
    if not rows:
        print("No recordings")
        return
    width = max(len(r["id"]) for r in rows)
    for r in rows:
        speakers = f"{r['speakers']} speakers" if r["speakers"] else ""
        trimmed = " trimmed" if r["trimmed"] else ""
        print(f"{r['id']:<{width}}  {r['date'].replace('T', ' '):<16}  "
              f"{fmt_duration(r['duration_s']):>6}  {r['state']:<11}  {speakers}{trimmed}".rstrip())


def cmd_show(root, args):
    folder = resolve(root, args.ref)
    info = summary(folder)
    meta = read_meta(folder)
    info["files"] = sorted(p.name for p in folder.iterdir() if p.is_file())
    info["transcription"] = meta.get("transcription")
    info["speakers"] = speaker_stats(read_lines(folder))
    if args.json:
        return emit_json(info)
    print(f"{info['id']}\n  path      {info['path']}\n  date      {info['date'].replace('T', ' ')}")
    print(f"  duration  {fmt_duration(info['duration_s'])}{' (trimmed)' if info['trimmed'] else ''}")
    print(f"  state     {info['state']}")
    print(f"  files     {', '.join(info['files'])}")
    tr = info["transcription"]
    if tr:
        print("  transcription")
        for key in ("transcribed_at", "model", "language", "speakers", "prompt", "dual_track"):
            value = tr.get(key)
            if value not in (None, ""):
                if isinstance(value, bool):
                    value = "yes" if value else "no"
                print(f"    {key:<14} {value}")
    if info["speakers"]:
        print("  speakers (lines, ≈ talk time)")
        for s in info["speakers"]:
            print(f"    {s['name']:<14} {s['lines']:>4}  {fmt_duration(s['seconds'])}")


def cmd_cat(root, args):
    folder = resolve(root, args.ref)
    if not (folder / "transcript.txt").exists():
        raise UsageError(f"not transcribed yet: {folder.name}")
    lines = read_lines(folder)
    if args.speaker:
        lines = [x for x in lines if fold(x["speaker"]) == fold(args.speaker)]
    if args.from_:
        start = parse_time(args.from_)
        lines = [x for x in lines if x["t"] >= start]
    if args.to:
        end = parse_time(args.to)
        lines = [x for x in lines if x["t"] <= end]
    if args.json:
        return emit_json(lines)
    for x in lines:
        print(f"[{x['time']}] {x['speaker']}: {x['text']}")


def cmd_search(root, args):
    query = fold(args.query)
    recs = all_recordings(root)
    if args.since:
        since = parse_since(args.since)
        recs = [r for r in recs if started_at(r) >= since]
    hits = []
    for folder in recs:
        lines = read_lines(folder)
        shown = set()
        for i, line in enumerate(lines):
            if query not in fold(line["text"]):
                continue
            if args.speaker and fold(line["speaker"]) != fold(args.speaker):
                continue
            if args.json:
                hits.append({"id": folder.name, **line})
                continue
            lo, hi = max(0, i - args.context), min(len(lines), i + args.context + 1)
            if args.context and shown and min(shown) < lo and lo > max(shown) + 1:
                print("--")
            for j in range(lo, hi):
                if j in shown:
                    continue
                shown.add(j)
                x = lines[j]
                mark = ">" if j == i else " "
                prefix = f"{mark} " if args.context else ""
                print(f"{prefix}{folder.name} [{x['time']}] {x['speaker']}: {x['text']}")
            hits.append(None)
    if args.json:
        return emit_json(hits)
    if not hits:
        print(f"No match for '{args.query}'", file=sys.stderr)
        return 1


def cmd_rename(root, args):
    folder = resolve(root, args.ref)
    if not SLUG_RE.match(args.slug):
        raise UsageError(f"invalid name: {args.slug} (only a-z 0-9 _, 50 chars max)")
    target = root / f"{started_at(folder):%Y%m%d}_{args.slug}"
    if target == folder:
        print(folder)
        return
    if target.exists():
        raise UsageError(f"already exists: {target.name}")
    folder.rename(target)
    print(target)


def cmd_meta_set(root, args):
    folder = Path(args.folder)
    if not folder.is_dir():
        raise UsageError(f"not a folder: {folder}")
    meta = read_meta(folder)
    for pair in args.pairs:
        if ":=" in pair:
            key, raw = pair.split(":=", 1)
            value = json.loads(raw)
        elif "=" in pair:
            key, value = pair.split("=", 1)
        else:
            raise UsageError(f"expected key=value or key:=json, got: {pair}")
        target = meta
        *parents, leaf = key.split(".")
        for p in parents:
            target = target.setdefault(p, {})
        target[leaf] = value
    write_meta(folder, meta)


def main():
    parser = argparse.ArgumentParser(prog="recordings.py")
    parser.add_argument("--root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resolve")
    p.add_argument("ref", nargs="?")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("list")
    p.add_argument("--since")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show")
    p.add_argument("ref", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("cat")
    p.add_argument("ref", nargs="?")
    p.add_argument("--speaker")
    p.add_argument("--from", dest="from_")
    p.add_argument("--to")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_cat)

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--since")
    p.add_argument("--speaker")
    p.add_argument("-C", "--context", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("rename")
    p.add_argument("ref")
    p.add_argument("slug")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("meta-set")
    p.add_argument("folder")
    p.add_argument("pairs", nargs="+")
    p.set_defaults(func=cmd_meta_set)

    args = parser.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"✗ recordings dir not found: {root}", file=sys.stderr)
        return 1
    try:
        return args.func(root, args) or 0
    except UsageError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
