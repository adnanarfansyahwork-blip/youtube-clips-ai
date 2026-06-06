# Upload Adapters

Placeholder untuk integrasi upload otomatis setelah credential/API siap.

## YouTube

Butuh:

- Google Cloud project
- YouTube Data API v3 enabled
- OAuth client secret
- OAuth scope `https://www.googleapis.com/auth/youtube.upload`

Rule storage:

- Setelah upload sukses dan video ID tersimpan, jalankan cleanup file besar.
- Jangan hapus file jika upload error, masih retry, atau status belum jelas.

## TikTok

Butuh:

- TikTok Developer app
- Content Posting API
- Direct Post enabled
- Approval scope `video.publish`
- Access token dan open ID dari user authorization

