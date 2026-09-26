import os
import re
import sys
import time
import shutil
import logging
import threading
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse
import yt_dlp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DownloaderEngine")

# Supported platform identification
def detect_platform(url: str) -> Dict[str, str]:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        if "/shorts/" in u:
            return {"id": "youtube", "name": "YouTube Shorts", "badge": "yt-shorts", "icon": "youtube"}
        return {"id": "youtube", "name": "YouTube", "badge": "youtube", "icon": "youtube"}
    elif "tiktok.com" in u:
        return {"id": "tiktok", "name": "TikTok", "badge": "tiktok", "icon": "tiktok"}
    elif "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        if "/reel/" in u or "/reels/" in u:
            return {"id": "facebook", "name": "Facebook Reel", "badge": "fb-reels", "icon": "facebook"}
        return {"id": "facebook", "name": "Facebook", "badge": "facebook", "icon": "facebook"}
    elif "instagram.com" in u:
        if "/reel/" in u or "/reels/" in u:
            return {"id": "instagram", "name": "Instagram Reel", "badge": "instagram", "icon": "instagram"}
        elif "/stories/" in u:
            return {"id": "instagram", "name": "Instagram Story", "badge": "instagram", "icon": "instagram"}
        return {"id": "instagram", "name": "Instagram", "badge": "instagram", "icon": "instagram"}
    elif "twitter.com" in u or "x.com" in u:
        return {"id": "twitter", "name": "Twitter / X", "badge": "twitter", "icon": "twitter"}
    return {"id": "other", "name": "Web Media", "badge": "other", "icon": "globe"}

def format_bytes(bytes_num: Optional[int]) -> str:
    if not bytes_num or bytes_num <= 0:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"

