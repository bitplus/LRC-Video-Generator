import os
import shutil
import asyncio
import time
import json
import uuid
import aiofiles
from pathlib import Path
from typing import List, Dict, Optional, Union
from dataclasses import dataclass, asdict

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from video_processor import VideoGenParams, create_preview_frame, create_karaoke_video, get_ffmpeg_probe_path
from animations import BACKGROUND_ANIMATIONS, TEXT_ANIMATIONS, COVER_ANIMATIONS
from lrc_parser import parse_bilingual_lrc_with_metadata

# Try to import color extractor
try:
    from color_extractor import extract_and_process_colors
    COLOR_EXTRACTION_AVAILABLE = True
except ImportError:
    COLOR_EXTRACTION_AVAILABLE = False

app = FastAPI(title="LRC Video Generator")

# CORS (allow all for local development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
BASE_DIR = Path(__file__).parent.resolve()
TEMP_DIR = BASE_DIR / "temp"
FONT_DIR = BASE_DIR / "font"
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "output"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
FONT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# State management
class TaskManager:
    def __init__(self):
        self.tasks: Dict[str, Dict] = {}

    def create_task(self, task_type: str) -> str:
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = {
            "id": task_id,
            "type": task_type,
            "status": "pending",
            "progress": 0,
            "message": "Initializing...",
            "result": None,
            "logs": []
        }
        return task_id

    def update_status(self, task_id: str, status: str, message: str = None):
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = status
            if message:
                self.tasks[task_id]["message"] = message

    def update_progress(self, task_id: str, progress: int, message: str = None):
        if task_id in self.tasks:
            self.tasks[task_id]["progress"] = progress
            if message:
                self.tasks[task_id]["message"] = message

    def add_log(self, task_id: str, message: str):
        if task_id in self.tasks:
            self.tasks[task_id]["logs"].append(message)
            # Keep only last 100 logs
            if len(self.tasks[task_id]["logs"]) > 100:
                self.tasks[task_id]["logs"].pop(0)

    def set_result(self, task_id: str, result: any):
        if task_id in self.tasks:
            self.tasks[task_id]["result"] = result
            self.tasks[task_id]["status"] = "completed"

    def set_error(self, task_id: str, error: str):
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = "failed"
            self.tasks[task_id]["message"] = error
            self.add_log(task_id, f"Error: {error}")

    def get_task(self, task_id: str):
        return self.tasks.get(task_id)

task_manager = TaskManager()

class WebLogger:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.start_time = time.time()
        self._last_percent = -1

    def status_update(self, message: str):
        task_manager.add_log(self.task_id, message)
        # Also update message in task status, but not overly frequent
        task_manager.tasks[self.task_id]["message"] = message

    def progress_update(self, percent: int):
        if percent > self._last_percent:
            self._last_percent = percent
            elapsed_time = time.time() - self.start_time
            remaining_msg = ""
            if percent > 0:
                total_time = (elapsed_time / percent) * 100
                remaining_time = total_time - elapsed_time
                remaining_time_str = time.strftime('%H:%M:%S', time.gmtime(remaining_time))
                remaining_msg = f"剩余时间: {remaining_time_str}"
            
            task_manager.update_progress(self.task_id, percent, remaining_msg)

# Models
class GenerateRequest(BaseModel):
    audio_path: str
    cover_path: str
    lrc_path: str
    background_path: Optional[str] = None
    
    font_primary: str
    font_size_primary: int
    font_secondary: str
    font_size_secondary: int
    
    color_primary: str
    color_secondary: str
    outline_color: str
    outline_width: int
    
    background_anim: str
    text_anim: str
    cover_anim: str
    
    ffmpeg_path: str = "ffmpeg"
    hw_accel: str = "无 (软件编码 x264)"
    
    preview_time: float = 0.0

class ColorExtractRequest(BaseModel):
    cover_path: str

# Helper functions
def get_file_path(filename: str) -> Path:
    # Check if absolute path
    p = Path(filename)
    if p.is_absolute() and p.exists():
        return p
    return TEMP_DIR / filename

