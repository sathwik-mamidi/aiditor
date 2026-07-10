import pathlib
import asyncio
import aiofiles
import filetype
from PIL import Image
import json
import subprocess
from typing import TypedDict, Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor

from app.config.config import config
from app.utils.logger import logger

FFPROBE_BIN = "ffprobe"
FFMPEG_BIN = "ffmpeg"

_thread_pool = ThreadPoolExecutor()

MIME_TYPE_MAPPINGS = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
    "image/heic": "image/heic",
    "image/heif": "image/heif",
    "application/heic": "image/heic",
    "application/heif": "image/heif",
    "video/mp4": "video/mp4",
    "application/mp4": "video/mp4",
    "video/quicktime": "video/mov",
    "application/x-quicktime": "video/mov",
    "video/x-msvideo": "video/avi",
    "application/x-msvideo": "video/avi",
    "video/mpeg": "video/mpeg",
    "application/x-mpeg": "video/mpeg",
    "video/x-flv": "video/x-flv",
    "application/x-flv": "video/x-flv",
    "video/webm": "video/webm",
    "application/x-webm": "video/webm",
    "video/x-ms-wmv": "video/wmv",
    "application/x-ms-wmv": "video/wmv",
    "video/3gpp": "video/3gpp",
    "application/3gpp": "video/3gpp",
    "video/3gpp2": "video/3gpp",
    "application/3gpp2": "video/3gpp",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/mpeg": "audio/mp3",
    "audio/mp3": "audio/mp3",
    "audio/aiff": "audio/aiff",
    "audio/x-aiff": "audio/aiff",
    "audio/aac": "audio/aac",
    "audio/x-aac": "audio/aac",
    "audio/ogg": "audio/ogg",
    "audio/vorbis": "audio/ogg",
    "audio/flac": "audio/flac",
    "audio/x-flac": "audio/flac",
}


class FileInfo(TypedDict, total=False):
    path: str
    name: str
    type: str
    size: int
    dimensions: Optional[Dict[str, int]]
    color_mode: Optional[str]
    bit_depth: Optional[int]
    dpi: Optional[tuple[int, int]]
    has_alpha: Optional[bool]
    duration: Optional[float]
    video_codec: Optional[str]
    audio_codec: Optional[str]
    frame_rate: Optional[float]
    bitrate_kbps: Optional[int]
    has_audio: Optional[bool]
    sample_rate: Optional[int]
    channels: Optional[int]
    error: Optional[str]

async def get_file_info(file_path_str: str) -> FileInfo:
    file_path = pathlib.Path(file_path_str)
    info: FileInfo = {
        "path": str(file_path),
        "name": file_path.name,
    }

    try:
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        stats = file_path.stat()
        info["size"] = stats.st_size

        async with aiofiles.open(file_path, mode='rb') as f:
            chunk = await f.read(4100)

        kind = filetype.guess(chunk)
        mime_type = kind.mime if kind else "application/octet-stream"

        info["type"] = MIME_TYPE_MAPPINGS.get(mime_type, "application/octet-stream")
        logger.debug(f"[getFileInfo] Path: {file_path}, Detected Mime: {mime_type}, Normalized: {info['type']}")

        if info["type"].startswith("image/"):
            try:
                img_metadata = await asyncio.get_event_loop().run_in_executor(
                    _thread_pool, _get_image_metadata, file_path
                )
                
                if img_metadata:
                    info.update(img_metadata)
                    
            except Exception as e:
                logger.error(f"Error getting image metadata for {file_path}: {e}")
                info["error"] = f"Failed to get image metadata: {e}"

        elif info["type"].startswith("video/") or info["type"].startswith("audio/"):
            try:
                probe_data = await _ffprobe_get_info(str(file_path))
                
                if not probe_data:
                    raise ValueError("Failed to get media probe data")
                
                await _process_media_probe_data(info, probe_data)
                
            except Exception as e:
                logger.error(f"Error processing media file {file_path}: {e}")
                info["error"] = f"Media analysis failed: {e}"

    except FileNotFoundError as e:
        logger.error(f"[getFileInfo] File not found: {file_path}")
        info["error"] = str(e)
    except Exception as e:
        logger.error(f"Failed to get file info for {file_path}: {e}")
        info["error"] = str(e)

    return info

