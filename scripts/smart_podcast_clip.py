#!/usr/bin/env python3
"""
Smart podcast clip selector using:
- ffmpeg scene detection (camera angle changes)
- MediaPipe FaceMesh (lip movement = active speaker)
- Audio energy (engaging moments)
- faster-whisper (word-level subtitles)
"""

import subprocess, json, sys, os, re, math, tempfile
from pathlib import Path
import cv2

# ── Config ──────────────────────────────────────────────────────────────
FFMPEG  = "/root/ffmpeg-static/ffmpeg-7.0.2-amd64-static/ffmpeg"
FFPROBE = "/root/ffmpeg-static/ffmpeg-7.0.2-amd64-static/ffprobe"
OUT_DIR  = Path("/root/.openclaw/workspace/content-automation/workspace/clips")
SUBS_DIR = Path("/root/.openclaw/workspace/content-automation/workspace/subtitles")

MIN_CLIP  = 30   # seconds
MAX_CLIP  = 60   # seconds
SCAN_FPS  = 1    # frames per second to scan for face/lip activity

# ── Helpers ─────────────────────────────────────────────────────────────

def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.stdout, r.stderr

def get_duration(video_path):
    out, _ = run([FFPROBE, "-v", "quiet", "-print_format", "json",
                  "-show_format", str(video_path)])
    return float(json.loads(out)['format']['duration'])

def extract_audio_energy(video_path, start, duration, window=5):
    """Get mean audio volume for a segment."""
    _, err = run([
        FFMPEG, "-ss", str(start), "-t", str(duration),
        "-i", str(video_path),
        "-af", "volumedetect", "-vn", "-f", "null", "-"
    ])
    for line in err.split('\n'):
        if 'mean_volume' in line:
            try:
                return float(line.split(':')[1].strip().replace(' dB', ''))
            except: pass
    return -60.0

def detect_scenes(video_path, threshold=30.0):
    """Detect scene changes using ffmpeg scdet filter."""
    print("  Detecting scene changes...")
    _, err = run([
        FFMPEG, "-i", str(video_path),
        "-vf", f"scdet=threshold={threshold}",
        "-f", "null", "-"
    ])
    scenes = []
    for line in err.split('\n'):
        if 'lavfi.scd.time' in line:
            try:
                t = float(line.split('lavfi.scd.time:')[1].split()[0])
                scenes.append(t)
            except: pass
    return sorted(scenes)

def extract_sample_frames(video_path, start_t, duration, sample_fps=1):
    """Extract sample frames through ffmpeg so AV1/VP9 sources decode reliably."""
    frames = []
    with tempfile.TemporaryDirectory(prefix="podcast_frames_") as tmp:
        pattern = str(Path(tmp) / "frame_%04d.jpg")
        run([
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-ss", str(start_t), "-t", str(duration),
            "-i", str(video_path),
            "-vf", f"fps={sample_fps},scale=640:-1",
            "-q:v", "3",
            pattern,
        ])
        for path in sorted(Path(tmp).glob("frame_*.jpg")):
            frame = cv2.imread(str(path))
            if frame is not None:
                frames.append(frame)
    return frames


def analyze_scene_faces(video_path, start_t, duration, sample_fps=1):
    """
    Analyze frames using OpenCV DNN face detector.
    Returns: face_score (0-1 close-up), lip_score (motion estimate), face_x (0-1 position)
    """
    face_scores = []
    lip_scores = []
    x_positions = []
    prev_lower = None

    # Use OpenCV haar cascade (lightweight, no model download needed)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    frames = extract_sample_frames(video_path, start_t, duration, sample_fps)

    for frame in frames:
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))

        if len(faces) > 0:
            # Pick largest face
            areas = [fw * fh for (fx, fy, fw, fh) in faces]
            best_idx = areas.index(max(areas))
            fx, fy, fw, fh = faces[best_idx]
            face_area_ratio = (fw * fh) / (w * h)
            face_scores.append(face_area_ratio)
            cx = (fx + fw / 2) / w
            x_positions.append(cx)

            # Lip motion: diff in lower-face region vs previous frame
            lower_region = gray[fy + int(fh * 0.6):fy + fh, fx:fx + fw]
            if prev_lower is not None and prev_lower.shape == lower_region.shape:
                diff = cv2.absdiff(prev_lower, lower_region)
                motion = diff.mean() / 255.0
                lip_scores.append(motion)
            prev_lower = lower_region.copy()
        else:
            face_scores.append(0)
            prev_lower = None

    avg_face = sum(face_scores) / len(face_scores) if face_scores else 0
    avg_lip  = sum(lip_scores)  / len(lip_scores)  if lip_scores  else 0
    avg_x    = sum(x_positions) / len(x_positions) if x_positions else 0.5

    return avg_face, avg_lip, avg_x