def run_preview_task(task_id: str, params_dict: dict):
    try:
        task_manager.update_status(task_id, "processing", "Generating preview...")
        
        # Resolve paths
        audio_path = get_file_path(params_dict['audio_path'])
        cover_path = get_file_path(params_dict['cover_path'])
        lrc_path = get_file_path(params_dict['lrc_path'])
        bg_path = get_file_path(params_dict['background_path']) if params_dict.get('background_path') else cover_path
        
        font_primary = FONT_DIR / params_dict['font_primary']
        font_secondary = FONT_DIR / params_dict['font_secondary']
        
        output_image_path = TEMP_DIR / f"preview_{task_id}.png"
        
        params = VideoGenParams(
            audio_path=audio_path,
            cover_path=cover_path,
            lrc_path=lrc_path,
            background_path=bg_path,
            font_primary=font_primary,
            font_size_primary=params_dict['font_size_primary'],
            font_secondary=font_secondary,
            font_size_secondary=params_dict['font_size_secondary'],
            color_primary=params_dict['color_primary'],
            color_secondary=params_dict['color_secondary'],
            outline_color=params_dict['outline_color'],
            outline_width=params_dict['outline_width'],
            background_anim=params_dict['background_anim'],
            text_anim=params_dict['text_anim'],
            cover_anim=params_dict['cover_anim'],
            ffmpeg_path=params_dict['ffmpeg_path'],
            hw_accel=params_dict['hw_accel'],
            output_image_path=output_image_path,
            preview_time=params_dict['preview_time'],
            logger=WebLogger(task_id)
        )
        
        create_preview_frame(params)
        
        if output_image_path.exists() and output_image_path.stat().st_size > 0:
            task_manager.set_result(task_id, {"image_url": f"/files/temp/{output_image_path.name}"})
        else:
            task_manager.set_error(task_id, "Preview generation failed (empty file)")
            
    except Exception as e:
        task_manager.set_error(task_id, str(e))

def run_video_task(task_id: str, params_dict: dict):
    try:
        task_manager.update_status(task_id, "processing", "Starting video generation...")
        
        # Resolve paths
        audio_path = get_file_path(params_dict['audio_path'])
        cover_path = get_file_path(params_dict['cover_path'])
        lrc_path = get_file_path(params_dict['lrc_path'])
        bg_path = get_file_path(params_dict['background_path']) if params_dict.get('background_path') else cover_path
        
        font_primary = FONT_DIR / params_dict['font_primary']
        font_secondary = FONT_DIR / params_dict['font_secondary']
        
        # Generate filename based on metadata if possible
        output_filename = f"video_{task_id}.mp4"
        try:
            with open(lrc_path, 'r', encoding='utf-8') as f:
                lrc_content = f.read()
            _, metadata = parse_bilingual_lrc_with_metadata(lrc_content)
            if title := metadata.get("ti"):
                artist = metadata.get("ar", "")
                name = f"{artist} - {title}" if artist else title
                # Sanitize filename
                name = "".join([c for c in name if c.isalpha() or c.isdigit() or c in " -_"]).strip()
                if name:
                    output_filename = f"{name}.mp4"
        except:
            pass
            
        output_path = OUTPUT_DIR / output_filename
        
        params = VideoGenParams(
            audio_path=audio_path,
            cover_path=cover_path,
            lrc_path=lrc_path,
            background_path=bg_path,
            font_primary=font_primary,
            font_size_primary=params_dict['font_size_primary'],
            font_secondary=font_secondary,
            font_size_secondary=params_dict['font_size_secondary'],
            color_primary=params_dict['color_primary'],
            color_secondary=params_dict['color_secondary'],
            outline_color=params_dict['outline_color'],
            outline_width=params_dict['outline_width'],
            background_anim=params_dict['background_anim'],
            text_anim=params_dict['text_anim'],
            cover_anim=params_dict['cover_anim'],
            ffmpeg_path=params_dict['ffmpeg_path'],
            hw_accel=params_dict['hw_accel'],
            output_path=output_path,
            logger=WebLogger(task_id)
        )
        
        create_karaoke_video(params)
        
        if output_path.exists() and output_path.stat().st_size > 0:
            task_manager.set_result(task_id, {
                "video_url": f"/files/output/{output_path.name}",
                "filename": output_path.name
            })
        else:
            task_manager.set_error(task_id, "Video generation failed (empty file)")
            
    except Exception as e:
        task_manager.set_error(task_id, str(e))

