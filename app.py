"""Authorized-media YouTube download API; deploy on a server that runs FFmpeg and yt-dlp.

This does not grant rights to download YouTube content. Teacher access key required.
"""
import asyncio
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
import shutil

API_KEY = os.environ.get('API_KEY', '')
ALLOWED_ORIGIN = os.environ.get('ALLOWED_ORIGIN', '')
if not API_KEY or len(API_KEY) < 20:
    raise RuntimeError('Set API_KEY to a secret of at least 20 characters before starting.')
if not ALLOWED_ORIGIN.startswith('https://') and not ALLOWED_ORIGIN.startswith('http://localhost'):
    raise RuntimeError('Set ALLOWED_ORIGIN to your exact Netlify site origin (https://...).')

app = FastAPI(title='GreenScreen Studio - Authorized Media Downloader', docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=[ALLOWED_ORIGIN], allow_methods=['POST','GET'], allow_headers=['Content-Type','X-API-Key'], expose_headers=['Content-Disposition'])
slots = asyncio.Semaphore(1)
MAX_SECONDS = 10*60
MAX_FILE_BYTES = 200*1024*1024

class DownloadRequest(BaseModel):
    url: str = Field(min_length=10, max_length=500)
    format: str
    authorized: bool

def normalize_youtube_url(raw: str) -> str:
    """Only canonical youtube video identifiers, not playlist or arbitrary remote URLs."""
    u = urlparse(raw)
    if u.scheme != 'https' or u.username or u.password or u.port:
        raise ValueError('Use a full HTTPS YouTube link.')
    host = (u.hostname or '').lower()
    if host in ('youtube.com','www.youtube.com','m.youtube.com') and u.path == '/watch':
        vid = parse_qs(u.query).get('v', [''])[0]
    elif host in ('youtube.com','www.youtube.com','m.youtube.com') and u.path.startswith('/shorts/'):
        vid = u.path.split('/')[2]
    elif host == 'youtu.be':
        vid = u.path.strip('/')
    else:
        raise ValueError('Paste an individual YouTube video URL (watch, Shorts or youtu.be).')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
        raise ValueError('Invalid YouTube video ID.')
    return 'https://www.youtube.com/watch?v='+vid

@app.get('/health')
def health():
    return {'status':'ok', 'service':'GreenScreen Studio Downloader'}

@app.post('/api/download')
async def download(req: DownloadRequest, x_api_key: str | None = Header(default=None)):
    import secrets
    if not secrets.compare_digest(x_api_key or '', API_KEY):
        raise HTTPException(401, 'Invalid teacher access key.')
    if not req.authorized:
        raise HTTPException(400, 'Permission confirmation required.')
    if req.format not in ('mp4','mp3'):
        raise HTTPException(400, 'Choose mp4 or mp3.')
    try:
        url = normalize_youtube_url(req.url)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    if slots.locked():
        raise HTTPException(429, 'Downloader is busy; try again when the current job finishes.')
    async with slots:
        workdir = Path(tempfile.mkdtemp(prefix='greenscreen_'))
        try:
            # Download only a single video, never a playlist, and reject videos > 10 min.
            # yt-dlp is external and may fail due to upstream site changes or rate limits.
            command = [
                'yt-dlp','--no-playlist','--no-progress','--no-warnings',
                '--no-call-home','--restrict-filenames','--no-write-info-json',
                '--max-filesize','200M','--match-filter',f'duration <= {MAX_SECONDS}',
                '--socket-timeout','15','--retries','2','--fragment-retries','2',
                '--paths',str(workdir),'--output','media.%(ext)s',
            ]
            if req.format == 'mp4':
                command += ['-f','bv*[height<=720]+ba/b[height<=720]/best',
                            '--recode-video','mp4', '--postprocessor-args','VideoConvertor:-c:v libx264 -preset veryfast -crf 24 -pix_fmt yuv420p -c:a aac -b:a 128k']
            else:
                command += ['-f','bestaudio/best','-x','--audio-format','mp3','--audio-quality','192K']
            command.append(url)
            try:
                proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    out,err = await asyncio.wait_for(proc.communicate(), timeout=210)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    raise HTTPException(504, 'Timed out downloading or converting this video.')
            except FileNotFoundError:
                raise HTTPException(503, 'Server is missing yt-dlp or FFmpeg.')
            if proc.returncode != 0:
                msg=(err or out).decode(errors='replace')[-800:]
                if 'duration' in msg.lower():
                    raise HTTPException(400,'Video exceeds the 10-minute limit.')
                raise HTTPException(422,'YouTube could not provide this file. It may be restricted, unavailable, or blocked by your host. '+msg)
            files=sorted(workdir.glob('media.*'))
            extension=req.format
            matches=[p for p in files if p.suffix.lower()=='.'+extension]
            if not matches:
                raise HTTPException(422,'No '+extension.upper()+' output was created; try a different video.')
            output=matches[-1]
            if not output.stat().st_size or output.stat().st_size > MAX_FILE_BYTES:
                raise HTTPException(413,'Output file is empty or exceeds the 200 MB limit.')
            return FileResponse(output, media_type='video/mp4' if extension=='mp4' else 'audio/mpeg',filename='greenscreen-youtube.'+extension,background=BackgroundTask(shutil.rmtree,workdir,ignore_errors=True),headers={'Cache-Control':'no-store'})
        except Exception:
            shutil.rmtree(workdir,ignore_errors=True)
            raise