def crop_focus_from_face_x(face_x):
    """Convert original-frame x focus to ffmpeg crop x ratio for 16:9 -> 9:16."""
    return max(0.0, min(1.0, (float(face_x) - 0.158) / 0.684))


def focus_to_crop_ratio(focus):
    focus = (focus or "auto").lower()
    if focus in {"wide", "pair", "fit", "both"}:
        return "wide"
    if focus == "left":
        return 0.0
    if focus == "right":
        return 1.0
    if focus == "center":
        return 0.5
    try:
        return max(0.0, min(1.0, float(focus)))
    except Exception:
        return None


def generate_wordlevel_ass(words, clip_offset, out_path):
    """Generate karaoke-style ASS from word-level whisper output."""
    def ass_time(s):
        s = max(0, float(s) - clip_offset)
        cs = int(round(s * 100))
        h = cs // 360000; cs %= 360000
        m = cs // 6000; cs %= 6000
        sec = cs // 100; cs %= 100
        return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pod,Arial Black,80,&H00FFFFFF,&H0000FFFF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,5,2,2,60,60,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    # Group into 3-word chunks
    i = 0
    while i < len(words):
        group = [words[i]]
        i += 1
        while i < len(words) and len(group) < 4:
            gap = words[i]['start'] - words[i-1]['end']
            if gap > 0.5:
                break
            dur = words[i]['end'] - group[0]['start']
            if dur > 2.5:
                break
            group.append(words[i])
            i += 1

        text = ' '.join(re.sub(r'[{}]', '', w['word']).strip().upper() for w in group if w['word'].strip())
        if not text:
            continue
        start = ass_time(group[0]['start'])
        end   = ass_time(group[-1]['end'] + 0.12)
        lines.append(f"Dialogue: 0,{start},{end},Pod,,0,0,0,,{text}")

    Path(out_path).write_text('\n'.join(lines))

# ── Main ─────────────────────────────────────────────────────────────────

def select_best_scene(video_path, skip_start=120, skip_end=300):
    total = get_duration(video_path)
    print(f"Video: {total/60:.1f} min")

    # Step 1: Detect all scene cuts
    scenes = detect_scenes(video_path)
    print(f"Found {len(scenes)} scene cuts")

    # Build scene segments: list of (start, end)
    boundaries = [skip_start] + [s for s in scenes if skip_start < s < total-skip_end] + [total-skip_end]
    segments = [(boundaries[i], boundaries[i+1])
                for i in range(len(boundaries)-1)
                if boundaries[i+1] - boundaries[i] >= MIN_CLIP]

    print(f"Candidate segments (≥{MIN_CLIP}s): {len(segments)}")

    if not segments:
        print("No segments found, using fallback.")
        return skip_start, MAX_CLIP

    # Step 2: Score each segment
    print("Scoring segments (face + lip + audio)...")
    scored = []
    for i, (seg_start, seg_end) in enumerate(segments[:40]):  # limit to 40
        seg_dur = min(seg_end - seg_start, MAX_CLIP)

        # Audio energy
        energy = extract_audio_energy(video_path, seg_start, min(30, seg_dur))

        # Face + lip (sample 10 frames)
        face_score, lip_score, face_x = analyze_scene_faces(
            video_path, seg_start, min(20, seg_dur), sample_fps=0.5)

        # Combined score
        # Normalize energy: -30 dB good, -20 dB excellent
        norm_energy = max(0, (energy + 30) / 10)
        # face_score > 0.03 = decent close-up
        norm_face = min(1, face_score / 0.06)
        # lip_score > 0.4 = clearly someone talking
        norm_lip = lip_score

        total_score = (norm_energy * 0.4) + (norm_face * 0.3) + (norm_lip * 0.3)

        mm, ss = divmod(int(seg_start), 60)
        scored.append({
            'start': seg_start,
            'duration': seg_dur,
            'score': total_score,
            'energy': energy,
            'face': face_score,
            'lip': lip_score,
            'face_x': face_x,
            'label': f"{mm:02d}:{ss:02d}",
        })

        if (i+1) % 5 == 0:
            print(f"  Scored {i+1}/{min(40, len(segments))}...")

    scored.sort(key=lambda x: x['score'], reverse=True)

    print("\nTop 5 scenes:")
    for s in scored[:5]:
        print(f"  {s['label']} score={s['score']:.3f} "
              f"(energy={s['energy']:.1f} face={s['face']:.3f} lip={s['lip']:.2f})")

    best = scored[0]
    return best['start'], min(best['duration'], MAX_CLIP), best.get('face_x', 0.5)

