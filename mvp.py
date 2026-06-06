#!/usr/bin/env python3
import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
DIRS = ["incoming", "source", "clips", "subtitles", "metadata", "approved", "failed", "tmp"]
DEFAULT_HASHTAGS = ["#shorts", "#viral", "#fyp"]
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
DEFAULT_YOUTUBE_CLIENT_SECRET = ROOT / "secrets" / "youtube_client_secret.json"
DEFAULT_YOUTUBE_TOKEN_FILE = ROOT / "secrets" / "youtube_token.json"
PENDING_APPROVALS_FILE = WORKSPACE / "metadata" / "pending-approvals.json"
PIPELINE_LOCK_FILE = WORKSPACE / "tmp" / "content-pipeline.lock"
PIPELINE_LOCK_ENV = "CONTENT_AUTOMATION_QUEUE_HELD"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def run(cmd):
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def tool_path(name):
    local = ROOT / ".venv" / "bin" / name
    if local.exists():
        return str(local)
    path = shutil.which(name)
    if path:
        return path
    return None


def ytdlp_base_cmd():
    ytdlp = tool_path("yt-dlp") or "yt-dlp"
    cmd = [ytdlp]
    node = tool_path("node")
    if node:
        cmd.extend(["--js-runtimes", f"node:{node}"])
    cookies_from_browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER")
    if cookies_from_browser:
        cmd.extend(["--cookies-from-browser", cookies_from_browser])
    cookies_file = os.environ.get("YTDLP_COOKIES_FILE")
    if cookies_file:
        cmd.extend(["--cookies", cookies_file])
    return cmd


def ensure_dirs():
    for name in DIRS:
        (WORKSPACE / name).mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def queued_pipeline(label):
    """Serialize RAM-heavy clip jobs so Whisper/ffmpeg do not pile up."""
    ensure_dirs()
    if os.environ.get(PIPELINE_LOCK_ENV):
        yield
        return

    with PIPELINE_LOCK_FILE.open("w") as lock:
        print(f"[queue] waiting for content pipeline slot: {label}", file=sys.stderr)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        os.environ[PIPELINE_LOCK_ENV] = "1"
        print(f"[queue] started: {label}", file=sys.stderr)
        try:
            yield
        finally:
            os.environ.pop(PIPELINE_LOCK_ENV, None)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            print(f"[queue] finished: {label}", file=sys.stderr)


def load_job(job_id):
    path = WORKSPACE / "metadata" / f"{job_id}.json"
    if not path.exists():
        raise SystemExit(f"Job not found: {job_id}")
    return json.loads(path.read_text())


def save_job(job):
    ensure_dirs()
    path = WORKSPACE / "metadata" / f"{job['job_id']}.json"
    path.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n")
    return path


def load_pending_approvals():
    ensure_dirs()
    if not PENDING_APPROVALS_FILE.exists():
        return []
    data = json.loads(PENDING_APPROVALS_FILE.read_text())
    return data if isinstance(data, list) else []


