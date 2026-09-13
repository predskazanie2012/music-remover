"""
music-remover — FastAPI server
Removes or replaces background music in video files.
Port: 7860
"""

import asyncio
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline.processor import (
    AUDIO_EXTENSIONS,
    JobConfig,
    VIDEO_EXTENSIONS,
    download_youtube,
    find_audio_files,
    process_single_video,
)

app = FastAPI(title="Music Remover")

from local_access import LocalOnly
app.add_middleware(LocalOnly)

app.mount("/static", StaticFiles(directory="static"), name="static")

BASE_DIR = Path(__file__).parent
MUSIC_LIB = BASE_DIR / "music_library"
MUSIC_LIB.mkdir(exist_ok=True)

jobs: Dict[str, dict] = {}
_ws_clients: Dict[str, List[WebSocket]] = {}
_ws_lock = threading.Lock()


# ─── WebSocket helpers ────────────────────────────────────────────────────────

def _broadcast(job_id: str, message: dict):
    global _loop
    if _loop is None:
        return
    with _ws_lock:
        clients = list(_ws_clients.get(job_id, []))
    for ws in clients:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(message), _loop)
        except Exception:
            pass


def _job_progress(job_id: str, file_idx: int, total: int, msg: str, pct):
    jobs[job_id]["files"][file_idx]["status"] = msg
    if pct is not None:
        jobs[job_id]["files"][file_idx]["progress"] = pct
    _broadcast(job_id, {
        "type": "progress",
        "file_idx": file_idx,
        "total": total,
        "msg": msg,
        "pct": pct,
    })


def _job_done(job_id: str):
    jobs[job_id]["status"] = "done"
    _broadcast(job_id, {"type": "done"})


def _job_error(job_id: str, error: str):
    jobs[job_id]["status"] = "error"
    jobs[job_id]["error"] = error
    _broadcast(job_id, {"type": "error", "msg": error})


# ─── Background worker ────────────────────────────────────────────────────────

def _run_job(job_id: str, video_files: List[Path], config: JobConfig):
    total = len(video_files)
    jobs[job_id]["total"] = total

    for idx, video_path in enumerate(video_files):
        jobs[job_id]["files"].append({
            "name": video_path.name,
            "status": "ожидание",
            "progress": 0,
            "output": None,
            "error": None,
        })

    for idx, video_path in enumerate(video_files):
        _broadcast(job_id, {"type": "file_start", "file_idx": idx, "name": video_path.name})

        work_dir = Path(tempfile.mkdtemp(prefix=f"mr_{job_id}_{idx}_"))
        try:
            def cb(msg: str, pct=None, _idx=idx):
                _job_progress(job_id, _idx, total, msg, pct)

            output = process_single_video(video_path, config, cb, work_dir)
            jobs[job_id]["files"][idx]["status"] = "готово"
            jobs[job_id]["files"][idx]["progress"] = 100
            jobs[job_id]["files"][idx]["output"] = str(output)
            _broadcast(job_id, {
                "type": "file_done",
                "file_idx": idx,
                "name": video_path.name,
                "output": str(output),
            })
        except Exception as exc:
            err = str(exc)
            jobs[job_id]["files"][idx]["status"] = "ошибка"
            jobs[job_id]["files"][idx]["error"] = err
            _broadcast(job_id, {
                "type": "file_error",
                "file_idx": idx,
                "name": video_path.name,
                "msg": err,
            })
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    _job_done(job_id)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/jobs")
async def create_job(
    mode: str = Form("remove"),
    demucs_model: str = Form("htdemucs"),
    cpu_threads: int = Form(0),
    music_volume: float = Form(0.20),
    output_folder: str = Form(""),
    music_file_lib: str = Form(""),   # filename in music_library/
    music_folder_path: str = Form(""), # local folder with replacement music
    youtube_urls: str = Form(""),      # newline-separated URLs
    folder_path: str = Form(""),       # local folder path
    files: List[UploadFile] = File(default=[]),
):
    job_id = str(uuid.uuid4())

    music_file = None
    music_files: List[str] = []
    if music_folder_path:
        music_folder = Path(music_folder_path)
        if music_folder.is_dir():
            music_files = [str(p) for p in find_audio_files(music_folder)]
    elif music_file_lib:
        candidate = MUSIC_LIB / music_file_lib
        if candidate.exists():
            music_file = str(candidate)

    if mode == "replace" and not music_file and not music_files:
        return JSONResponse(
            {"error": "Для замены музыки выберите аудиофайл или папку с музыкой."},
            status_code=400,
        )

    config = JobConfig(
        mode=mode,
        demucs_model=demucs_model,
        cpu_threads=cpu_threads,
        music_volume=music_volume,
        music_file=music_file,
        music_files=music_files,
        output_folder=output_folder,
    )

    jobs[job_id] = {
        "status": "running",
        "files": [],
        "total": 0,
        "error": None,
    }

    video_files: List[Path] = []

    # uploaded files → save to temp dir
    if files and any(f.filename for f in files):
        tmp_upload = Path(tempfile.mkdtemp(prefix=f"mr_upload_{job_id}_"))
        for uf in files:
            if not uf.filename:
                continue
            ext = Path(uf.filename).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            dest = tmp_upload / uf.filename
            content = await uf.read()
            dest.write_bytes(content)
            video_files.append(dest)

    # folder path → scan for videos
    if folder_path:
        fp = Path(folder_path)
        if fp.is_dir():
            for p in sorted(fp.iterdir()):
                if p.suffix.lower() in VIDEO_EXTENSIONS:
                    video_files.append(p)

    # YouTube URLs → download then process
    yt_urls = [u.strip() for u in youtube_urls.splitlines() if u.strip()]
    if yt_urls:
        yt_tmp = Path(tempfile.mkdtemp(prefix=f"mr_yt_{job_id}_"))
        jobs[job_id]["status"] = "downloading"
        _broadcast(job_id, {"type": "status", "msg": "Скачивание YouTube-видео..."})
        for url in yt_urls:
            try:
                downloaded = download_youtube(
                    url,
                    yt_tmp,
                    lambda msg: _broadcast(job_id, {"type": "yt_progress", "msg": msg}),
                )
                video_files.extend(downloaded)
            except Exception as exc:
                _broadcast(job_id, {"type": "yt_error", "url": url, "msg": str(exc)})

    if not video_files:
        del jobs[job_id]
        return JSONResponse({"error": "Нет видеофайлов для обработки."}, status_code=400)

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, video_files, config),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        return JSONResponse({"error": "Задание не найдено."}, status_code=404)
    return jobs[job_id]


