# youtube-clips-ai

Automation pipeline: YouTube video → short-form vertical clips with captions → upload ke YouTube.

Pipeline:
1. Download video dari YouTube pakai `yt-dlp` + cookies
2. Potong jadi clip pendek (30–60 detik)
3. Render ke format 9:16 (1080×1920) dengan background blur + video center
4. Generate subtitle/caption otomatis pakai `faster-whisper`
5. Upload ke YouTube (unlisted/private/public) via YouTube Data API v3

Job berat berjalan lewat antrean global di `workspace/tmp/content-pipeline.lock`, jadi beberapa cron/manual run tidak akan load Whisper/ffmpeg bersamaan dan membuat RAM berat.

---

## 🎬 Sample Output

Contoh hasil clip yang dirender oleh pipeline ini (format 9:16 vertical, wide-pair layout, auto-caption bahasa Indonesia):

| # | Source | Upload |
|---|--------|--------|
| 1 | Koleksinya Lebih Tua Dari Saya — Raditya Dika Podcast | [YouTube ↗](https://youtu.be/T4tymdHHNB0) |
| 2 | Koleksinya Lebih Tua Dari Saya — clip 2 | [YouTube ↗](https://youtu.be/6XycAYcqJes) |
| 3 | Test pipeline — 30 detik clip (yt-dlp + wide-pair render) | [YouTube ↗](https://youtu.be/zDjzBL_LqCI) |

Semua clip di atas dirender dengan setting: `libx264 medium crf20`, layout `wide_pair` 1080×1920, subtitle auto dari `faster-whisper medium`.

---

## ⚡ Quick Start (langsung pakai)

Cara tercepat dari nol sampai clip jadi:

```bash
# 1. Install ffmpeg
apt install ffmpeg

# 2. Clone repo
git clone https://github.com/adnanarfansyahwork-blip/youtube-clips-ai.git
cd youtube-clips-ai

# 3. Setup Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Buat folder runtime + secrets
python mvp.py init
mkdir -p secrets

# 5. Cek semua dependency siap
python mvp.py doctor
```

Setelah doctor OK, export cookies YouTube kamu:
1. Install ekstensi browser: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome) atau [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/) (Firefox)
2. Login ke YouTube di browser
3. Klik ekstensi → Export → simpan ke `secrets/youtube-cookies.txt`

Lalu jalankan pipeline penuh:

```bash
./scripts/process-link-autopilot.sh "https://youtu.be/VIDEO_ID" --clips 3 --duration 45 --caption-model medium
```

Output clip ada di `workspace/clips/` dalam format `*_subtitled.mp4`.
Kalau ada job lain sedang render/caption, command ini akan menunggu antrean sampai slot kosong.

> Upload ke YouTube butuh setup tambahan — lihat bagian [YouTube Upload API](#youtube-upload-api--setup).

---

## Yang Dibutuhkan

### Python 3.10+

**Fungsi:** menjalankan semua script pipeline (download, render, caption, upload).

**Cara dapat:**
```bash
# Ubuntu/Debian
apt install python3 python3-pip python3-venv

# Cek versi
python3 --version
```

---

### ffmpeg

**Fungsi:** memotong video, render layout 9:16 (wide-pair), dan encode output MP4. Ini komponen inti untuk semua operasi video.

**Cara dapat — Ubuntu/Debian:**
```bash
apt install ffmpeg
ffmpeg -version
```

**Cara dapat — static build (versi lebih baru, direkomendasikan untuk VPS):**
```bash
wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
mkdir -p /root/ffmpeg-static
tar xf ffmpeg-release-amd64-static.tar.xz -C /root/ffmpeg-static
```

Script otomatis mendeteksi static build di `/root/ffmpeg-static/ffmpeg-*/ffmpeg`. Jika tidak ada, fallback ke `ffmpeg` sistem.

---

### yt-dlp

**Fungsi:** download video dari YouTube. Mendukung cookies browser untuk bypass bot-check. Dipasang otomatis lewat pip.

**Cara dapat:** sudah include di `requirements.txt`, tidak perlu install manual.

Tapi jika ingin versi nightly (lebih update untuk bypass YouTube):
```bash
pip install "yt-dlp[default]" --pre
```

---

### faster-whisper

**Fungsi:** transkripsi audio jadi teks untuk subtitle/caption otomatis. Berjalan 100% lokal, tidak perlu API key. Model default `medium` dipakai untuk kualitas upload agar typo subtitle lebih sedikit.

**Cara dapat:** sudah include di `requirements.txt`. Model AI-nya didownload otomatis saat pertama kali dipakai.

Rekomendasi model:
- `small`: lebih cepat, cocok untuk tes.
- `medium`: default, lebih aman untuk upload.
- `large-v3` / `large-v3-turbo`: paling akurat, tapi lebih berat dan lebih lama.

---

### Node.js + bgutil (opsional tapi direkomendasikan)

**Fungsi:** menyediakan PO-token (Proof of Origin token) untuk yt-dlp. YouTube makin sering memblokir download dari IP server/VPS tanpa token ini. Tanpa Node.js, download masih bisa berhasil tapi lebih rentan diblokir.

**Cara dapat — Node.js:**
```bash
# Pakai nvm (direkomendasikan)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install --lts
node --version
```

**Cara dapat — bgutil:**
```bash
npm install -g bgutil-ytdlp-pot-provider
```

Atau lewat pip (helper Python-nya):
```bash
pip install bgutil-ytdlp-pot-provider
```

---

### Google Cloud + YouTube Data API (untuk upload)

**Fungsi:** mengupload clip ke channel YouTube kamu secara otomatis. Tidak dibutuhkan jika hanya ingin render clip lokal tanpa upload.

Cara setup lengkap ada di bagian [YouTube Upload API — Setup](#youtube-upload-api--setup) di bawah.

---

### YouTube Cookies (untuk download)

**Fungsi:** mengirim session login YouTube ke yt-dlp supaya download tidak diblokir bot-check. Didapat dari browser yang sudah login YouTube.

Cara export ada di bagian [YouTube Download — Setup Cookies](#youtube-download--setup-cookies) di bawah.

---

## Setup

### 1. Clone repo

```bash
git clone https://github.com/adnanarfansyahwork-blip/youtube-clips-ai.git
cd youtube-clips-ai
```

### 2. Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Buat folder secrets

```bash
mkdir -p secrets
```

Folder `secrets/` tidak di-commit ke git. Semua credential/token disimpan di sini.

### 4. Cek dependency

```bash
python mvp.py doctor
```

Output akan menunjukkan ffmpeg, yt-dlp, dan modul Python yang tersedia.

---

## YouTube Download — Setup Cookies

YouTube memblokir download dari IP VPS/server tanpa session yang valid. Solusi:

**Opsi A — Export cookies dari browser**

1. Install ekstensi browser: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/) (Firefox) atau [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome)
2. Login ke YouTube di browser kamu
3. Export cookies ke format Netscape
4. Simpan ke:

```
secrets/youtube-cookies.txt
```

**Opsi B — Browser profile otomatis (VPS dengan display)**

```bash
./scripts/launch-youtube-browser-profile.sh
```

Login ke YouTube di browser yang terbuka. Session tersimpan di `secrets/browser-profiles/`.

Proses link pakai session browser:

```bash
./scripts/process-link-with-browser-session.sh "https://youtu.be/VIDEO_ID" --clips 3 --duration 45
```

---

## YouTube Upload API — Setup

Upload ke channel YouTube pakai OAuth. Setup sekali:

### 1. Google Cloud Console

1. Buka <https://console.cloud.google.com/>
2. Buat project baru
3. Enable **YouTube Data API v3** (APIs & Services → Library)
4. Buat OAuth client: APIs & Services → Credentials → Create Credentials → OAuth client ID
5. Application type: **Desktop app**
6. Download file JSON

### 2. Simpan client secret

```bash
cp ~/Downloads/client_secret_*.json secrets/youtube_client_secret.json
```

### 3. Authorize channel YouTube

```bash
source .venv/bin/activate
python mvp.py youtube-auth --no-browser
```

Untuk VPS/headless: buka URL auth di browser lokal atau lewat port-forward, lalu paste authorization code ke terminal. Token tersimpan di `secrets/youtube_token.json`.

---

## Penggunaan

### Buat folder runtime (sekali)

```bash
python mvp.py init
```

### Full autopilot dari link YouTube

```bash
python mvp.py autopilot "https://youtu.be/VIDEO_ID" --clips 3 --duration 45 --caption-model medium
```

Atau pakai helper script:

```bash
./scripts/process-link-autopilot.sh "https://youtu.be/VIDEO_ID" --clips 3 --duration 45 --caption-model medium
```

Pipeline yang berjalan:
- Buat job
- Ambil metadata (judul, deskripsi)
- Download video via yt-dlp + cookies
- Render clip vertikal 9:16 dengan ffmpeg
- Generate subtitle/caption dengan faster-whisper lewat antrean global
- Simpan output ke `workspace/clips/`

### Dari file video lokal

```bash
python mvp.py create-upload-job /path/to/video.mp4
```

Dengan metadata dari YouTube:

```bash
python mvp.py create-upload-job /path/to/video.mp4 --metadata-url "https://youtu.be/VIDEO_ID"
```

### Render clip manual

```bash
python mvp.py render JOB_ID --clips 3 --duration 45
```

### Generate subtitle/caption

```bash
source .venv/bin/activate
python scripts/render-subtitled-clips.py JOB_ID --model medium
```

Model whisper yang tersedia: `tiny`, `base`, `small`, `medium` (default), `large-v3`, `large-v3-turbo`.
Model lebih besar = lebih akurat tapi lebih lambat.
Subtitle default diposisikan agak naik dari bawah supaya tidak ketutup UI Shorts/Reels/TikTok.

### Upload ke YouTube

```bash
python mvp.py upload-youtube JOB_ID --clip 1 --privacy unlisted
```

Privacy options: `private`, `unlisted`, `public`.

Upload lalu cleanup file besar:

```bash
python mvp.py upload-youtube JOB_ID --clip 1 --privacy unlisted --cleanup
```

### Approve + upload sekaligus

```bash
python mvp.py approve-upload JOB_ID --clip 1 --privacy unlisted --no-notify
```

---

## Struktur Folder

```
youtube-clips-ai/
├── mvp.py                    # Main CLI controller
├── requirements.txt
├── scripts/
│   ├── render-subtitled-clips.py     # Render + caption pipeline
│   ├── smart_podcast_clip.py         # Clip detection
│   ├── run-clip-pipeline.sh          # Shell pipeline wrapper
│   ├── process-link-autopilot.sh     # Full autopilot dari link
│   ├── publish-clips.sh              # Publish ke static host
│   └── ...
├── upload_adapters/          # YouTube / TikTok upload adapters
├── secrets/                  # Tidak di-commit — credentials & tokens
└── workspace/                # Tidak di-commit — video, clips, tmp
    ├── source/               # Video sumber
    ├── clips/                # Hasil render
    ├── subtitles/            # File .ass subtitle
    ├── metadata/             # Job manifest JSON
    └── tmp/                  # File sementara
```

---

## Format Output

Clip output: `workspace/clips/JOB_ID_clip1_subtitled.mp4`

- Resolusi: 1080×1920 (9:16 vertical)
- Layout: wide-pair (video full frame di tengah, background blur)
- Caption: uppercase, max 5 kata per baris, posisi tengah bawah
- Video codec: H.264 (libx264), preset medium, CRF 20
- Audio: AAC 192k

---

## Environment Variables (opsional)

```bash
# Pakai session browser tertentu untuk yt-dlp
export YTDLP_COOKIES_FROM_BROWSER="chrome"   # atau chromium, firefox

# Nonaktifkan notifikasi Discord setelah upload
export DISCORD_NOTIFY=0
```

---

## Troubleshooting

**YouTube error `LOGIN_REQUIRED` atau bot-check**
→ Cookies expired atau tidak ada. Export ulang dari browser yang sudah login YouTube, simpan ke `secrets/youtube-cookies.txt`.

**ffmpeg not found**
→ `apt install ffmpeg` atau pastikan static build ada di `/root/ffmpeg-static/`.

**faster-whisper error / model download gagal**
→ Model didownload otomatis ke `~/.cache/huggingface/`. Pastikan koneksi internet tersedia saat pertama kali run.

**YouTube auth `redirect_uri_mismatch`**
→ Di Google Cloud Console, tambahkan `http://localhost` ke Authorized redirect URIs.

---

## License

MIT
