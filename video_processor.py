# video_processor.py
# 核心视频处理模块，负责构建和执行 FFmpeg 命令。

import subprocess
import os
import sys
import tempfile
import shlex
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple

from lrc_parser import parse_bilingual_lrc_with_metadata
from animations import (
    BACKGROUND_ANIMATIONS, TEXT_ANIMATIONS, COVER_ANIMATIONS,
    GENERATIVE_BACKGROUND_ANIMATIONS
)

# [新增] 使用 dataclass 封装所有参数，提高代码可读性和类型安全性
@dataclass
class VideoGenParams:
    """存储视频生成所需的所有参数。"""
    audio_path: Path
    cover_path: Path
    lrc_path: Path
    background_path: Path
    font_primary: Path
    font_size_primary: int
    font_secondary: Path
    font_size_secondary: int
    color_primary: str
    color_secondary: str
    outline_color: str
    outline_width: int
    background_anim: str
    text_anim: str
    cover_anim: str
    ffmpeg_path: str
    hw_accel: str
    song_title: str = None
    song_artist: str = None
    output_path: Path = field(default=None)
    output_image_path: Path = field(default=None)
    preview_time: float = 0.0
    logger: object = None
    duration: float = 0.0

def to_ffmpeg_color(hex_color: str) -> str:
    """将十六进制颜色字符串转换为FFmpeg可用的 '0xRRGGBB' 格式。"""
    return f"0x{hex_color.lstrip('#')}"

def get_ffmpeg_probe_path(ffmpeg_path_str: str) -> str:
    """
    安全地获取 ffprobe 的路径。
    优先查找与 ffmpeg 同级的目录，其次在系统 PATH 中查找。
    """
    if ffmpeg_path_str == 'ffmpeg':
        return 'ffprobe'

    ffmpeg_path = Path(ffmpeg_path_str)
    ffprobe_exe = 'ffprobe.exe' if sys.platform == 'win32' else 'ffprobe'

    # 1. 检查同级目录
    ffprobe_path_sibling = ffmpeg_path.parent / ffprobe_exe
    if ffprobe_path_sibling.is_file():
        return str(ffprobe_path_sibling)

    # 2. 尝试在系统 PATH 中查找
    try:
        cmd = ['where' if sys.platform == 'win32' else 'which', ffprobe_exe]
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        return result.stdout.strip().split('\n')[0]
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise FileNotFoundError(f"在FFmpeg同目录和系统PATH中都找不到 '{ffprobe_exe}'")

def _get_media_duration(ffprobe_path: str, media_path: Path, logger) -> float:
    """获取媒体文件的总时长（秒）。"""
    logger.status_update(f"正在分析文件: {media_path.name}")
    cmd = [
        ffprobe_path, '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(media_path)
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    )
    duration = float(result.stdout.strip())
    logger.status_update(f"文件时长: {duration:.2f}s")
    return duration

