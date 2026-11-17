import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

VERCEL_HOSTED = (
    os.getenv("VERCEL_ENV") in {"preview", "production"}
    or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))
)
DATA = (ROOT / os.getenv("CLIP_DATA_DIR", "/tmp/clip-factory" if VERCEL_HOSTED else "data")).resolve()
SOURCE_DIR = DATA / "sources"
CLIP_DIR = DATA / "clips"
EDIT_DIR = DATA / "shorts"
BANNER_DIR = DATA / "banners"
IMPORT_DIR = DATA / "imports"
TWITCH_DIR = DATA / "twitch"
TWITCH_RESERVE_BYTES = int(float(os.getenv("TWITCH_DISK_RESERVE_GB", "5")) * 1024**3)
MAX_BYTES = int(float(os.getenv("CLIP_MAX_UPLOAD_GB", "20")) * 1024**3)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:3000").rstrip("/")
FFMPEG = os.getenv("FFMPEG", "ffmpeg")
FFPROBE = os.getenv("FFPROBE", "ffprobe")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b").strip()
if ":" not in OLLAMA_MODEL.rsplit("/", 1)[-1]:
    OLLAMA_MODEL += ":latest"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", str(min(6, os.cpu_count() or 1))))
EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v", ".ts"}