def format_seconds(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def sanitize_filename(filename: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", filename)

def find_ffmpeg_path() -> Optional[str]:
    # Check current directory / bin directory first
    local_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
    for check_dir in [local_bin, os.getcwd()]:
        exe = os.path.join(check_dir, "ffmpeg.exe")
        if os.path.exists(exe):
            return exe
    # Check system PATH
    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path
    # Check standard winget paths
    winget_patterns = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\**\ffmpeg.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe"
    ]
    import glob
    for p in winget_patterns:
        matches = glob.glob(p, recursive=True)
        if matches:
            return matches[0]
    return None

def fetch_tikwm_data(url: str) -> Optional[Dict[str, Any]]:
    """Fetch 100% watermark-free video data from TikWM API for TikTok videos"""
    try:
        import requests
        api_url = "https://www.tikwm.com/api/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.tikwm.com/"
        }
        params = {"url": url, "hd": 1}
        resp = requests.get(api_url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0 and "data" in data:
                logger.info(f"TikWM API successfully extracted clean stream for: {url}")
                return data["data"]
            else:
                logger.warning(f"TikWM returned status code: {data.get('code')}, msg: {data.get('msg')}")
    except Exception as e:
        logger.warning(f"TikWM API lookup failed: {e}")
    return None

class DownloadTask:
    def __init__(self, task_id: str, url: str, options: Dict[str, Any]):
        self.task_id = task_id
        self.url = url
        self.options = options
        self.remove_watermark = options.get("remove_watermark", True)
        self.status = "queued" # queued, fetching, downloading, processing, completed, failed, cancelled
        self.progress = 0.0
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.speed_str = "0 KB/s"
        self.eta_str = "--:--"
        self.title = "Fetching video details..."
        self.author = ""
        self.thumbnail = ""
        self.duration_str = "--:--"
        self.filename = ""
        self.filepath = ""
        self.file_size_str = ""
        self.error_message = ""
        self.platform = detect_platform(url)
        self.created_at = time.time()
        self.completed_at = 0.0

    def to_dict(self) -> Dict[str, Any]:
        folder = os.path.dirname(self.filepath) if self.filepath else self.options.get("download_dir", "")
        folder_norm = os.path.normpath(folder) if folder else ""
        filepath_norm = os.path.normpath(self.filepath) if self.filepath else ""
        return {
            "task_id": self.task_id,
            "url": self.url,
            "status": self.status,
            "progress": round(self.progress, 1),
            "downloaded_bytes_str": format_bytes(self.downloaded_bytes),
            "total_bytes_str": format_bytes(self.total_bytes),
            "speed_str": self.speed_str,
            "eta_str": self.eta_str,
            "title": self.title,
            "author": self.author,
            "thumbnail": self.thumbnail,
            "duration_str": self.duration_str,
            "filename": self.filename or (os.path.basename(self.filepath) if self.filepath else ""),
            "filepath": filepath_norm,
            "folder": folder_norm,
            "file_size_str": self.file_size_str,
            "error_message": self.error_message,
            "platform": self.platform,
            "remove_watermark": self.remove_watermark,
            "created_at": self.created_at,
            "completed_at": self.completed_at
        }


class DownloaderEngine:
    def __init__(self, default_download_dir: Optional[str] = None):
        if not default_download_dir:
            # Default to user's standard Downloads/MediaDownloader folder
            user_downloads = os.path.join(os.path.expanduser("~"), "Downloads", "MediaDownloader")
            self.download_dir = user_downloads
        else:
            self.download_dir = default_download_dir

        os.makedirs(self.download_dir, exist_ok=True)
        self.tasks: Dict[str, DownloadTask] = {}
        self.ffmpeg_path = find_ffmpeg_path()
        logger.info(f"Initialized DownloaderEngine with target dir: {self.download_dir}")
        if self.ffmpeg_path:
            logger.info(f"FFmpeg located at: {self.ffmpeg_path}")
        else:
            logger.warning("FFmpeg not found yet. yt-dlp will use standard stream merging if available.")

    def set_download_dir(self, new_dir: str):
        if new_dir:
            os.makedirs(new_dir, exist_ok=True)
            self.download_dir = new_dir

    def _download_direct_stream(self, stream_url: str, dest_filepath: str, task: DownloadTask):
        """Direct stream downloader with real-time progress, speed, and ETA tracking"""
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.tikwm.com/"
        }
        with requests.get(stream_url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_length = r.headers.get("content-length")
            total = int(total_length) if total_length and total_length.isdigit() else 0
            task.total_bytes = total
            downloaded = 0
            start_time = time.time()
            last_update = start_time
            task.status = "downloading"
            
            with open(dest_filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        task.downloaded_bytes = downloaded
                        now = time.time()
                        if now - last_update >= 0.25:
                            elapsed = max(0.001, now - start_time)
                            speed = downloaded / elapsed
                            task.speed_str = f"{format_bytes(int(speed))}/s"
                            if total > 0:
                                task.progress = min(99.0, (downloaded / total) * 100.0)
                                remaining = max(0, total - downloaded)
                                eta = remaining / speed if speed > 0 else 0
                                task.eta_str = format_seconds(int(eta))
                            else:
                                task.progress = min(95.0, task.progress + 1.0)
                            last_update = now

    def extract_info(self, url: str) -> Dict[str, Any]:
        """Fetch video metadata and available qualities without downloading"""
        platform = detect_platform(url)

        # Fast unwatermarked metadata extraction for TikTok via TikWM API
        if platform["id"] == "tiktok":
            tik_data = fetch_tikwm_data(url)
            if tik_data:
                title = tik_data.get("title") or "TikTok Video"
                thumbnail = tik_data.get("cover") or ""
                duration = tik_data.get("duration") or 0
                author_obj = tik_data.get("author") or {}
                author = author_obj.get("nickname") or author_obj.get("unique_id") or "TikTok Creator"
                play_count = tik_data.get("play_count")
                
                formats = [
                    {
                        "format_id": "best",
                        "label": "Original HD Quality - 100% Watermark-Free (Recommended)",
                        "resolution": "HD Clean",
                        "note": "Ultra Clean MP4, No Watermark"
                    },
                    {
                        "format_id": "clean_sd",
                        "label": "Standard Quality - Watermark-Free",
                        "resolution": "Clean SD",
                        "note": "Smooth MP4, No Watermark"
                    },
                    {
                        "format_id": "mp3_high",
                        "label": "Audio Only - MP3 (Original Sound)",
                        "resolution": "Audio",
                        "note": "Crystal Clear 320 kbps Audio"
                    }
                ]
                return {
                    "is_playlist": False,
                    "title": title,
                    "thumbnail": thumbnail,
                    "duration": duration,
                    "duration_str": format_seconds(duration),
                    "author": author,
                    "views": f"{play_count:,}" if play_count else None,
                    "platform": platform,
                    "formats": formats,
                    "webpage_url": url,
                    "tikwm_data": tik_data,
                    "is_watermark_free": True
                }

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"]
                }
            }
        }
        if platform["id"] == "tiktok":
            ydl_opts["extractor_args"]["tiktok"] = {
                "api_hostname": "api22-normal-c-useast1a.tiktokv.com"
            }
        if self.ffmpeg_path:
            ydl_opts["ffmpeg_location"] = os.path.dirname(self.ffmpeg_path)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.error(f"Error extracting info for {url}: {e}")
                raise RuntimeError(f"Could not retrieve video information: {str(e)}")

        if not info:
            raise RuntimeError("No media stream found at this URL.")

        # Check if playlist or single video
        if "entries" in info and info["entries"]:
            # It's a playlist
            entries_count = len(list(info["entries"]))
            title = info.get("title") or "Playlist"
            first_entry = info["entries"][0] if info["entries"] else {}
            thumb = first_entry.get("thumbnail") or info.get("thumbnail") or ""
            return {
                "is_playlist": True,
                "title": title,
                "entries_count": entries_count,
                "thumbnail": thumb,
                "platform": platform,
                "author": info.get("uploader") or info.get("channel") or "Unknown Channel",
                "formats": [
                    {"format_id": "best", "label": "Best Video Quality (Clean / No Watermark)", "resolution": "Best"},
                    {"format_id": "1080", "label": "Full HD 1080p (Clean)", "resolution": "1080p"},
                    {"format_id": "720", "label": "HD 720p (Clean)", "resolution": "720p"},
                    {"format_id": "mp3", "label": "Audio MP3 (320 kbps)", "resolution": "Audio"}
                ]
            }

        title = info.get("title") or "Unknown Title"
        thumbnail = info.get("thumbnail") or ""
        duration = info.get("duration") or 0
        author = info.get("uploader") or info.get("channel") or info.get("creator") or "Unknown Creator"
        view_count = info.get("view_count")

        # Parse available format options
        formats_raw = info.get("formats") or []
        available_resolutions = set()
        for f in formats_raw:
            h = f.get("height")
            if h and isinstance(h, int):
                available_resolutions.add(h)

        quality_options = []
        if platform["id"] == "tiktok":
            quality_options.append({
                "format_id": "best",
                "label": "Original Video Quality (Watermark-Free)",
                "resolution": "Clean HD",
                "note": "Clean video without watermark"
            })
        else:
            quality_options.append({
                "format_id": "best",
                "label": "Best Available Quality (Recommended - Clean)",
                "resolution": "Best",
                "note": "Highest quality video + audio"
            })

        standard_heights = [
            (2160, "4K Ultra HD (2160p)"),
            (1440, "2K Quad HD (1440p)"),
            (1080, "Full HD (1080p)"),
            (720, "HD (720p)"),
            (480, "SD (480p)"),
            (360, "Low (360p)")
        ]

        for h, label in standard_heights:
            # If resolution exists or if we have video streams
            if any(res >= h for res in available_resolutions) or not available_resolutions:
                quality_options.append({
                    "format_id": str(h),
                    "label": f"{label} (Clean)",
                    "resolution": f"{h}p",
                    "note": "MP4 Video"
                })

        # Audio options
        quality_options.append({
            "format_id": "mp3_high",
            "label": "Audio Only - MP3 (320 kbps)",
            "resolution": "Audio",
            "note": "Crystal Clear Audio"
        })
        quality_options.append({
            "format_id": "mp3_standard",
            "label": "Audio Only - MP3 (192 kbps)",
            "resolution": "Audio",
            "note": "Standard Audio"
        })
        quality_options.append({
            "format_id": "m4a",
            "label": "Audio Only - M4A / AAC",
            "resolution": "Audio",
            "note": "Apple / Mobile Friendly"
        })

        return {
            "is_playlist": False,
            "title": title,
            "thumbnail": thumbnail,
            "duration": duration,
            "duration_str": format_seconds(duration),
            "author": author,
            "views": f"{view_count:,}" if view_count else None,
            "platform": platform,
            "formats": quality_options,
            "webpage_url": info.get("webpage_url") or url
        }


    def _progress_hook(self, task: DownloadTask, d: Dict[str, Any]):
        try:
            status = d.get("status")
            if status == "downloading":
                task.status = "downloading"
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                task.downloaded_bytes = downloaded
                task.total_bytes = total

                if total > 0:
                    task.progress = (downloaded / total) * 100.0
                else:
                    # Fallback for streams without total length
                    pct = d.get("_percent_str")
                    if pct:
                        clean_pct = re.sub(r"[^\d.]", "", pct)
                        if clean_pct:
                            task.progress = float(clean_pct)

                speed = d.get("speed")
                if speed:
                    task.speed_str = f"{format_bytes(int(speed))}/s"
                else:
                    task.speed_str = d.get("_speed_str") or "Calculating..."

                eta = d.get("eta")
                if eta:
                    task.eta_str = format_seconds(int(eta))
                else:
                    task.eta_str = d.get("_eta_str") or "--:--"

            elif status == "finished":
                task.status = "processing"
                task.progress = 99.0
                task.speed_str = "Merging streams..."
                task.eta_str = "Finishing..."
                filename = d.get("filename")
                if filename:
                    task.filename = os.path.basename(filename)
                    task.filepath = filename
        except Exception as e:
            logger.warning(f"Error in progress hook: {e}")

    def execute_download(self, task: DownloadTask):
        """Worker function running in a separate thread"""
        task.status = "fetching"
        url = task.url
        opts = task.options
        format_choice = opts.get("format_id", "best")
        target_dir = opts.get("download_dir", self.download_dir)
        remove_watermark = opts.get("remove_watermark", True)
        os.makedirs(target_dir, exist_ok=True)

        is_audio = format_choice.startswith("mp3") or format_choice == "m4a"
        platform = task.platform

        # -------------------------------------------------------------
        # STEP 1: Direct Unwatermarked Stream Download for TikTok (TikWM)
        # -------------------------------------------------------------
        if platform["id"] == "tiktok" and remove_watermark:
            try:
                tik_data = opts.get("tikwm_data") or fetch_tikwm_data(url)
                if tik_data:
                    task.title = tik_data.get("title") or task.title or "TikTok Video"
                    author_obj = tik_data.get("author") or {}
                    task.author = author_obj.get("nickname") or author_obj.get("unique_id") or task.author
                    task.thumbnail = tik_data.get("cover") or task.thumbnail
                    task.duration_str = format_seconds(tik_data.get("duration"))

                    stream_url = None
                    if is_audio:
                        stream_url = tik_data.get("music")
                        ext = "mp3"
                    elif format_choice == "clean_sd":
                        stream_url = tik_data.get("play") or tik_data.get("hdplay")
                        ext = "mp4"
                    else: # best or clean_hd
                        stream_url = tik_data.get("hdplay") or tik_data.get("play")
                        ext = "mp4"

                    if stream_url:
                        safe_title = sanitize_filename(task.title)[:70].strip() or "TikTok_Video"
                        filename = f"{safe_title} [Clean].{ext}"
                        filepath = os.path.join(target_dir, filename)

                        # Avoid filename collisions
                        counter = 1
                        base_name, file_ext = os.path.splitext(filename)
                        while os.path.exists(filepath):
                            filename = f"{base_name}_{counter}{file_ext}"
                            filepath = os.path.join(target_dir, filename)
                            counter += 1

                        logger.info(f"Downloading unwatermarked stream for {url} via TikWM to {filepath}")
                        self._download_direct_stream(stream_url, filepath, task)

                        task.filename = filename
                        task.filepath = filepath
                        task.file_size_str = format_bytes(os.path.getsize(filepath))
                        task.status = "completed"
                        task.progress = 100.0
                        task.completed_at = time.time()
                        task.speed_str = "Done"
                        task.eta_str = "00:00"
                        logger.info(f"TikWM unwatermarked download successful: {filepath}")
                        return
            except Exception as e:
                logger.warning(f"TikWM stream download failed, continuing with yt-dlp clean extractor: {e}")

        # -------------------------------------------------------------
        # STEP 2: yt-dlp Clean / Filtered Download
        # -------------------------------------------------------------
        outtmpl = os.path.join(target_dir, "%(title)s [%(id)s].%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [lambda d: self._progress_hook(task, d)],
            "noplaylist": not opts.get("download_playlist", False),
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"]
                }
            }
        }

        if platform["id"] == "tiktok":
            ydl_opts["extractor_args"]["tiktok"] = {
                "api_hostname": "api22-normal-c-useast1a.tiktokv.com"
            }
        elif platform["id"] == "instagram":
            ydl_opts["http_headers"] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Mode": "navigate"
            }

        # Check and set FFmpeg
        current_ffmpeg = self.ffmpeg_path or find_ffmpeg_path()
        if current_ffmpeg:
            ydl_opts["ffmpeg_location"] = os.path.dirname(current_ffmpeg)

        if is_audio:
            if format_choice == "mp3_high":
                bitrate = "320"
            elif format_choice == "mp3_standard":
                bitrate = "192"
            else:
                bitrate = "192"

            ydl_opts["format"] = "bestaudio/best"
            if current_ffmpeg:
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3" if "mp3" in format_choice else "m4a",
                    "preferredquality": bitrate,
                }]
        else:
            if remove_watermark:
                if platform["id"] == "tiktok":
                    # Exclude watermarked streams and download_addr explicitly
                    ydl_opts["format"] = (
                        "bestvideo[format_id!*=download_addr][format_note!*=watermark]+bestaudio/"
                        "best[format_id!*=download_addr][format_note!*=watermark]/"
                        "best[format_id!*=download_addr]/"
                        "best"
                    )
                else:
                    ydl_opts["format"] = (
                        "bestvideo[format_note!*=watermark][format_note!*=watermarked][format_id!*=watermarked][ext=mp4]+bestaudio[ext=m4a]/"
                        "bestvideo[format_note!*=watermark][format_note!*=watermarked][format_id!*=watermarked]+bestaudio/"
                        "best[format_note!*=watermark][format_note!*=watermarked][format_id!*=watermarked]/"
                        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
                    )
            else:
                # Standard format selection
                if format_choice == "best":
                    ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
                elif format_choice.isdigit():
                    max_h = int(format_choice)
                    ydl_opts["format"] = f"bestvideo[height<={max_h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={max_h}]+bestaudio/best[height<={max_h}]/best"
                else:
                    ydl_opts["format"] = "bestvideo+bestaudio/best"

            if current_ffmpeg:
                ydl_opts["merge_output_format"] = "mp4"
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegVideoRemuxer",
                    "preferedformat": "mp4"
                }]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # First extract info to set title and thumbnail
                info = ydl.extract_info(url, download=False)
                if info:
                    task.title = info.get("title") or "Unknown Title"
                    task.author = info.get("uploader") or info.get("channel") or ""
                    task.thumbnail = info.get("thumbnail") or ""
                    task.duration_str = format_seconds(info.get("duration"))

                # Now download
                task.status = "downloading"
                res = ydl.download([url])

                task.status = "completed"
                task.progress = 100.0
                task.completed_at = time.time()
                task.speed_str = "Done"
                task.eta_str = "00:00"

                # Robust final output file resolution on disk
                final_path = None

                # Check if current task.filepath exists
                if task.filepath and os.path.exists(task.filepath):
                    final_path = task.filepath

                # Check ydl.prepare_filename(info) and its common extensions
                if not final_path and info:
                    guessed_name = ydl.prepare_filename(info)
                    base_guess = os.path.splitext(guessed_name)[0]
                    candidates = [guessed_name]
                    if is_audio:
                        candidates.extend([base_guess + ".mp3", base_guess + ".m4a", base_guess + ".aac", base_guess + ".opus"])
                    else:
                        candidates.extend([base_guess + ".mp4", base_guess + ".mkv", base_guess + ".webm"])
                    for cand in candidates:
                        if os.path.exists(cand):
                            final_path = cand
                            break

                # If task.filepath had an intermediate format ID like .f137.mp4
                if not final_path and task.filepath:
                    clean_cand = re.sub(r'\.f\d+\.', '.', task.filepath)
                    if os.path.exists(clean_cand):
                        final_path = clean_cand
                    else:
                        base_cand = os.path.splitext(re.sub(r'\.f\d+', '', task.filepath))[0]
                        for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
                            if os.path.exists(base_cand + ext):
                                final_path = base_cand + ext
                                break

                # Scan target_dir for the most recently created/modified file (within 5 minutes)
                if not final_path and os.path.exists(target_dir):
                    try:
                        files = [
                            os.path.join(target_dir, f) for f in os.listdir(target_dir)
                            if not f.endswith(".part") and not f.endswith(".ytdl") and not f.endswith(".tmp")
                        ]
                        if files:
                            files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                            # Check if the newest file was modified recently
                            if files and (time.time() - os.path.getmtime(files[0]) < 300):
                                final_path = files[0]
                    except Exception as scan_err:
                        logger.warning(f"Error scanning target_dir for output: {scan_err}")

                if final_path and os.path.exists(final_path):
                    task.filepath = os.path.abspath(final_path)
                    task.filename = os.path.basename(final_path)
                    task.file_size_str = format_bytes(os.path.getsize(final_path))
                    logger.info(f"Verified downloaded file on disk: {task.filepath} ({task.file_size_str})")
                else:
                    logger.warning(f"Could not locate exact final file on disk for task {task.task_id} in {target_dir}")

        except Exception as e:
            logger.error(f"Download failed for {url}: {e}", exc_info=True)
            task.status = "failed"
            task.error_message = str(e)

    def start_download_async(self, task_id: str, url: str, options: Dict[str, Any]) -> DownloadTask:
        task = DownloadTask(task_id, url, options)
        self.tasks[task_id] = task
        thread = threading.Thread(target=self.execute_download, args=(task,), daemon=True)
        thread.start()
        return task

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in sorted(self.tasks.values(), key=lambda x: x.created_at, reverse=True)]

    def get_completed_history(self) -> List[Dict[str, Any]]:
        completed = []
        for t in self.tasks.values():
            if t.status == "completed":
                completed.append(t.to_dict())
        return completed

    def batch_inspect_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Extract metadata for multiple URLs concurrently without downloading"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = []
        
        def _inspect_one(idx: int, u: str) -> Dict[str, Any]:
            try:
                info = self.extract_info(u)
                info["index"] = idx
                info["url"] = u
                return info
            except Exception as err:
                logger.warning(f"Batch inspection error for {u}: {err}")
                p = detect_platform(u)
                return {
                    "index": idx,
                    "url": u,
                    "error": str(err),
                    "title": u,
                    "author": "Online Video",
                    "thumbnail": "",
                    "duration_str": "--:--",
                    "platform": p,
                    "formats": [
                        {"format_id": "best", "label": "Best Video Quality (Clean)", "resolution": "Best"},
                        {"format_id": "mp3_high", "label": "Audio Only - MP3 (320 kbps)", "resolution": "Audio"}
                    ]
                }

        max_workers = min(6, max(1, len(urls)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {executor.submit(_inspect_one, i, url): i for i, url in enumerate(urls)}
            for future in as_completed(future_to_idx):
                results.append(future.result())

        # Sort results back to original input order
        results.sort(key=lambda x: x.get("index", 0))
        return results


class BatchQueueManager:
    """Manages bulk downloads with worker queue"""
    def __init__(self, engine: DownloaderEngine, max_workers: int = 2):
        self.engine = engine
        self.max_workers = max_workers
        self.queue: List[str] = [] # List of task_ids
        self.active_count = 0
        self.lock = threading.Lock()
        self._stop = False
        self.worker_thread = threading.Thread(target=self._run_queue, daemon=True)
        self.worker_thread.start()

    def add_batch(self, urls: List[str], default_format: str = "best", download_dir: Optional[str] = None, remove_watermark: bool = True) -> List[DownloadTask]:
        created_tasks = []
        for u in urls:
            u_clean = u.strip()
            if not u_clean:
                continue
            import uuid
            task_id = f"batch_{uuid.uuid4().hex[:8]}"
            opts = {
                "format_id": default_format,
                "download_dir": download_dir or self.engine.download_dir,
                "remove_watermark": remove_watermark
            }
            task = DownloadTask(task_id, u_clean, opts)
            self.engine.tasks[task_id] = task
            with self.lock:
                self.queue.append(task_id)
            created_tasks.append(task)
        return created_tasks

    def _run_queue(self):
        while not self._stop:
            task_to_run = None
            with self.lock:
                if self.queue and self.active_count < self.max_workers:
                    task_id = self.queue.pop(0)
                    task_to_run = self.engine.get_task(task_id)
                    if task_to_run:
                        self.active_count += 1

            if task_to_run:
                threading.Thread(target=self._process_task, args=(task_to_run,), daemon=True).start()
            else:
                time.sleep(0.5)

    def _process_task(self, task: DownloadTask):
        try:
            self.engine.execute_download(task)
        finally:
            with self.lock:
                self.active_count = max(0, self.active_count - 1)