def _build_filter_complex(params: VideoGenParams, lrc_data: List, is_preview: bool, audio_stream_idx: int) -> str:
    """
    构建完整的FFmpeg filter_complex字符串。
    """
    params.logger.status_update(f"构建滤镜图 (背景: {params.background_anim}, 歌词: {params.text_anim}, 封面: {params.cover_anim})...")

    W, H, FPS = 1920, 1080, 60

    # 确定输入流索引
    is_generative_bg = params.background_anim in GENERATIVE_BACKGROUND_ANIMATIONS
    use_separate_bg = params.background_path != params.cover_path
    cover_stream_idx = 0
    background_stream_idx = 1 if not is_generative_bg and use_separate_bg else 0

    # 准备歌词数据
    lyrics_with_ends = [
        (start, lrc_data[i + 1][0] if i + 1 < len(lrc_data) else params.duration, primary, secondary)
        for i, (start, primary, secondary) in enumerate(lrc_data)
    ]
    visible_lyrics = _get_visible_lyrics(lyrics_with_ends, params, is_preview)

    # 构建滤镜链
    filters = []
    bg_filter_str = BACKGROUND_ANIMATIONS[params.background_anim](W=W, H=H, FPS=FPS, duration=params.duration)
    if is_generative_bg:
        filters.append(f"{bg_filter_str}[base_bg]")
    else:
        filters.append(f"[{background_stream_idx}:v]{bg_filter_str}[base_bg]")

    # --- Audio Visualization (Spectrum) ---
    # Create waves from audio stream
    # s=1920x200: width matches video width, height 200px
    # mode=line: smooth lines
    # colors=white@0.3: semi-transparent white
    filters.append(
        f"[{audio_stream_idx}:a]showwaves=s={W}x250:mode=line:colors=0xFFFFFF@0.3[waves]"
    )
    
    # Overlay waves on background (at the bottom)
    filters.append(f"[base_bg][waves]overlay=x=0:y={H}-250[bg_with_waves]")

    cover_filter_str = COVER_ANIMATIONS[params.cover_anim](duration=params.duration)
    filters.append(f"[{cover_stream_idx}:v]{cover_filter_str}[fg_cover]")
    
    # Overlay cover on [bg_with_waves] instead of [base_bg]
    filters.append(f"[bg_with_waves][fg_cover]overlay=x='(W/2.618-w)/2':y='(H-h)/2'[final_bg]")

    text_filter_str = ""
    if visible_lyrics:
        font_primary_escaped = str(params.font_primary).replace('\\', '/').replace(':', '\\:')
        font_secondary_escaped = str(params.font_secondary).replace('\\', '/').replace(':', '\\:')
        text_filter_str = TEXT_ANIMATIONS[params.text_anim](
            lyrics_with_ends=visible_lyrics,
            font_primary_escaped=font_primary_escaped,
            font_size_primary=params.font_size_primary,
            color_primary_ffmpeg=to_ffmpeg_color(params.color_primary),
            font_secondary_escaped=font_secondary_escaped,
            font_size_secondary=params.font_size_secondary,
            color_secondary_ffmpeg=to_ffmpeg_color(params.color_secondary),
            outline_color_ffmpeg=to_ffmpeg_color(params.outline_color),
            outline_width=params.outline_width
        )

    # --- Add Info and Timer Overlays ---
    info_filters = []
    
    # 1. Song Info (Title - Artist)
    if params.song_title or params.song_artist:
        title = params.song_title or "Unknown Title"
        artist = params.song_artist or "Unknown Artist"
        info_text = f"{title} - {artist}"
        # Basic escaping for drawtext
        info_text = info_text.replace(":", "\:").replace("'", "\u2019")
        
        font_secondary_escaped = str(params.font_secondary).replace('\\', '/').replace(':', '\\:')
        color_secondary_ffmpeg = to_ffmpeg_color(params.color_secondary)
        
        info_filters.append(
            f"drawtext=fontfile='{font_secondary_escaped}':text='{info_text}':"
            f"fontsize={int(params.font_size_secondary * 0.6)}:fontcolor={color_secondary_ffmpeg}:"
            f"x=50:y=h-100:shadowcolor=black@0.5:shadowx=2:shadowy=2"
        )

    # 2. Timer (00:00 / 05:30)
    m = int(params.duration // 60)
    s = int(params.duration % 60)
    duration_str = f"{m:02d}\:{s:02d}"
    
    # Use secondary font for timer too
    font_secondary_escaped = str(params.font_secondary).replace('\\', '/').replace(':', '\\:')
    color_secondary_ffmpeg = to_ffmpeg_color(params.color_secondary)
    
    # %{pts:gmtime:0:%M\:%S} displays current timestamp in MM:SS format
    # Note: We need to escape : as \: for drawtext
    # Fix: %{pts} sometimes fails if timebase is not set correctly or in complex filter graphs.
    # Using %{pts:gmtime...} usually works, but let's try a safer approach if it fails.
    # Actually, the error "%{pts} requires at most 3 arguments" usually means the syntax inside gmtime is parsed incorrectly
    # or the version of ffmpeg handles arguments differently.
    # Let's simplify the time display logic.
    
    # Try using text='%{pts\:gmtime\:0\:%M\:%S}' directly.
    # If that fails, it might be due to the colon escaping in python string vs ffmpeg string.
    
    # We will try a slightly different syntax that is often more robust:
    # text='%{pts\:gmtime\:0\:%M\\\\\:%S}' (double escaping) might be needed depending on layers.
    # But usually the issue is the number of arguments to gmtime. 
    # gmtime takes: offset (optional), format (optional).
    # If we provide 0 and format, that is 2 arguments.
    
    # Let's try removing the second argument (format) to see if it defaults to something usable,
    # OR ensure we are passing exactly what we expect.
    # A common issue is the colon in the format string being interpreted as a separator.
    
    # Let's use a simpler format first to debug, or fix the escaping.
    # The error "requires at most 3 arguments" suggests it sees more than 3.
    # %{pts:gmtime:0:%M:%S} -> args are "gmtime", "0", "%M:%S".
    # If the colon in %M:%S is split, it sees "%M", "%S" etc.
    
    # We need to escape the colons in the time format string so they aren't parsed as argument separators.
    # In drawtext, arguments are separated by ':', so the format string itself needs escaping.
    # In the python string, we need to be careful.
    
    # Current: text='%{{pts\:gmtime\:0\:%M\:%S}}
    # Python f-string -> %{pts\:gmtime\:0\:%M\:%S}
    # FFmpeg sees: pts:gmtime:0:%M\:%S
    # The backslash might not be enough if it's being eaten by python or shell.
    
    # Let's try quadruple escaping for the colons inside the time format.
    # Or better, use the standard timecode option if possible, but drawtext uses expansion.
    
    # Let's try this robust form:
    info_filters.append(
        f"drawtext=fontfile='{font_secondary_escaped}':text='%{{pts\:gmtime\:0\:%M\\\\\:%S}} / {duration_str}':"
        f"fontsize={int(params.font_size_secondary * 0.5)}:fontcolor={color_secondary_ffmpeg}:"
        f"x=50:y=h-60:shadowcolor=black@0.5:shadowx=2:shadowy=2"
    )
    
    # Combine lyric filters with info filters
    combined_text_filter = text_filter_str
    if info_filters:
        info_filter_str = ",".join(info_filters)
        if combined_text_filter:
            combined_text_filter += f",{info_filter_str}"
        else:
            combined_text_filter = info_filter_str

    # [修复] 只有当 combined_text_filter 不为空时才添加逗号
    filter_separator = "," if combined_text_filter else ""
    final_chain = f"[final_bg]{combined_text_filter}{filter_separator}format=yuv420p"
    
    if is_preview:
        filters.append(f"{final_chain},select='eq(n\\,{int(params.preview_time * FPS)})'[v]")
    else:
        filters.append(f"{final_chain}[v]")
    return ";".join(filters)

def _get_visible_lyrics(lyrics_with_ends: List, params: VideoGenParams, is_preview: bool) -> List:
    """根据是否为预览模式，筛选出需要渲染的歌词以优化性能。"""
    if not is_preview:
        return lyrics_with_ends

    params.logger.status_update(f"优化预览: 筛选时间点 {params.preview_time:.2f}s 附近的歌词...")
    current_lyric_index = next((i for i, (start, end, _, _) in enumerate(lyrics_with_ends) if start <= params.preview_time < end), -1)

    if current_lyric_index == -1:
        params.logger.status_update("预览模式: 当前时间点无歌词。")
        return []

    if params.text_anim == "淡入淡出":
        params.logger.status_update("预览模式: 淡入淡出，只加载1行歌词。")
        return [lyrics_with_ends[current_lyric_index]]

    if params.text_anim == "滚动列表":
        window_size = 7
        start_idx = max(0, current_lyric_index - window_size)
        end_idx = min(len(lyrics_with_ends), current_lyric_index + window_size + 1)
        visible_lyrics = lyrics_with_ends[start_idx:end_idx]
        params.logger.status_update(f"预览模式: 滚动列表，加载了 {len(visible_lyrics)} 行歌词。")
        return visible_lyrics

    return lyrics_with_ends

def _run_ffmpeg_process(command: List[str], logger, duration: float = 0):
    """通用FFmpeg进程执行函数，包含进度解析。"""
    logged_command = ' '.join(shlex.quote(str(s)) for s in command)
    logger.status_update(f"--- 开始执行FFmpeg ---\n{logged_command}\n----------------------")

    creationflags = subprocess.CREATE_NO_WINDOW | subprocess.NORMAL_PRIORITY_CLASS if sys.platform == 'win32' else 0
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, encoding='utf-8', errors='ignore',
        creationflags=creationflags
    )

    if hasattr(logger, 'progress_update') and duration > 0:
        for line in iter(process.stdout.readline, ''):
            logger.status_update(line.strip())
            if progress_match := re.search(r'time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})', line):
                h, m, s, ds = map(float, progress_match.groups())
                current_time = h * 3600 + m * 60 + s + ds / 100
                percent = int(100 * current_time / duration)
                logger.progress_update(percent)
        process.wait()
    else:
        stdout, _ = process.communicate()
        if stdout: logger.status_update(stdout)

    if process.returncode != 0:
        # Capture remaining output if any
        # If communicate() was used, stdout is closed and consumed. 
        # If iter() was used, stdout is exhausted/closed.
        # We can't read from it again safely if it's closed.
        
        raise subprocess.CalledProcessError(process.returncode, command, f"FFmpeg 执行失败 (Exit Code: {process.returncode})。请检查日志。")