def render_clip(video_path, clip_start, clip_dur, job_tag="smart", focus="auto", face_x=0.5):
    """Transcribe and render one clip with word-level subtitles."""
    sys.path.insert(0, str(Path(video_path).parent.parent.parent.parent /
                           'content-automation/.venv/lib/python3.11/site-packages'))
    from faster_whisper import WhisperModel

    # Extract audio for transcription
    tmp_audio = f"/tmp/{job_tag}_audio.wav"
    run([FFMPEG, "-y", "-ss", str(clip_start), "-t", str(clip_dur+5),
         "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", tmp_audio])

    print(f"Transcribing {clip_dur:.0f}s clip from {int(clip_start)//60:02d}:{int(clip_start)%60:02d}...")
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(tmp_audio, language="id",
                                word_timestamps=True, vad_filter=True,
                                beam_size=3, condition_on_previous_text=False)

    words = []
    for seg in segs:
        if seg.words:
            for w in seg.words:
                if w.start <= clip_dur:
                    words.append({'word': w.word.strip(), 'start': w.start, 'end': min(w.end, clip_dur)})

    print(f"  {len(words)} words transcribed")

    # Generate ASS
    ass_path = SUBS_DIR / f"{job_tag}.ass"
    generate_wordlevel_ass(words, 0, ass_path)

    # Render
    out_path = OUT_DIR / f"{job_tag}_subtitled.mp4"
    esc_ass = str(ass_path).replace(':', '\\:')
    crop_ratio = focus_to_crop_ratio(focus)
    if crop_ratio == "wide":
        # Keep the full horizontal podcast frame visible inside a 9:16 canvas.
        # This is safer for 2-person/two-side podcast scenes than guessing a
        # tight speaker crop and accidentally cutting out the active speaker.
        vf = (
            "[0:v]split=2[bgsrc][fgsrc];"
            "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=24:2[bg];"
            "[fgsrc]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
            f"subtitles='{esc_ass}'"
        )
    else:
        if crop_ratio is None:
            crop_ratio = crop_focus_from_face_x(face_x)
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='(iw-ow)*{crop_ratio:.4f}':y='(ih-oh)/2',"
            "setsar=1,"
            f"subtitles='{esc_ass}'"
        )

    print(f"  Rendering...")
    _, err = run([
        FFMPEG, "-y",
        "-ss", str(clip_start), "-t", str(clip_dur),
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "faststart",
        str(out_path)
    ])
    if not out_path.exists():
        print("Render error:", err[-400:])
        return None

    print(f"  Done: {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    return out_path

# ── Entry point ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    video = sys.argv[1] if len(sys.argv) > 1 else \
        "/root/.openclaw/workspace/content-automation/workspace/source/yt-FSC1kOGDdLg-20260605114553.mp4"
    tag   = sys.argv[2] if len(sys.argv) > 2 else "podcast_smart"
    focus = sys.argv[3] if len(sys.argv) > 3 else "auto"

    print(f"=== Smart Podcast Clip: {Path(video).name} ===\n")

    clip_start, clip_dur, face_x = select_best_scene(video)
    mm, ss = divmod(int(clip_start), 60)
    print(f"\nSelected: {mm:02d}:{ss:02d} ({clip_dur:.0f}s), face_x={face_x:.2f}, focus={focus}\n")

    out = render_clip(video, clip_start, clip_dur, tag, focus=focus, face_x=face_x)
    if out:
        print(f"\nResult: {out}")
        meta = {'start': clip_start, 'duration': clip_dur, 'output': str(out), 'face_x': face_x, 'focus': focus}
        Path(f'/tmp/{tag}_meta.json').write_text(json.dumps(meta, indent=2))
