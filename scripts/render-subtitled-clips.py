#!/usr/bin/env python3
import argparse
import contextlib
import fcntl
import gc
import json
import os
import re
import subprocess
from pathlib import Path

from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
PIPELINE_LOCK_FILE = WORKSPACE / "tmp" / "content-pipeline.lock"
PIPELINE_LOCK_ENV = "CONTENT_AUTOMATION_QUEUE_HELD"
SUBTITLE_MARGIN_V = 760
TYPO_FIXES = {
    "RADITIKA": "RADITYA DIKA",
    "RADITYA DIKA DIKA": "RADITYA DIKA",
    "RANZ": "RANS",
    "RENS": "RANS",
    "RAFI AHMAD": "RAFFI AHMAD",
    "NAGITA SLAFINA": "NAGITA SLAVINA",
    "TIK TOK": "TIKTOK",
    "YOUTUB": "YOUTUBE",
}


def run(cmd):
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout


FFMPEG_STATIC = "/root/ffmpeg-static/ffmpeg-7.0.2-amd64-static/ffmpeg"


def ffmpeg_bin():
    import os
    return FFMPEG_STATIC if os.path.isfile(FFMPEG_STATIC) else "ffmpeg"


def video_encode_args():
    encoders = run([ffmpeg_bin(), "-hide_banner", "-encoders"])
    if "libx264" in encoders:
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    return ["-c:v", "mpeg4", "-q:v", "5"]


def load_job(job_id):
    path = WORKSPACE / "metadata" / f"{job_id}.json"
    return path, json.loads(path.read_text())


def save_job(path, job):
    path.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n")


@contextlib.contextmanager
def queued_pipeline(label):
    """Serialize RAM-heavy subtitle jobs when called outside mvp.py."""
    (WORKSPACE / "tmp").mkdir(parents=True, exist_ok=True)
    if os.environ.get(PIPELINE_LOCK_ENV):
        yield
        return

    with PIPELINE_LOCK_FILE.open("w") as lock:
        print(f"[queue] waiting for content pipeline slot: {label}")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        os.environ[PIPELINE_LOCK_ENV] = "1"
        print(f"[queue] started: {label}")
        try:
            yield
        finally:
            os.environ.pop(PIPELINE_LOCK_ENV, None)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            print(f"[queue] finished: {label}")


def ass_time(seconds):
    seconds = max(0, float(seconds))
    cs = int(round(seconds * 100))
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_caption(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"[{}]", "", text)
    text = text.upper()
    for wrong, right in TYPO_FIXES.items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text)
    return text.upper()


def wrap_caption(text, max_words=5):
    words = clean_caption(text).split()
    if not words:
        return ""
    lines = []
    for i in range(0, len(words), max_words):
        lines.append(" ".join(words[i : i + max_words]))
    return r"\N".join(lines[:2])


def ass_escape_path(path):
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def write_ass(path, segments):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,Anton,85,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,8,0,2,60,60,{SUBTITLE_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for seg in segments:
        text = wrap_caption(seg["text"])
        if not text:
            continue
        lines.append(
            f"Dialogue: 0,{ass_time(seg['start'])},{ass_time(seg['end'])},Shorts,,0,0,0,,{text}\n"
        )
    path.write_text("".join(lines))


def _render_wide_pair(source, start, duration, ass_file, out_path):
    """Render a 9:16 clip while keeping the full podcast frame visible."""
    vf = (
        "[0:v]split=2[bgsrc][fgsrc];"
        "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=24:2[bg];"
        "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        f"subtitles='{ass_escape_path(ass_file)}'"
    )
    run([
        ffmpeg_bin(), "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-vf", vf,
        *video_encode_args(),
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ])


def _transcribe_clip(source, clip, model, job_id):
    """Phase 1: extract audio + transcribe + write .ass (model still loaded)."""
    index = clip["index"]
    start = clip["start_seconds"]
    duration = clip["duration_seconds"]
    tmp_audio = WORKSPACE / "tmp" / f"{job_id}_clip{index}.wav"
    ass_file = WORKSPACE / "subtitles" / f"{job_id}_clip{index}.ass"

    run([
        ffmpeg_bin(), "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-vn", "-ac", "1", "-ar", "16000",
        str(tmp_audio),
    ])

    segments, info = model.transcribe(
        str(tmp_audio),
        language="id",
        vad_filter=False,
        beam_size=5,
        best_of=5,
        temperature=0,
        condition_on_previous_text=False,
        hallucination_silence_threshold=1.0,
    )
    caption_segments = [
        {"start": seg.start, "end": seg.end, "text": seg.text}
        for seg in segments
        if clean_caption(seg.text)
    ]
    write_ass(ass_file, caption_segments)

    clip["subtitle_file"] = str(ass_file)
    clip["subtitle_segments"] = caption_segments
    clip["subtitle_language"] = getattr(info, "language", "id")

    # cleanup temp audio immediately to free disk
    tmp_audio.unlink(missing_ok=True)


def _render_clip_with_ass(source, clip, job_id):
    """Phase 2: ffmpeg render using pre-generated .ass (model already freed)."""
    index = clip["index"]
    start = clip["start_seconds"]
    duration = clip["duration_seconds"]
    ass_file = Path(clip["subtitle_file"])
    out_file = WORKSPACE / "clips" / f"{job_id}_clip{index}_subtitled.mp4"

    _render_wide_pair(source, start, duration, ass_file, out_file)

    clip["captioned_file"] = str(out_file)
    clip.pop("captioned_file_left", None)
    clip.pop("captioned_file_right", None)
    clip["layout"] = "wide_pair"
    clip.pop("overlay_label", None)
    clip["status"] = "captioned"
    return out_file


def render_subtitled_clip(source, clip, model, job_id):
    """Legacy single-clip helper (used by external callers)."""
    _transcribe_clip(source, clip, model, job_id)
    return _render_clip_with_ass(source, clip, job_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument(
        "--model",
        default="medium",
        help="faster-whisper model. Use medium for upload quality, large-v3/large-v3-turbo for best accuracy.",
    )
    args = parser.parse_args()

    with queued_pipeline(f"render-subtitles {args.job_id}"):
        for name in ["tmp", "subtitles", "clips"]:
            (WORKSPACE / name).mkdir(parents=True, exist_ok=True)

        job_path, job = load_job(args.job_id)
        source = Path(job["source_file"])
        clips = job.get("clips", [])

        # Process one clip at a time: load model → transcribe → unload → render
        # This keeps peak RAM usage to one clip worth of memory at a time.
        outputs = []
        for i, clip in enumerate(clips, 1):
            print(f"[{i}/{len(clips)}] Transcribing clip {clip['index']} (model={args.model})...")
            model = WhisperModel(args.model, device="cpu", compute_type="int8")
            _transcribe_clip(source, clip, model, job["job_id"])
            del model
            gc.collect()

            print(f"[{i}/{len(clips)}] Rendering clip {clip['index']}...")
            out_file = _render_clip_with_ass(source, clip, job["job_id"])
            outputs.append(str(out_file))
            gc.collect()

        job["status"] = "captioned"
        job["preferred_output"] = "captioned_file"
        job["default_layout"] = "wide_pair"
        save_job(job_path, job)
        print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()