# API Endpoints

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), type: str = Form(...)):
    try:
        print(f"DEBUG: Upload request received. Type: {type}, Filename: {file.filename}")
        
        # Ensure temp dir exists
        if not TEMP_DIR.exists():
            print(f"DEBUG: Creating temp dir at {TEMP_DIR}")
            TEMP_DIR.mkdir(parents=True, exist_ok=True)

        # Generate unique filename to avoid conflicts, but keep extension
        filename = file.filename or "unknown"
        ext = Path(filename).suffix
        
        # Generate safe UUID
        file_uuid = str(uuid.uuid4())
        new_filename = f"{file_uuid}{ext}"
        
        file_path = TEMP_DIR / new_filename
        print(f"DEBUG: Saving file to {file_path}")
        
        async with aiofiles.open(file_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
            
        print(f"DEBUG: File saved successfully. Size: {file_path.stat().st_size} bytes")
            
        # If it's audio, get duration (optional, just log if fails)
        duration = 0
        if type == "audio":
            try:
                # Use ffprobe to get duration
                from video_processor import get_ffmpeg_probe_path
                # We assume 'ffmpeg' is in path for now, or user sets it later. 
                # This simple check might fail if ffmpeg is not in PATH.
                # But the UI allows setting ffmpeg path. 
                # For upload response, we might just return 0 if failed.
                pass 
            except:
                pass
                
        return {
            "filename": new_filename,
            "original_name": file.filename,
            "path": str(file_path),
            "url": f"/files/temp/{new_filename}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
def get_config():
    # List fonts
    fonts = [f.name for f in FONT_DIR.iterdir() if f.suffix.lower() in ('.ttf', '.otf', '.ttc')]
    
    return {
        "fonts": fonts,
        "background_animations": list(BACKGROUND_ANIMATIONS.keys()),
        "text_animations": list(TEXT_ANIMATIONS.keys()),
        "cover_animations": list(COVER_ANIMATIONS.keys()),
        "hw_accels": ["无 (软件编码 x264)", "NVIDIA (h264_nvenc)", "AMD (h264_amf)", "Intel (h264_qsv)"],
        "color_extraction_available": COLOR_EXTRACTION_AVAILABLE
    }

@app.post("/api/preview")
async def create_preview(request: GenerateRequest, background_tasks: BackgroundTasks):
    task_id = task_manager.create_task("preview")
    background_tasks.add_task(run_preview_task, task_id, request.dict())
    return {"task_id": task_id}

@app.post("/api/generate")
async def create_video(request: GenerateRequest, background_tasks: BackgroundTasks):
    task_id = task_manager.create_task("video")
    background_tasks.add_task(run_video_task, task_id, request.dict())
    return {"task_id": task_id}

@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.post("/api/extract-colors")
def extract_colors(request: ColorExtractRequest):
    if not COLOR_EXTRACTION_AVAILABLE:
        raise HTTPException(status_code=400, detail="Color extraction not available")
    
    path = get_file_path(request.cover_path)
    if not path.exists():
         raise HTTPException(status_code=404, detail="Cover file not found")
         
    try:
        primary, secondary, outline = extract_and_process_colors(path)
        return {
            "color_primary": primary,
            "color_secondary": secondary,
            "outline_color": outline
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/audio-duration")
def get_audio_duration(path: str, ffmpeg_path: str = "ffmpeg"):
    full_path = get_file_path(path)
    try:
        from video_processor import get_ffmpeg_probe_path
        ffprobe_path = get_ffmpeg_probe_path(ffmpeg_path)
        
        cmd = [
            ffprobe_path, '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(full_path)
        ]
        result = os.popen(f"{' '.join(cmd)}").read()
        return {"duration": float(result.strip())}
    except Exception as e:
        print(f"Error getting duration: {e}")
        return {"duration": 0}

@app.get("/api/lrc-metadata")
def get_lrc_metadata(path: str):
    full_path = get_file_path(path)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        _, metadata = parse_bilingual_lrc_with_metadata(content)
        return metadata
    except Exception as e:
        return {}

# Serve static files
app.mount("/files/temp", StaticFiles(directory=TEMP_DIR), name="temp")
app.mount("/files/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # Open browser automatically
    import webbrowser
    webbrowser.open("http://127.0.0.1:8000")
    
    uvicorn.run(app, host="127.0.0.1", port=8000)