@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()
    with _ws_lock:
        _ws_clients.setdefault(job_id, []).append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            clients = _ws_clients.get(job_id, [])
            if websocket in clients:
                clients.remove(websocket)


# ─── Music library ────────────────────────────────────────────────────────────

@app.get("/api/music-library")
async def list_music():
    items = []
    for p in sorted(MUSIC_LIB.iterdir()):
        if p.suffix.lower() in AUDIO_EXTENSIONS:
            items.append({"name": p.name, "size": p.stat().st_size})
    return {"files": items}


@app.post("/api/music-library")
async def upload_music(file: UploadFile = File(...)):
    dest = MUSIC_LIB / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"name": file.filename, "size": len(content)}


@app.delete("/api/music-library/{filename}")
async def delete_music(filename: str):
    p = MUSIC_LIB / filename
    if p.exists():
        p.unlink()
    return {"ok": True}


# ─── Folder browser ───────────────────────────────────────────────────────────

@app.get("/api/browse")
async def browse_folder(path: str = "", kind: str = "video"):
    """List directories and matching media files at given path for folder picker."""
    if not path:
        # return drives on Windows
        import string
        drives = []
        for d in string.ascii_uppercase:
            dp = Path(f"{d}:\\")
            if dp.exists():
                drives.append({"name": f"{d}:\\", "type": "drive", "path": str(dp)})
        return {"items": drives, "current": ""}

    p = Path(path)
    if not p.exists() or not p.is_dir():
        return JSONResponse({"error": "Папка не найдена"}, status_code=404)

    file_exts = AUDIO_EXTENSIONS if kind == "music" else VIDEO_EXTENSIONS
    file_type = "audio" if kind == "music" else "video"
    items = []
    try:
        for child in sorted(p.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                items.append({"name": child.name, "type": "dir", "path": str(child)})
            elif child.suffix.lower() in file_exts:
                items.append({
                    "name": child.name,
                    "type": file_type,
                    "path": str(child),
                    "size": child.stat().st_size,
                })
    except PermissionError:
        pass

    parent = str(p.parent) if p.parent != p else None
    return {"items": items, "current": str(p), "parent": parent}


@app.post("/api/create-folder")
async def create_folder(parent_path: str = Form(...), folder_name: str = Form(...)):
    """Create a subfolder inside the selected local folder."""
    parent = Path(parent_path)
    if not parent.is_dir():
        return JSONResponse({"error": "Текущая папка не найдена"}, status_code=404)

    name = folder_name.strip()
    invalid_chars = set('<>:"/\\|?*')
    if (
        not name
        or name in {".", ".."}
        or name.endswith((" ", "."))
        or any(ch in invalid_chars or ord(ch) < 32 for ch in name)
    ):
        return JSONResponse({"error": "Недопустимое имя папки"}, status_code=400)

    new_folder = parent / name
    try:
        new_folder.mkdir()
    except FileExistsError:
        return JSONResponse({"error": "Такая папка уже существует"}, status_code=409)
    except OSError as exc:
        return JSONResponse({"error": f"Не удалось создать папку: {exc}"}, status_code=400)

    return {"ok": True, "path": str(new_folder), "name": name}


@app.get("/api/scan-folder")
async def scan_folder(path: str):
    """Return all video files in a folder (non-recursive)."""
    p = Path(path)
    if not p.is_dir():
        return JSONResponse({"error": "Папка не найдена"}, status_code=404)
    videos = []
    for child in sorted(p.iterdir()):
        if child.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append({"name": child.name, "path": str(child), "size": child.stat().st_size})
    return {"videos": videos, "count": len(videos), "folder": str(p)}


@app.get("/api/scan-music-folder")
async def scan_music_folder(path: str):
    """Return all audio files in a folder (non-recursive)."""
    p = Path(path)
    if not p.is_dir():
        return JSONResponse({"error": "Папка не найдена"}, status_code=404)
    files = []
    for child in find_audio_files(p):
        files.append({"name": child.name, "path": str(child), "size": child.stat().st_size})
    return {"files": files, "count": len(files), "folder": str(p)}


# ─── Entry point ──────────────────────────────────────────────────────────────

_loop: Optional[asyncio.AbstractEventLoop] = None


@app.on_event("startup")
async def _capture_loop():
    global _loop
    _loop = asyncio.get_event_loop()


if __name__ == "__main__":
    import webbrowser
    import time

    def _open_browser():
        time.sleep(1.8)
        webbrowser.open("http://localhost:7860")

    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=7860)
