"""Push a recording to mento.cc, the storage and sharing service.

API: https://mento.cc/api/v1, `Authorization: Bearer mento_pat_…` (MENTO_API_TOKEN),
errors as application/problem+json (RFC 9457).
1. PUT /recordings/by-ref/{folder name}  meta.json + title — idempotent, returns {id}
2. PUT /recordings/{id}/transcript      transcript.txt as text/plain
3. audio (optional): POST …/audio/upload {size_bytes, sha256} → a signed S3 URL
   (or already_present), PUT the mp3 there, then POST …/audio/complete.
   The mp3 goes straight to S3: Cloudflare caps request bodies at 100 MB.
Summaries are not pushed here: the agent writes them through the MCP or oto.
"""

import hashlib
import re
from pathlib import Path

import httpx

from .library import duration, read_meta, started_at

DEFAULT_URL = "https://mento.cc/api/v1"


class PushError(Exception):
    pass


def title_from(folder_name):
    """'20261118_point_client_onboarding' → 'Point client onboarding'."""
    name = re.sub(r"^(recording_)?\d{8}(_\d{6})?_?", "", folder_name).replace("_", " ").strip()
    return name[:1].upper() + name[1:] if name else folder_name


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(resp, what):
    if resp.is_success:
        return resp
    detail = resp.text[:300]
    if "json" in resp.headers.get("content-type", ""):
        problem = resp.json()
        detail = problem.get("detail") or problem.get("title") or detail
    if resp.status_code == 401:
        detail = "invalid token — create one on https://app.mento.cc/jetons and run 'mento setup'"
    raise PushError(f"{what} failed (HTTP {resp.status_code}): {detail}")


def push(folder, token, base_url=DEFAULT_URL, with_audio=False, client=None, out=print):
    """Creates or updates the recording on mento.cc; returns its id."""
    folder = Path(folder)
    if not token:
        raise PushError("MENTO_API_TOKEN not set: create a token on https://app.mento.cc/jetons, then 'mento setup'")
    transcript = folder / "transcript.txt"
    if not transcript.exists():
        raise PushError(f"no transcript: {transcript} — run 'mento transcribe' first")
    audio = folder / "audio.mp3"
    if with_audio and not audio.exists():
        raise PushError(f"no audio: {audio}")

    meta = read_meta(folder)
    body = {"title": title_from(folder.name),
            "started_at": meta.get("started_at") or started_at(folder).astimezone().isoformat(timespec="seconds"),
            "duration_s": duration(folder)}
    if meta.get("transcription"):
        body["transcription"] = meta["transcription"]

    client = client or httpx.Client(timeout=httpx.Timeout(30, read=600))
    auth = {"Authorization": f"Bearer {token}"}
    api = base_url.rstrip("/")

    rec = check(client.put(f"{api}/recordings/by-ref/{folder.name}", headers=auth, json=body),
                "recording").json()
    rid = rec["id"]
    check(client.put(f"{api}/recordings/{rid}/transcript", content=transcript.read_bytes(),
                     headers={**auth, "Content-Type": "text/plain; charset=utf-8"}), "transcript")

    if with_audio:
        size = audio.stat().st_size
        ticket = check(client.post(f"{api}/recordings/{rid}/audio/upload", headers=auth,
                                   json={"size_bytes": size, "sha256": sha256(audio)}),
                       "audio upload").json()
        if ticket.get("already_present"):
            out("Audio already stored")
        else:
            out(f"Uploading audio ({size / 1e6:.0f} MB)…")
            # In memory: a signed S3 PUT needs a Content-Length, not a chunked body
            check(client.request(ticket.get("method", "PUT"), ticket["upload_url"],
                                 headers=ticket.get("headers") or {}, content=audio.read_bytes()),
                  "audio transfer")
            check(client.post(f"{api}/recordings/{rid}/audio/complete", headers=auth), "audio completion")
    return rid