def _process_media(params: VideoGenParams, is_preview: bool = False):
    """统一处理视频生成和预览的通用函数。"""
    temp_filter_file = None
    
    # Define temp directory relative to this file
    base_dir = Path(__file__).parent.resolve()
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        logger = params.logger
        ffprobe_path = get_ffmpeg_probe_path(params.ffmpeg_path)
        params.duration = _get_media_duration(ffprobe_path, params.audio_path, logger)

        logger.status_update("正在解析双语LRC文件...")
        with open(params.lrc_path, 'r', encoding='utf-8') as f:
            lrc_data, metadata = parse_bilingual_lrc_with_metadata(f.read())
        if not lrc_data:
            raise ValueError("LRC文件解析失败或内容为空。")
            
        # Fill missing title/artist from metadata
        if not params.song_title and metadata.get('ti'):
            params.song_title = metadata.get('ti')
        if not params.song_artist and metadata.get('ar'):
            params.song_artist = metadata.get('ar')

        # [修复] 1. 统一构建所有输入
        command_inputs = ['-i', str(params.cover_path)]
        is_generative_bg = params.background_anim in GENERATIVE_BACKGROUND_ANIMATIONS
        if not is_generative_bg and params.background_path != params.cover_path:
            command_inputs.extend(['-i', str(params.background_path)])

        # Ensure audio is always input for filter graph (needed for visualization)
        audio_input_idx = len(command_inputs) // 2
        command_inputs.extend(['-i', str(params.audio_path)])
        
        audio_map_str = ""
        if not is_preview:
            # For final video, map the audio stream to output
            audio_map_str = f'-map {audio_input_idx}:a'

        # 2. 构建滤镜并写入临时文件
        full_filter_complex_string = _build_filter_complex(params, lrc_data, is_preview, audio_input_idx)
        
        # Save temp filter file to project temp dir
        with tempfile.NamedTemporaryFile(mode='w', suffix=".txt", delete=False, encoding='utf-8', dir=temp_dir) as f:
            f.write(full_filter_complex_string)
            temp_filter_file = f.name
            logger.status_update(f"已生成临时滤镜脚本: {temp_filter_file}")

        # [修复] 3. 使用包含所有输入的 `command_inputs` 构建命令
        command_base = [params.ffmpeg_path, '-y', *command_inputs]
        command_filters = ['-filter_complex_script', temp_filter_file]

        if is_preview:
            command = [*command_base, *command_filters, '-map', '[v]', '-vframes', '1', str(params.output_image_path)]
        else:
            video_codec_params = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20']
            if params.hw_accel and "无" not in params.hw_accel:
                if "NVIDIA" in params.hw_accel: video_codec_params = ['-c:v', 'h264_nvenc', '-preset', 'fast', '-cq', '23', '-profile:v', 'high']
                elif "AMD" in params.hw_accel: video_codec_params = ['-c:v', 'h264_amf', '-quality', 'balanced', '-rc', 'cqp', '-qp_p', '23', '-qp_i', '23']
                elif "Intel" in params.hw_accel: video_codec_params = ['-c:v', 'h264_qsv', '-preset', 'fast', '-global_quality', '23']
                logger.status_update(f"启用硬件加速: {params.hw_accel}，使用编码器 {video_codec_params[1]}")

            command = [
                *command_base, *command_filters, '-map', '[v]', *audio_map_str.split(),
                *video_codec_params, '-c:a', 'aac', '-b:a', '320k', '-pix_fmt', 'yuv420p',
                '-r', str(60), '-t', str(params.duration), str(params.output_path)
            ]

        _run_ffmpeg_process(command, logger, params.duration if not is_preview else 0)
        logger.status_update("处理成功完成。")

    finally:
        # if temp_filter_file and os.path.exists(temp_filter_file):
        #     try:
        #         os.remove(temp_filter_file)
        #         logger.status_update(f"已清理临时文件: {temp_filter_file}")
        #     except OSError as e:
        #         logger.status_update(f"清理临时文件失败: {e}")
        pass

def create_karaoke_video(params: VideoGenParams):
    """创建卡拉OK视频的入口函数。"""
    _process_media(params, is_preview=False)

def create_preview_frame(params: VideoGenParams):
    """创建预览帧的入口函数。"""
    _process_media(params, is_preview=True)