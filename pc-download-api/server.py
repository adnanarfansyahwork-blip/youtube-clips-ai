#!/usr/bin/env python3
"""
Simple yt-dlp download API server for Windows PC.
Expose via: cloudflared tunnel --url http://localhost:8766
VPS calls: GET /download?url=https://youtu.be/xxx
"""
import http.server
import json
import os
import subprocess
import threading
import urllib.parse
from pathlib import Path

PORT = 8766
YTDLP = str(Path(os.environ.get("LOCALAPPDATA","")) / "Microsoft/WinGet/Links/yt-dlp.exe")
OUTDIR = Path(os.environ.get("TEMP", "C:/tmp")) / "yt_download_api"
OUTDIR.mkdir(exist_ok=True)

active_jobs = {}
lock = threading.Lock()


def run_download(job_id, url):
    outfile = OUTDIR / f"{job_id}.mp4"
    cmd = [
        YTDLP, "--no-playlist",
        "--merge-output-format", "mp4",
        "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best[height<=720]",
        "-o", str(outfile), url
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    with lock:
        if proc.returncode == 0 and outfile.exists():
            active_jobs[job_id] = {"status": "done", "file": str(outfile), "size": outfile.stat().st_size}
        else:
            active_jobs[job_id] = {"status": "error", "error": proc.stderr[-500:]}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logs

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if parsed.path == "/health":
            self.send_json(200, {"status": "ok", "ytdlp": YTDLP})
            return

        if parsed.path == "/download":
            url = params.get("url")
            if not url:
                self.send_json(400, {"error": "missing url param"})
                return
            import hashlib, time
            job_id = hashlib.md5((url + str(time.time())).encode()).hexdigest()[:12]
            with lock:
                active_jobs[job_id] = {"status": "queued"}
            t = threading.Thread(target=run_download, args=(job_id, url), daemon=True)
            t.start()
            self.send_json(202, {"job_id": job_id, "status": "queued"})
            return

        if parsed.path == "/status":
            job_id = params.get("job_id")
            with lock:
                job = active_jobs.get(job_id)
            if not job:
                self.send_json(404, {"error": "job not found"})
                return
            self.send_json(200, job)
            return

        if parsed.path == "/file":
            job_id = params.get("job_id")
            with lock:
                job = active_jobs.get(job_id)
            if not job or job.get("status") != "done":
                self.send_json(404, {"error": "file not ready"})
                return
            filepath = Path(job["file"])
            if not filepath.exists():
                self.send_json(404, {"error": "file missing on disk"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", filepath.stat().st_size)
            self.send_header("Content-Disposition", f'attachment; filename="{filepath.name}"')
            self.end_headers()
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return

        self.send_json(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"PC Download API running on port {PORT}", flush=True)
    print(f"yt-dlp: {YTDLP}", flush=True)
    print(f"Output dir: {OUTDIR}", flush=True)
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
