# Metadata Privacy Scrubber

A web app that removes personal metadata from images, PDFs, and Office files.
It removes location, camera and device details, timestamps, and author names.
It keeps AI provenance markers (C2PA Content Credentials, IPTC AI source type,
Stable Diffusion generation details) unchanged.

Files are processed in memory and removed from disk after each request.

## What it needs

- A server (VPS or container host) with Docker, and at least 2 GB of RAM.
- A domain name, pointed at the server. HTTPS is required in production.

Static hosts such as GitHub Pages or Netlify **will not work**, because the
cleaning runs on the server.

## Option 1: Docker Compose on a VPS (recommended)

This gives you HTTPS automatically through Caddy.

1. Copy this folder to the server.
2. Create a file named `.env` containing your domain:

   ```
   DOMAIN=scrub.example.com
   ```

3. Point the domain's DNS A record to the server's IP address.
4. Open ports 80 and 443 in the firewall.
5. Start the site:

   ```
   docker compose up -d --build
   ```

6. Visit `https://scrub.example.com`.

Check it is running: `curl https://scrub.example.com/health` should return
`{"status":"ok"}`.

## Option 2: Container platforms (Render, Railway, Fly.io, Google Cloud Run)

These platforms build the `Dockerfile` for you and provide HTTPS.

- Create a new web service from this folder or its Git repository.
- Choose Docker as the runtime. Use the default `Dockerfile`.
- Set the port to `8000`.
- Use at least 2 GB of memory.
- Set the health check path to `/health`.

## Option 3: Run without Docker (for testing only)

Requires `exiftool` and `qpdf` installed on the system.

```
pip install -r requirements.txt
gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 180 app:app
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MAX_UPLOAD_MB` | `100` | Maximum total upload size per request |
| `PORT` | `8000` | Port used by the container |

Up to 20 files can be cleaned in one request. Several files are returned as a zip.

## Privacy

- Uploaded files are held in a temporary folder only while they are processed,
  then deleted. Any leftover folder older than an hour is removed automatically.
- The app does not store uploads, file names, or logs of what was cleaned.
- The web server's access log records each request (method, path, status, and
  client address) but not file names or contents.
- If you put a proxy or CDN in front of the site, its own logs may record
  client addresses. Configure its retention to match your policy.
- Every response sends `Cache-Control: no-store`, and the page uses no
  third-party scripts, fonts, or trackers.

## Read-only AI marker checker

The `/inspect` page reports AI provenance markers without changing the file.
It shows whether a file has C2PA Content Credentials (and who created them),
the IPTC AI source type, and Stable Diffusion, ComfyUI, or Forge generation
data. It never removes or hides these markers, and it returns no file.

Signature validation needs the optional `c2pa-python` package. Without it, the
report says the signature was not checked.

## What it does not do

- It does not remove AI provenance markers. This is intentional. The site
  states this on its page.
- A file with a digital signature, such as a C2PA manifest, will show its
  signature as broken after cleaning, because the file has changed.
- Video and audio files are not supported yet.

## Testing

```
pip install pytest pillow pypdf python-docx python-pptx openpyxl
python -m pytest -v
```

The tests need `exiftool` and `qpdf` on the system.
They generate their own sample files and do not store any personal data.