async def generate_video_thumbnail(video_path_str: str) -> Optional[str]:
    video_path = pathlib.Path(video_path_str)

    try:
        logger.info(f"[Thumbnail] Starting generation for: {video_path}")

        if not video_path.is_file():
            logger.error(f"Video file not found for thumbnail generation: {video_path}")
            return None

        temp_thumb_dir = pathlib.Path("/tmp/thumbnails")
        temp_thumb_dir.mkdir(parents=True, exist_ok=True)
        
        thumb_filename = f"{video_path.stem}.jpg" # Original stem + .jpg
        thumb_path = temp_thumb_dir / thumb_filename

        logger.debug(f"[Thumbnail] Temporary output path: {thumb_path}")

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss", "00:00:00.500",
            "-i", str(video_path),
            "-map", "0:v:0?",
            "-vframes", "1",
            "-vf", "scale=320:-1,format=pix_fmts=yuv420p",
            "-q:v", "4",
            "-loglevel", "error",
            str(thumb_path)
        ]

        success, stdout, stderr = await _run_command(cmd, timeout=60)
        
        if not success:
            logger.error(f"[Thumbnail] ffmpeg error: {stderr}")
            return None
            
        if stderr:
            logger.debug(f"[Thumbnail] ffmpeg stderr: {stderr}")
            
        if thumb_path.is_file():
            logger.info(f"[Thumbnail] Successfully generated temporary thumbnail: {thumb_path}")
            return str(thumb_path) # Return the full path to the temporary thumbnail
        else:
            logger.error(f"[Thumbnail] Temporary thumbnail file not created at: {thumb_path}")
            return None

    except Exception as e:
        logger.error(f"[Thumbnail] Error generating thumbnail for {video_path}: {e}")
        return None

async def generate_image_thumbnail(image_path_str: str, max_size=(256, 256)) -> Optional[str]:
    image_path = pathlib.Path(image_path_str)
    
    try:
        from PIL import Image
    except ImportError:
        logger.error("Pillow library is not installed. Cannot generate image thumbnails.")
        return None

    try:
        logger.info(f"[Thumbnail] Starting image thumbnail generation for: {image_path}")

        if not image_path.is_file():
            logger.error(f"Image file not found for thumbnail generation: {image_path}")
            return None

        temp_thumb_dir = pathlib.Path("/tmp/thumbnails") # Using /tmp for docker environments
        temp_thumb_dir.mkdir(parents=True, exist_ok=True)
        
        thumb_filename = f"{image_path.stem}.jpg" # Original stem + .jpg
        thumb_path = temp_thumb_dir / thumb_filename

        with Image.open(image_path) as img:
            # Ensure image is in RGB mode for saving as JPG (handles RGBA, P, etc.)
            if img.mode == 'RGBA':
                # Create a white background image
                background = Image.new('RGB', img.size, (255, 255, 255))
                # Paste the RGBA image onto the white background
                background.paste(img, (0, 0), img)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Resize while maintaining aspect ratio (thumbnail)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Save as JPG
            img.save(thumb_path, "JPEG", quality=85, optimize=True)
            logger.debug(f"[Thumbnail] Image thumbnail saved to: {thumb_path}")

        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return str(thumb_path)
        else:
            logger.error(f"Generated image thumbnail {thumb_path} is empty or does not exist.")
            return None

    except Exception as e:
        logger.error(f"Error generating image thumbnail for {image_path}: {e}", exc_info=True)
        return None

