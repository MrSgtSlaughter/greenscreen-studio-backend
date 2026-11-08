"""Offline smoke tests: API validation only; no YouTube download is attempted."""
import os
os.environ.setdefault('API_KEY','local-testing-key-not-for-production')
os.environ.setdefault('ALLOWED_ORIGIN','https://example.netlify.app')
from fastapi.testclient import TestClient
from app import app, normalize_youtube_url

client = TestClient(app)
key = {'X-API-Key': os.environ['API_KEY']}
url = 'https://youtu.be/dQw4w9WgXcQ'
def post(payload, headers=None):
    return client.post('/api/download', json=payload, headers=headers or {})

assert client.get('/health').status_code == 200
assert post({'url':url,'format':'mp4','authorized':True}).status_code == 401
assert post({'url':url,'format':'mp4','authorized':False},key).status_code == 400
assert post({'url':url,'format':'exe','authorized':True},key).status_code == 400
assert post({'url':'https://localhost/internal','format':'mp3','authorized':True},key).status_code == 400
assert normalize_youtube_url(url) == 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
assert client.options('/api/download',headers={'Origin':os.environ['ALLOWED_ORIGIN'],'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'x-api-key,content-type'}).headers.get('access-control-allow-origin') == os.environ['ALLOWED_ORIGIN']
print('PASS: health, authentication, authorization, format, URL validation, CORS')