def save_pending_approvals(records):
    ensure_dirs()
    PENDING_APPROVALS_FILE.write_text(
        json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def find_clip(job, clip_index):
    clip = next((item for item in job.get("clips", []) if item.get("index") == clip_index), None)
    if not clip:
        raise SystemExit(f"Clip not found: {clip_index}")
    return clip


def upsert_pending_approval(record):
    records = load_pending_approvals()
    for idx, item in enumerate(records):
        if item.get("job_id") == record["job_id"] and item.get("clip") == record["clip"]:
            records[idx] = {**item, **record}
            save_pending_approvals(records)
            return records[idx]
    records.append(record)
    save_pending_approvals(records)
    return record


def latest_pending_approval(job_id=None, clip=None):
    records = [
        item for item in load_pending_approvals()
        if item.get("status") == "pending"
        and (job_id is None or item.get("job_id") == job_id)
        and (clip is None or item.get("clip") == clip)
    ]
    if not records:
        raise SystemExit("No pending approval matched")
    return sorted(records, key=lambda item: item.get("registered_at", ""))[-1]


def update_pending_approval(job_id, clip, **fields):
    records = load_pending_approvals()
    for item in records:
        if item.get("job_id") == job_id and item.get("clip") == clip:
            item.update(fields)
            item["updated_at"] = now_iso()
            save_pending_approvals(records)
            return item
    raise SystemExit(f"Pending approval not found: {job_id} clip {clip}")


def parse_approval_action(text):
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return "unknown"
    if normalized in {"approve", "approved", "acc", "ok", "okay", "oke", "gas", "upload", "lanjut"}:
        return "approve"
    if normalized.startswith(("skip", "batal", "cancel", "tolak")):
        return "skip"
    if normalized.startswith(("revisi", "edit", "ganti", "perbaiki")):
        return "revision"
    return "unknown"


def update_job_upload(job, platform, upload_data, cleanup=False):
    job["upload"] = {
        "platform": platform,
        **upload_data,
    }
    deleted = []
    if cleanup:
        candidates = []
        if job.get("source_file"):
            candidates.append(Path(job["source_file"]))
        for clip in job.get("clips", []):
            for key in ("file", "captioned_file", "captioned_file_left", "captioned_file_right", "approved_file"):
                if clip.get(key):
                    candidates.append(Path(clip[key]))
        for path in candidates:
            if path.exists() and path.is_file():
                path.unlink()
                deleted.append(str(path))
        job["cleanup"] = {
            "status": "completed",
            "deleted_files": sorted(set(deleted)),
            "cleaned_at": now_iso(),
        }
    job["status"] = "uploaded"
    job["updated_at"] = now_iso()
    save_job(job)
    return deleted


def workspace_safe_file(path):
    resolved_workspace = WORKSPACE.resolve()
    resolved_path = Path(path).resolve()
    if not resolved_path.is_file():
        return None
    try:
        resolved_path.relative_to(resolved_workspace)
    except ValueError:
        raise RuntimeError(f"Refusing to delete outside workspace: {resolved_path}")
    return resolved_path


def cleanup_job_assets(job):
    job_id = job["job_id"]
    candidates = []
    if job.get("source_file"):
        candidates.append(Path(job["source_file"]))
    for clip in job.get("clips", []):
        for key in ("file", "captioned_file", "captioned_file_left", "captioned_file_right", "approved_file"):
            if clip.get(key):
                candidates.append(Path(clip[key]))
    for dirname in ("source", "clips", "approved", "tmp", "failed"):
        base = WORKSPACE / dirname
        if base.exists():
            candidates.extend(base.rglob(f"*{job_id}*"))

    deleted = []
    for candidate in sorted(set(candidates), key=lambda p: str(p)):
        path = workspace_safe_file(candidate)
        if not path:
            continue
        path.unlink()
        deleted.append(str(path))

    job["cleanup"] = {
        "status": "completed",
        "deleted_files": sorted(set(deleted)),
        "kept": ["metadata", "subtitles"],
        "cleaned_at": now_iso(),
    }
    job["updated_at"] = now_iso()
    save_job(job)
    return deleted


def youtube_id(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        return parsed.path.strip("/")
    if "youtube.com" in host:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
    return None


def clean_text(value, limit=None):
    text = re.sub(r"\s+", " ", (value or "")).strip()
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def safe_hashtag(value):
    tag = re.sub(r"[^A-Za-z0-9_]", "", value or "")
    if not tag:
        return None
    return f"#{tag[:40]}"


def metadata_from_url(url):
    video_id = youtube_id(url)
    if video_id:
        return youtube_oembed_metadata(url, video_id)
    try:
        raw = run([*ytdlp_base_cmd(), "--dump-json", "--skip-download", "--no-playlist", url])
        data = json.loads(raw)
        return {
            "id": data.get("id"),
            "title": clean_text(data.get("title"), 100),
            "channel": clean_text(data.get("channel") or data.get("uploader"), 80),
            "duration": data.get("duration"),
            "description": clean_text(data.get("description"), 500),
            "tags": [clean_text(tag, 40) for tag in (data.get("tags") or [])[:12]],
            "webpage_url": data.get("webpage_url") or url,
            "metadata_source": "yt-dlp",
        }
    except RuntimeError as exc:
        raise RuntimeError(f"Could not read metadata without authentication: {clean_text(str(exc), 300)}")


def youtube_oembed_metadata(url, video_id):
    oembed_url = f"https://www.youtube.com/oembed?url=https://youtu.be/{video_id}&format=json"
    with urlopen(oembed_url, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return {
        "id": video_id,
        "title": clean_text(data.get("title"), 100),
        "channel": clean_text(data.get("author_name"), 80),
        "duration": None,
        "description": "",
        "tags": [],
        "webpage_url": f"https://youtu.be/{video_id}" if video_id else url,
        "metadata_source": "youtube-oembed",
    }


def build_draft_post(job):
    meta = job.get("source_metadata") or {}
    base_title = clean_text(meta.get("title") or "Clip terbaru", 72)
    channel = clean_text(meta.get("channel"), 40)
    title = base_title
    if len(title) < 55 and channel:
        title = clean_text(f"{title} | {channel}", 80)

    hashtags = []
    for tag in meta.get("tags", []):
        hashed = safe_hashtag(tag)
        if hashed and hashed.lower() not in [h.lower() for h in hashtags]:
            hashtags.append(hashed)
        if len(hashtags) >= 5:
            break
    for tag in DEFAULT_HASHTAGS:
        if tag.lower() not in [h.lower() for h in hashtags]:
            hashtags.append(tag)

    source_line = f"Sumber: {meta.get('webpage_url') or job.get('source_url')}"
    original_desc = clean_text(meta.get("description"), 220)
    description_parts = [
        title,
        "",
        original_desc or "Potongan singkat dari video utama.",
        "",
        " ".join(hashtags[:8]),
        "",
        source_line,
    ]
    return {
        "title": title,
        "description": "\n".join(part for part in description_parts if part is not None),
        "hashtags": hashtags[:8],
        "source_channel": channel,
        "generated_at": now_iso(),
    }


def new_youtube_job(url):
    ensure_dirs()
    video_id = youtube_id(url)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    job_id = f"yt-{video_id or 'url'}-{stamp}"
    job = {
        "job_id": job_id,
        "source_url": url,
        "source_video_id": video_id,
        "status": "created",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source_file": None,
        "source_metadata": {},
        "draft_post": {},
        "clips": [],
        "approved_clips": [],
        "upload": {},
        "cleanup": {},
    }
    save_job(job)
    return job


def create_job(args):
    job = new_youtube_job(args.url)
    print(job["job_id"])


def create_local_job(args):
    create_file_job(args.file, metadata_url=None, origin="local")


def create_upload_job(args):
    create_file_job(args.file, metadata_url=args.metadata_url, origin="upload")


def create_file_job(file_path, metadata_url=None, origin="local"):
    ensure_dirs()
    src = Path(file_path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise SystemExit(f"File not found: {src}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    job_id = f"{origin}-{src.stem}-{stamp}"
    dest = WORKSPACE / "source" / f"{job_id}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    metadata = {
        "title": clean_text(src.stem.replace("-", " ").replace("_", " "), 100),
        "channel": None,
        "duration": None,
        "description": "",
        "tags": [],
        "webpage_url": metadata_url,
        "metadata_source": "filename",
    }
    if metadata_url:
        metadata = metadata_from_url(metadata_url)
    job = {
        "job_id": job_id,
        "source_url": metadata_url,
        "source_video_id": None,
        "status": "downloaded",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source_file": str(dest),
        "source_metadata": metadata,
        "draft_post": {},
        "clips": [],
        "approved_clips": [],
        "input_mode": origin,
        "upload": {},
        "cleanup": {},
    }
    job["draft_post"] = build_draft_post(job)
    save_job(job)
    print(job_id)


def download_job(job_id):
    ensure_dirs()
    job = load_job(job_id)
    if job.get("source_url"):
        job["source_metadata"] = metadata_from_url(job["source_url"])
        job["draft_post"] = build_draft_post(job)
    out_template = str(WORKSPACE / "source" / f"{job_id}.%(ext)s")
    cmd = [
        *ytdlp_base_cmd(),
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-f",
        "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "-o",
        out_template,
        job["source_url"],
    ]
    try:
        run(cmd)
    except RuntimeError as exc:
        job["status"] = "failed"
        job["error"] = {
            "stage": "download",
            "message": clean_text(str(exc), 500),
            "failed_at": now_iso(),
        }
        job["updated_at"] = now_iso()
        save_job(job)
        raise
    matches = sorted((WORKSPACE / "source").glob(f"{job_id}.*"))
    if not matches:
        raise SystemExit("Download finished but source file was not found")
    job["source_file"] = str(matches[0])
    job["status"] = "downloaded"
    job["updated_at"] = now_iso()
    save_job(job)
    return job


def download(args):
    job = download_job(args.job_id)
    print(job["source_file"])


def ffprobe_duration(path):
    out = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return float(out)


FFMPEG_STATIC = "/root/ffmpeg-static/ffmpeg-7.0.2-amd64-static/ffmpeg"


def ffmpeg_bin():
    import os
    if os.path.isfile(FFMPEG_STATIC):
        return FFMPEG_STATIC
    return "ffmpeg"


def video_encode_args():
    ffbin = ffmpeg_bin()
    encoders = run([ffbin, "-hide_banner", "-encoders"])
    if "libx264" in encoders:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    return ["-c:v", "mpeg4", "-q:v", "5"]


def caption_job(job_id, model="medium"):
    with queued_pipeline(f"caption {job_id}"):
        script = ROOT / "scripts" / "render-subtitled-clips.py"
        run([sys.executable, str(script), job_id, "--model", model])
        return load_job(job_id).get("clips", [])


def render_job(job_id, clips, duration_seconds):
    ensure_dirs()
    job = load_job(job_id)
    if not job.get("source_file"):
        raise SystemExit("Job has no source_file. Run download first.")
    source = Path(job["source_file"])
    total = ffprobe_duration(source)
    clip_count = max(1, clips)
    duration = max(10, duration_seconds)
    spacing = max(1, int(total // (clip_count + 1)))
    rendered = []
    for index in range(1, clip_count + 1):
        start = max(0, min(int(spacing * index), int(max(0, total - duration))))
        out = WORKSPACE / "clips" / f"{job_id}_clip{index}.mp4"
        filters = [
            "crop=ih*9/16:ih:(iw-ih*9/16)/2:0",
            "scale=1080:1920",
            "setsar=1",
        ]
        vf = ",".join(filters)
        cmd = [
            ffmpeg_bin(),
            "-y",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(duration),
            "-vf",
            vf,
            *video_encode_args(),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out),
        ]
        run(cmd)
        rendered.append({
            "index": index,
            "file": str(out),
            "start_seconds": start,
            "duration_seconds": duration,
            "status": "preview",
        })
    job["clips"] = rendered
    job["status"] = "rendered"
    job["updated_at"] = now_iso()
    save_job(job)
    return rendered


def render(args):
    rendered = render_job(args.job_id, args.clips, args.duration)
    print(json.dumps(rendered, indent=2))


def caption(args):
    clips = caption_job(args.job_id, args.model)
    print(json.dumps(clips, indent=2, ensure_ascii=False))


def approve_clip(job, clip_index):
    clip = next((c for c in job.get("clips", []) if c["index"] == clip_index), None)
    if not clip:
        raise SystemExit(f"Clip not found: {clip_index}")
    src = Path(clip.get("captioned_file") or clip["file"])
    dest = WORKSPACE / "approved" / src.name
    shutil.copy2(src, dest)
    clip["status"] = "approved"
    clip["approved_file"] = str(dest)
    job.setdefault("approved_clips", [])
    if clip_index not in job["approved_clips"]:
        job["approved_clips"].append(clip_index)
    job["status"] = "approved"
    job["updated_at"] = now_iso()
    save_job(job)
    return dest, clip


def approve(args):
    job = load_job(args.job_id)
    dest, _ = approve_clip(job, args.clip)
    print(dest)


def import_google_upload_libs():
    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError(
            "Missing YouTube API dependency. Run: pip install -r requirements.txt"
        ) from exc
    return GoogleAuthRequest, Credentials, InstalledAppFlow, build, HttpError, MediaFileUpload


def youtube_secret_path(value=None):
    path = Path(value).expanduser() if value else DEFAULT_YOUTUBE_CLIENT_SECRET
    return path.resolve()


def youtube_token_path(value=None):
    path = Path(value).expanduser() if value else DEFAULT_YOUTUBE_TOKEN_FILE
    return path.resolve()


def youtube_credentials(client_secret=None, token_file=None, allow_auth=False, no_browser=False, port=0):
    GoogleAuthRequest, Credentials, InstalledAppFlow, *_ = import_google_upload_libs()
    client_secret_path = youtube_secret_path(client_secret)
    token_path = youtube_token_path(token_file)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    if creds and creds.valid:
        return creds
    if not allow_auth:
        raise RuntimeError(
            f"YouTube OAuth token is missing or expired: {token_path}. Run youtube-auth first."
        )
    if not client_secret_path.exists():
        raise RuntimeError(
            f"YouTube OAuth client secret not found: {client_secret_path}. "
            "Save the Google Cloud OAuth desktop client JSON there first."
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), YOUTUBE_SCOPES)
    creds = flow.run_local_server(
        host="127.0.0.1",
        port=port,
        open_browser=not no_browser,
        authorization_prompt_message=(
            "Open this URL in the VPS browser/profile that can reach localhost:\n{url}\n"
        ),
        success_message="YouTube OAuth is complete. You can close this browser tab.",
    )
    token_path.write_text(creds.to_json() + "\n")
    token_path.chmod(0o600)
    return creds


def youtube_service(client_secret=None, token_file=None, allow_auth=False, no_browser=False, port=0):
    *_, build, _, _ = import_google_upload_libs()
    creds = youtube_credentials(
        client_secret=client_secret,
        token_file=token_file,
        allow_auth=allow_auth,
        no_browser=no_browser,
        port=port,
    )
    return build("youtube", "v3", credentials=creds)


def youtube_auth(args):
    token_path = youtube_token_path(args.token_file)
    youtube_credentials(
        client_secret=args.client_secret,
        token_file=args.token_file,
        allow_auth=True,
        no_browser=args.no_browser,
        port=args.port,
    )
    print(f"YouTube OAuth token saved: {token_path}")


def clip_upload_file(job, clip_index):
    clip = next((item for item in job.get("clips", []) if item.get("index") == clip_index), None)
    if not clip:
        raise SystemExit(f"Clip not found: {clip_index}")
    for key in ("approved_file", "captioned_file", "file"):
        if clip.get(key):
            path = Path(clip[key])
            if path.exists():
                return path, clip, key
    raise SystemExit(f"Clip {clip_index} has no existing video file")


def youtube_upload_args_from_job(job, title=None, description=None, tags=None):
    draft = job.get("draft_post") or {}
    upload_title = clean_text(title or draft.get("title") or "Clip terbaru", 100)
    upload_description = description if description is not None else draft.get("description", "")
    upload_tags = tags if tags is not None else draft.get("hashtags", [])
    upload_tags = [tag.lstrip("#") for tag in upload_tags if tag]
    return upload_title, upload_description, upload_tags


def upload_video_to_youtube(service, file_path, title, description, tags, privacy_status, made_for_kids, notify):
    *_, _, HttpError, MediaFileUpload = import_google_upload_libs()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }
    media = MediaFileUpload(str(file_path), chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=notify,
    )
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"Upload progress: {int(status.progress() * 100)}%", file=sys.stderr)
        except HttpError as exc:
            raise RuntimeError(f"YouTube upload failed: {exc}") from exc
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload finished without a video id: {clean_text(json.dumps(response), 500)}")
    return response


def upload_youtube_clip(args):
    service = youtube_service(
        client_secret=args.client_secret,
        token_file=args.token_file,
        allow_auth=args.auth_if_needed,
        no_browser=args.no_browser,
        port=args.port,
    )
    job = load_job(args.job_id)
    file_path, clip, selected_key = clip_upload_file(job, args.clip)
    tags = args.tag if args.tag is not None else None
    title, description, upload_tags = youtube_upload_args_from_job(
        job,
        title=args.title,
        description=args.description,
        tags=tags,
    )
    response = upload_video_to_youtube(
        service,
        file_path,
        title,
        description,
        upload_tags,
        args.privacy,
        args.made_for_kids,
        not args.no_notify,
    )
    video_id = response["id"]
    clip["status"] = "uploaded"
    clip["youtube_id"] = video_id
    clip["youtube_url"] = f"https://youtu.be/{video_id}"
    clip["youtube_privacy"] = args.privacy
    clip["uploaded_file_key"] = selected_key
    clip["uploaded_at"] = now_iso()
    update_job_upload(
        job,
        "youtube",
        {
            "youtube_id": video_id,
            "youtube_url": f"https://youtu.be/{video_id}",
            "status": "uploaded",
            "uploaded_at": now_iso(),
            "clip": args.clip,
            "file": str(file_path),
            "title": title,
            "privacy": args.privacy,
        },
        cleanup=args.cleanup,
    )
    return {
        "job_id": job["job_id"],
        "clip": args.clip,
        "youtube_id": video_id,
        "youtube_url": f"https://youtu.be/{video_id}",
        "privacy": args.privacy,
        "title": title,
    }


def upload_youtube(args):
    print(json.dumps(upload_youtube_clip(args), indent=2, ensure_ascii=False))


def approve_upload_youtube(args):
    job = load_job(args.job_id)
    approved_path, _ = approve_clip(job, args.clip)
    result = upload_youtube_clip(args)
    result["approved_file"] = str(approved_path)
    result["status"] = "approved_and_uploaded"
    print(json.dumps(result, indent=2, ensure_ascii=False))


def register_preview(args):
    job = load_job(args.job_id)
    clip = find_clip(job, args.clip)
    clip["preview_url"] = args.url
    clip["preview_status"] = "pending_approval"
    if args.channel_id:
        clip["preview_channel_id"] = args.channel_id
    if args.message_id:
        clip["preview_message_id"] = args.message_id
    job["status"] = "preview_pending_approval"
    job["updated_at"] = now_iso()
    save_job(job)

    record = upsert_pending_approval({
        "job_id": args.job_id,
        "clip": args.clip,
        "preview_url": args.url,
        "status": "pending",
        "privacy": args.privacy,
        "channel_id": args.channel_id,
        "message_id": args.message_id,
        "source": args.source,
        "registered_at": now_iso(),
    })
    print(json.dumps(record, indent=2, ensure_ascii=False))


def handle_discord_approval(args):
    action = parse_approval_action(args.text)
    if action == "unknown":
        raise SystemExit("Unknown approval action. Use approve, skip, or revisi ...")

    pending = latest_pending_approval(args.job_id, args.clip)
    job_id = pending["job_id"]
    clip_index = pending["clip"]
    job = load_job(job_id)
    clip = find_clip(job, clip_index)

    if action == "approve":
        approve_clip(job, clip_index)
        args.job_id = job_id
        args.clip = clip_index
        args.privacy = args.privacy or pending.get("privacy") or "unlisted"
        result = upload_youtube_clip(args)
        result["status"] = "approved_and_uploaded"
        result["preview_url"] = pending.get("preview_url")
        update_pending_approval(
            job_id,
            clip_index,
            status="uploaded",
            action="approve",
            youtube_url=result.get("youtube_url"),
            handled_text=args.text,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if action == "skip":
        clip["status"] = "skipped"
        clip["skip_reason"] = args.text
        clip["skipped_at"] = now_iso()
        job["updated_at"] = now_iso()
        save_job(job)
        record = update_pending_approval(
            job_id,
            clip_index,
            status="skipped",
            action="skip",
            handled_text=args.text,
        )
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return

    clip["status"] = "revision_requested"
    clip["revision_request"] = args.text
    clip["revision_requested_at"] = now_iso()
    job["updated_at"] = now_iso()
    save_job(job)
    record = update_pending_approval(
        job_id,
        clip_index,
        status="revision_requested",
        action="revision",
        handled_text=args.text,
    )
    print(json.dumps(record, indent=2, ensure_ascii=False))


def mark_uploaded(args):
    job = load_job(args.job_id)
    deleted = update_job_upload(
        job,
        "youtube",
        {
            "youtube_id": args.youtube_id,
            "youtube_url": f"https://youtu.be/{args.youtube_id}",
            "status": "uploaded",
            "uploaded_at": now_iso(),
        },
        cleanup=args.cleanup,
    )
    print(json.dumps(job["cleanup"], indent=2))


def cleanup_job(args):
    job = load_job(args.job_id)
    deleted = cleanup_job_assets(job)
    print(json.dumps({
        "job_id": job["job_id"],
        "deleted_count": len(deleted),
        "deleted_files": deleted,
        "kept": ["metadata", "subtitles"],
    }, indent=2))


def status(args):
    print(json.dumps(load_job(args.job_id), indent=2, sort_keys=True))


def draft_post(args):
    job = load_job(args.job_id)
    if args.refresh and job.get("source_url"):
        job["source_metadata"] = metadata_from_url(job["source_url"])
    job["draft_post"] = build_draft_post(job)
    job["updated_at"] = now_iso()
    save_job(job)
    print(json.dumps(job["draft_post"], indent=2, ensure_ascii=False))


def process_link_free(args):
    job = new_youtube_job(args.url)
    result = {
        "job_id": job["job_id"],
        "mode": "free_self_hosted",
        "source_url": args.url,
        "status": "created",
        "downloaded": False,
        "rendered": False,
        "clips": [],
        "draft_post": {},
        "error": None,
    }
    try:
        job["source_metadata"] = metadata_from_url(args.url)
        job["draft_post"] = build_draft_post(job)
        job["status"] = "metadata_ready"
        job["updated_at"] = now_iso()
        save_job(job)
        result["draft_post"] = job["draft_post"]

        job = download_job(job["job_id"])
        result["downloaded"] = True
        result["source_file"] = job.get("source_file")

        rendered = render_job(job["job_id"], args.clips, args.duration)
        result["rendered"] = True
        result["clips"] = rendered
        result["status"] = "rendered"
    except RuntimeError as exc:
        job = load_job(job["job_id"])
        job["status"] = "blocked_free_download"
        job["error"] = {
            "stage": "free_self_hosted",
            "message": clean_text(str(exc), 700),
            "failed_at": now_iso(),
            "next_options": [
                "try another public video link",
                "use an approved first-party source file",
                "use an approved external API/free-tier fallback",
            ],
        }
        job["updated_at"] = now_iso()
        save_job(job)
        result["status"] = "blocked_free_download"
        result["error"] = job["error"]
    print(json.dumps(result, indent=2, ensure_ascii=False))


def process_link_autopilot(args):
    with queued_pipeline("autopilot"):
        return _process_link_autopilot(args)


def _process_link_autopilot(args):
    job = new_youtube_job(args.url)
    result = {
        "job_id": job["job_id"],
        "mode": "yt_dlp_local",
        "source_url": args.url,
        "status": "created",
        "attempts": [],
        "clips": [],
        "draft_post": {},
        "error": None,
    }
    try:
        job["source_metadata"] = metadata_from_url(args.url)
        job["draft_post"] = build_draft_post(job)
        job["status"] = "metadata_ready"
        job["input_mode"] = "youtube_link"
        job["updated_at"] = now_iso()
        save_job(job)
        result["draft_post"] = job["draft_post"]

        downloaded = download_job(job["job_id"])
        result["attempts"].append({"provider": "yt-dlp", "status": "downloaded"})
        render_job(downloaded["job_id"], args.clips, args.duration)
        result["attempts"].append({"provider": "local-ffmpeg", "status": "rendered"})
        rendered = caption_job(downloaded["job_id"], args.caption_model)
        result["clips"] = rendered
        result["status"] = "captioned"
        result["attempts"].append({
            "provider": "faster-whisper",
            "status": "captioned",
            "model": args.caption_model,
        })
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except RuntimeError as exc:
        job = load_job(job["job_id"])
        job["status"] = "failed"
        job["error"] = {
            "stage": "download",
            "message": clean_text(str(exc), 700),
            "failed_at": now_iso(),
        }
        job["updated_at"] = now_iso()
        save_job(job)
        result["status"] = "failed"
        result["error"] = job["error"]
        print(json.dumps(result, indent=2, ensure_ascii=False))


def doctor(args):
    checks = {
        "python": sys.version.split()[0],
        "ffmpeg": tool_path("ffmpeg"),
        "ffprobe": tool_path("ffprobe"),
        "yt-dlp": tool_path("yt-dlp"),
    }
    missing = [name for name, value in checks.items() if not value]
    print(json.dumps(checks, indent=2))
    if missing:
        raise SystemExit(f"Missing dependency: {', '.join(missing)}")


def main():
    parser = argparse.ArgumentParser(description="Media content automation MVP")
    sub = parser.add_subparsers(required=True)

    init_p = sub.add_parser("init")
    init_p.set_defaults(func=lambda args: (ensure_dirs(), print(WORKSPACE))[1])

    create_p = sub.add_parser("create-job")
    create_p.add_argument("url")
    create_p.set_defaults(func=create_job)

    process_free_p = sub.add_parser("process-link-free")
    process_free_p.add_argument("url")
    process_free_p.add_argument("--clips", type=int, default=3)
    process_free_p.add_argument("--duration", type=int, default=45)
    process_free_p.set_defaults(func=process_link_free)

    autopilot_p = sub.add_parser("autopilot")
    autopilot_p.add_argument("url")
    autopilot_p.add_argument("--clips", type=int, default=3)
    autopilot_p.add_argument("--duration", type=int, default=45)
    autopilot_p.add_argument(
        "--caption-model",
        default="medium",
        help="faster-whisper model for subtitles. medium is default upload quality; use large-v3/large-v3-turbo for best accuracy.",
    )
    autopilot_p.set_defaults(func=process_link_autopilot)

    local_p = sub.add_parser("create-local-job")
    local_p.add_argument("file")
    local_p.set_defaults(func=create_local_job)

    upload_p = sub.add_parser("create-upload-job")
    upload_p.add_argument("file")
    upload_p.add_argument("--metadata-url")
    upload_p.set_defaults(func=create_upload_job)

    download_p = sub.add_parser("download")
    download_p.add_argument("job_id")
    download_p.set_defaults(func=download)

    render_p = sub.add_parser("render")
    render_p.add_argument("job_id")
    render_p.add_argument("--clips", type=int, default=3)
    render_p.add_argument("--duration", type=int, default=45)
    render_p.set_defaults(func=render)

    caption_p = sub.add_parser("caption")
    caption_p.add_argument("job_id")
    caption_p.add_argument("--model", default="medium")
    caption_p.set_defaults(func=caption)

    approve_p = sub.add_parser("approve")
    approve_p.add_argument("job_id")
    approve_p.add_argument("--clip", type=int, required=True)
    approve_p.set_defaults(func=approve)

    yt_auth_p = sub.add_parser("youtube-auth")
    yt_auth_p.add_argument("--client-secret")
    yt_auth_p.add_argument("--token-file")
    yt_auth_p.add_argument("--no-browser", action="store_true")
    yt_auth_p.add_argument("--port", type=int, default=0)
    yt_auth_p.set_defaults(func=youtube_auth)

    yt_upload_p = sub.add_parser("upload-youtube")
    yt_upload_p.add_argument("job_id")
    yt_upload_p.add_argument("--clip", type=int, required=True)
    yt_upload_p.add_argument("--title")
    yt_upload_p.add_argument("--description")
    yt_upload_p.add_argument("--tag", action="append")
    yt_upload_p.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    yt_upload_p.add_argument("--made-for-kids", action="store_true")
    yt_upload_p.add_argument("--no-notify", action="store_true")
    yt_upload_p.add_argument("--auth-if-needed", action="store_true")
    yt_upload_p.add_argument("--client-secret")
    yt_upload_p.add_argument("--token-file")
    yt_upload_p.add_argument("--no-browser", action="store_true")
    yt_upload_p.add_argument("--port", type=int, default=0)
    yt_upload_p.add_argument("--cleanup", action="store_true")
    yt_upload_p.set_defaults(func=upload_youtube)

    approve_upload_p = sub.add_parser("approve-upload")
    approve_upload_p.add_argument("job_id")
    approve_upload_p.add_argument("--clip", type=int, required=True)
    approve_upload_p.add_argument("--title")
    approve_upload_p.add_argument("--description")
    approve_upload_p.add_argument("--tag", action="append")
    approve_upload_p.add_argument("--privacy", choices=["private", "unlisted", "public"], default="unlisted")
    approve_upload_p.add_argument("--made-for-kids", action="store_true")
    approve_upload_p.add_argument("--no-notify", action="store_true")
    approve_upload_p.add_argument("--auth-if-needed", action="store_true")
    approve_upload_p.add_argument("--client-secret")
    approve_upload_p.add_argument("--token-file")
    approve_upload_p.add_argument("--no-browser", action="store_true")
    approve_upload_p.add_argument("--port", type=int, default=0)
    approve_upload_p.add_argument("--cleanup", action="store_true")
    approve_upload_p.set_defaults(func=approve_upload_youtube)

    preview_p = sub.add_parser("register-preview")
    preview_p.add_argument("job_id")
    preview_p.add_argument("--clip", type=int, required=True)
    preview_p.add_argument("--url", required=True)
    preview_p.add_argument("--privacy", choices=["private", "unlisted", "public"], default="unlisted")
    preview_p.add_argument("--source", default="hostinger")
    preview_p.add_argument("--channel-id")
    preview_p.add_argument("--message-id")
    preview_p.set_defaults(func=register_preview)

    approval_p = sub.add_parser("handle-discord-approval")
    approval_p.add_argument("text")
    approval_p.add_argument("--job-id")
    approval_p.add_argument("--clip", type=int)
    approval_p.add_argument("--title")
    approval_p.add_argument("--description")
    approval_p.add_argument("--tag", action="append")
    approval_p.add_argument("--privacy", choices=["private", "unlisted", "public"], default="unlisted")
    approval_p.add_argument("--made-for-kids", action="store_true")
    approval_p.add_argument("--no-notify", action="store_true", default=True)
    approval_p.add_argument("--auth-if-needed", action="store_true")
    approval_p.add_argument("--client-secret")
    approval_p.add_argument("--token-file")
    approval_p.add_argument("--no-browser", action="store_true")
    approval_p.add_argument("--port", type=int, default=0)
    approval_p.add_argument("--cleanup", action="store_true")
    approval_p.set_defaults(func=handle_discord_approval)

    uploaded_p = sub.add_parser("mark-uploaded")
    uploaded_p.add_argument("job_id")
    uploaded_p.add_argument("--youtube-id", required=True)
    uploaded_p.add_argument("--cleanup", action="store_true")
    uploaded_p.set_defaults(func=mark_uploaded)

    cleanup_p = sub.add_parser("cleanup-job")
    cleanup_p.add_argument("job_id")
    cleanup_p.set_defaults(func=cleanup_job)

    draft_p = sub.add_parser("draft-post")
    draft_p.add_argument("job_id")
    draft_p.add_argument("--refresh", action="store_true")
    draft_p.set_defaults(func=draft_post)

    status_p = sub.add_parser("status")
    status_p.add_argument("job_id")
    status_p.set_defaults(func=status)

    doctor_p = sub.add_parser("doctor")
    doctor_p.set_defaults(func=doctor)

    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