async def _run_command(cmd: List[str], timeout: int = 30) -> Tuple[bool, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            success = process.returncode == 0
            return success, stdout.decode('utf-8', errors='replace'), stderr.decode('utf-8', errors='replace')
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            return False, "", f"Command timed out after {timeout} seconds: {' '.join(cmd)}"
            
    except Exception as e:
        return False, "", f"Failed to execute command: {e}"

async def _ffprobe_get_info(file_path: str) -> Optional[Dict[str, Any]]:
    cmd = [
        FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    
    success, stdout, stderr = await _run_command(cmd)
    
    if not success:
        logger.error(f"ffprobe failed: {stderr}")
        return None
    
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ffprobe JSON output: {e}")
        return None

def _get_image_metadata(file_path: pathlib.Path) -> Dict[str, Any]:
    with Image.open(file_path) as img:
        img_info = img.info
        dpi = img_info.get('dpi')
        
        if not isinstance(dpi, tuple) or len(dpi) != 2 or not all(isinstance(i, (int, float)) for i in dpi):
            dpi = (72, 72) # Default DPI
        else:
            dpi = tuple(int(i) for i in dpi)

        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img_info)
        
        bit_depth = None
        if img.mode == '1': bit_depth = 1
        elif img.mode == 'L': bit_depth = 8
        elif img.mode == 'P': bit_depth = 8 # Palette implies 8-bit indices
        elif img.mode in ('RGB', 'CMYK', 'YCbCr'): bit_depth = 8 * len(img.getbands())
        elif img.mode in ('RGBA', 'LA'): bit_depth = 8 * len(img.getbands())
        elif img.mode == 'I': bit_depth = 32 # Integer pixels
        elif img.mode == 'F': bit_depth = 32 # Float pixels

        # Animation Info (for GIFs, WebP etc.)
        animation_info: Optional[Dict[str, Any]] = None
        if getattr(img, 'is_animated', False):
            animation_info = {
                "is_animated": True,
                "frame_count": getattr(img, 'n_frames', 1)
            }
            try:
                # Duration might be a single int (ms per frame) or a tuple/list for APNG/WebP
                duration = img.info.get('duration')
                if duration:
                    animation_info["duration_ms_per_frame"] = duration
                loop = img.info.get('loop') # Number of loops, 0 for infinite
                if loop is not None:
                    animation_info["loop_count"] = loop
            except Exception as e:
                logger.debug(f"Could not get detailed animation info for {file_path}: {e}")

        metadata_to_return = {
            "dimensions": {"width": img.width, "height": img.height},
            "color_mode": img.mode,
            "bit_depth": bit_depth,
            "dpi": f"{dpi[0]}x{dpi[1]}",
            "has_alpha": has_alpha,
            "animation_info": animation_info
        }
        logger.debug(f"[_get_image_metadata] Returning metadata for {file_path}: {metadata_to_return}")
        return metadata_to_return

async def _process_media_probe_data(info: FileInfo, probe_data: Dict[str, Any]) -> None:
    if probe_data.get("format") and probe_data["format"].get("duration"):
        try:
            info["duration"] = float(probe_data["format"]["duration"])
        except (ValueError, TypeError):
            logger.warning(f"Invalid duration value: {probe_data['format'].get('duration')}")
    
    if probe_data.get("format") and probe_data["format"].get("bit_rate"):
        try:
            info["bitrate_kbps"] = int(float(probe_data["format"]["bit_rate"]) / 1000)
        except (ValueError, TypeError):
            logger.warning(f"Invalid bit_rate value: {probe_data['format'].get('bit_rate')}")

    video_stream = next((s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video_stream:
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        if width > 0 and height > 0:
            info["dimensions"] = {"width": width, "height": height}
        
        if codec_name := video_stream.get("codec_name"):
            info["video_codec"] = codec_name
        
        if r_frame_rate := video_stream.get('r_frame_rate'):
            if '/' in r_frame_rate:
                try:
                    num_str, den_str = r_frame_rate.split('/')
                    num, den = int(num_str), int(den_str)
                    if den != 0:
                        info["frame_rate"] = round(num / den, 2)
                except (ValueError, ZeroDivisionError):
                    logger.warning(f"Could not parse r_frame_rate: {r_frame_rate}")
        
        if not info.get("bitrate_kbps") and video_stream.get("bit_rate"):
            try:
                info["bitrate_kbps"] = int(float(video_stream["bit_rate"]) / 1000)
            except (ValueError, TypeError):
                logger.warning(f"Could not parse video stream bit_rate: {video_stream.get('bit_rate')}")

    audio_stream = next((s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio_stream:
        info["has_audio"] = True
        
        if audio_codec := audio_stream.get("codec_name"):
            info["audio_codec"] = audio_codec
        
        if sample_rate_str := audio_stream.get("sample_rate"):
            try:
                info["sample_rate"] = int(sample_rate_str)
            except ValueError:
                logger.warning(f"Could not parse sample_rate: {sample_rate_str}")
        
        if channels_str := audio_stream.get("channels"):
            try:
                info["channels"] = int(channels_str)
            except ValueError:
                logger.warning(f"Could not parse channels: {channels_str}")
        
        if audio_bit_depth := audio_stream.get("bits_per_raw_sample", audio_stream.get("bits_per_sample")):
            try:
                info["bit_depth"] = int(audio_bit_depth)
            except ValueError:
                logger.warning(f"Could not parse audio bit_depth: {audio_bit_depth}")
        
        if not info.get("bitrate_kbps") and audio_stream.get("bit_rate"):
            try:
                info["bitrate_kbps"] = int(float(audio_stream["bit_rate"]) / 1000)
            except (ValueError, TypeError):
                logger.warning(f"Could not parse audio stream bit_rate: {audio_stream.get('bit_rate')}")
    else:
        info["has_audio"] = False

def cleanup():
    _thread_pool.shutdown(wait=True)
    logger.info("Media utilities resources cleaned up")