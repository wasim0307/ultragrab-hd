import os
import sys
import json
import time
import uuid
import socket
import threading
import subprocess

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from flask import Flask, render_template, request, jsonify, Response, send_file
from downloader_engine import DownloaderEngine, BatchQueueManager, find_ffmpeg_path

if getattr(sys, "frozen", False):
    bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    app = Flask(__name__, 
                template_folder=os.path.join(bundle_dir, "templates"),
                static_folder=os.path.join(bundle_dir, "static"))
else:
    app = Flask(__name__)

public_tunnel_url = None

def get_local_ips():
    """Find all potential local LAN IP addresses (Wi-Fi / Ethernet), prioritized"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            ips.append(ip)
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None):
            addr = item[4][0]
            if ":" not in addr and not addr.startswith("127.") and addr not in ips:
                ips.append(addr)
    except Exception:
        pass

    def ip_priority(ip_str):
        if ip_str.startswith("192.168."):
            return 1
        if ip_str.startswith("10."):
            return 2
        if ip_str.startswith("172."):
            return 3
        return 4

    ips.sort(key=ip_priority)
    return ips if ips else ["127.0.0.1"]

def get_local_ip() -> str:
    return get_local_ips()[0]

def start_public_tunnel(port: int = 5055):
    """Starts a free Cloudflare Tunnel so the app can be accessed from ANY phone/4G data worldwide, with auto-reconnect"""
    global public_tunnel_url
    while True:
        try:
            from pycloudflared import try_cloudflare
            tunnel = try_cloudflare(port=port)
            public_tunnel_url = tunnel.tunnel
            print(f"\n========================================================")
            print(f"  ★ ULTRAGRAB HD UNIVERSAL LINK (PC & MOBILE DONO KE LIYE):")
            print(f"  >> {public_tunnel_url}")
            print(f"  (Yeh aik hi link Computer aur Mobile dono par chalega)")
            print(f"========================================================\n")

            # Save latest active link to MOBILE_LINK.txt for easy reference
            try:
                base_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
                for fname in ["MOBILE_LINK.txt", "SAVE_PANDA_LINK.txt"]:
                    txt_path = os.path.join(base_dir, fname)
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write("=== ULTRAGRAB HD UNIVERSAL LINK (PC & MOBILE DONO KE LIYE) ===\n\n")
                        f.write(f"👉 {public_tunnel_url}\n\n")
                        f.write("Yeh AIK HI LINK aap apne Computer ke browser me bhi khol sakte hain aur Mobile phone (4G/5G/Wi-Fi) me bhi!\n")
                        f.write(f"Local Wi-Fi Link: http://{get_local_ip()}:{port}\n")
            except Exception:
                pass

            # Monitor tunnel process so if edge drops, it auto-reconnects
            if hasattr(tunnel, "process") and tunnel.process:
                tunnel.process.wait()
            else:
                break
        except Exception as e:
            print(f"[*] Cloudflare Tunnel note: {e}")
            time.sleep(5)

# Initialize engine & batch manager
engine = DownloaderEngine()
batch_manager = BatchQueueManager(engine, max_workers=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/network-info", methods=["GET"])
def get_network_info():
    port = 5055
    all_ips = get_local_ips()
    ip = all_ips[0]
    return jsonify({
        "ip": ip,
        "all_ips": all_ips,
        "port": port,
        "local_url": f"http://localhost:{port}",
        "mobile_url": f"http://{ip}:{port}",
        "all_mobile_urls": [f"http://{i}:{port}" for i in all_ips],
        "public_url": public_tunnel_url,
        "unified_url": public_tunnel_url or f"http://{ip}:{port}"
    })


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Please provide a valid video URL."}), 400

    try:
        info = engine.extract_info(url)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/batch-inspect", methods=["POST"])
def batch_inspect_videos():
    data = request.get_json() or {}
    urls = data.get("urls", [])
    if not urls or not isinstance(urls, list):
        return jsonify({"error": "Please provide a list of video URLs."}), 400

    clean_urls = [u.strip() for u in urls if u and u.strip()]
    if not clean_urls:
        return jsonify({"error": "No valid URLs provided."}), 400

    clean_urls = clean_urls[:50]
    results = engine.batch_inspect_urls(clean_urls)
    return jsonify({
        "count": len(results),
        "items": results
    })

@app.route("/api/download", methods=["POST"])
def start_single_download():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    format_id = data.get("format_id", "best")
    download_dir = data.get("download_dir", engine.download_dir)
    remove_watermark = data.get("remove_watermark", True)

    if not url:
        return jsonify({"error": "Please provide a valid video URL."}), 400

    task_id = f"single_{uuid.uuid4().hex[:8]}"
    options = {
        "format_id": format_id,
        "download_dir": download_dir,
        "remove_watermark": remove_watermark
    }
    task = engine.start_download_async(task_id, url, options)
    return jsonify(task.to_dict())

@app.route("/api/batch-download", methods=["POST"])
def start_batch_download():
    data = request.get_json() or {}
    urls = data.get("urls", [])
    format_id = data.get("format_id", "best")
    download_dir = data.get("download_dir", engine.download_dir)
    remove_watermark = data.get("remove_watermark", True)

    if not urls or not isinstance(urls, list):
        return jsonify({"error": "Please provide at least one URL for batch download."}), 400

    clean_urls = [u.strip() for u in urls if u and u.strip()]
    if not clean_urls:
        return jsonify({"error": "No valid URLs provided."}), 400

    created_tasks = batch_manager.add_batch(clean_urls, default_format=format_id, download_dir=download_dir, remove_watermark=remove_watermark)
    return jsonify({
        "message": f"Successfully queued {len(created_tasks)} items.",
        "tasks": [t.to_dict() for t in created_tasks]
    })

@app.route("/api/task/<task_id>", methods=["GET"])
def get_task_status(task_id):
    task = engine.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found."}), 404
    return jsonify(task.to_dict())

@app.route("/api/tasks", methods=["GET"])
def get_all_tasks():
    return jsonify(engine.get_all_tasks())

@app.route("/api/stream/<task_id>")
def stream_task_progress(task_id):
    """Server-Sent Events stream for instant real-time progress update"""
    def event_stream():
        last_progress = -1
        last_status = ""
        while True:
            task = engine.get_task(task_id)
            if not task:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                break
            
            # Send update if state or progress changed
            task_dict = task.to_dict()
            yield f"data: {json.dumps(task_dict)}\n\n"

            if task.status in ["completed", "failed", "cancelled"]:
                break
            time.sleep(0.5)

    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(engine.get_completed_history())

@app.route("/api/open-folder", methods=["GET", "POST"])
def open_folder():
    data = {}
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict() or {}
    else:
        data = request.args.to_dict()

    filepath = data.get("filepath", "").strip()
    folder = data.get("folder", "").strip() or engine.download_dir
    
    try:
        # Check if filepath exists directly
        if filepath and os.path.exists(filepath):
            norm_filepath = os.path.normpath(os.path.abspath(filepath))
            if sys.platform == "win32":
                try:
                    # Windows explorer with /select requires raw list without shell=True to handle () and spaces in video names
                    subprocess.Popen(["explorer.exe", f"/select,{norm_filepath}"])
                except Exception:
                    os.startfile(os.path.dirname(norm_filepath))
            else:
                target_folder = os.path.dirname(norm_filepath)
                subprocess.run(["xdg-open", target_folder])
            return jsonify({"success": True, "filepath": norm_filepath, "folder": os.path.dirname(norm_filepath)})

        # If filepath was given but not found at exact path, check if filename exists inside engine.download_dir
        if filepath:
            filename = os.path.basename(filepath)
            alt_path = os.path.join(engine.download_dir, filename)
            if os.path.exists(alt_path):
                norm_filepath = os.path.normpath(os.path.abspath(alt_path))
                if sys.platform == "win32":
                    try:
                        subprocess.Popen(["explorer.exe", f"/select,{norm_filepath}"])
                    except Exception:
                        os.startfile(os.path.dirname(norm_filepath))
                else:
                    subprocess.run(["xdg-open", os.path.dirname(norm_filepath)])
                return jsonify({"success": True, "filepath": norm_filepath, "folder": os.path.dirname(norm_filepath)})

        target_dir = os.path.normpath(os.path.abspath(folder))
        os.makedirs(target_dir, exist_ok=True)
        if sys.platform == "win32":
            try:
                os.startfile(target_dir)
            except Exception:
                subprocess.Popen(["explorer.exe", target_dir])
        else:
            subprocess.run(["xdg-open", target_dir])
        return jsonify({"success": True, "folder": target_dir})
    except Exception as e:
        return jsonify({"error": f"Failed to open folder: {str(e)}"}), 500

@app.route("/api/play-file", methods=["GET", "POST"])
def play_file():
    data = {}
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict() or {}
    else:
        data = request.args.to_dict()

    filepath = data.get("filepath", "").strip()
    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "File not found on disk."}), 404
    try:
        norm_path = os.path.normpath(filepath)
        if sys.platform == "win32":
            try:
                os.startfile(norm_path)
            except Exception:
                subprocess.Popen(f'cmd /c start "" "{norm_path}"', shell=True)
        else:
            subprocess.run(["xdg-open", norm_path])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/download-file/<task_id>", methods=["GET"])
def download_file_to_device(task_id):
    """Directly delivers the downloaded video file to the user's mobile browser or PC with explicit MP4 MIME type for Phone Gallery indexing"""
    task = engine.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found."}), 404

    target_file = task.filepath
    if not target_file or not os.path.exists(target_file):
        # Fallback: scan engine.download_dir for matching task title/id or newest file
        if os.path.exists(engine.download_dir):
            try:
                candidates = [
                    os.path.join(engine.download_dir, f) for f in os.listdir(engine.download_dir)
                    if not f.endswith(".part") and not f.endswith(".ytdl") and not f.endswith(".tmp")
                ]
                if candidates:
                    candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                    if task.filename:
                        for c in candidates:
                            if os.path.basename(c).lower() == task.filename.lower():
                                target_file = c
                                break
                    if (not target_file or not os.path.exists(target_file)) and candidates:
                        target_file = candidates[0]
            except Exception:
                pass

    if not target_file or not os.path.exists(target_file):
        return jsonify({"error": "File is still downloading or does not exist on disk."}), 404
    
    task.filepath = target_file
    filename = task.filename or os.path.basename(target_file)
    ext = os.path.splitext(filename)[1].lower()
    
    # Strictly set explicit MIME type so mobile OS media scanner indexes into Gallery / Photos
    if ext in [".mp3", ".m4a", ".aac", ".wav"]:
        mimetype = "audio/mpeg" if ext == ".mp3" else "audio/mp4"
    else:
        mimetype = "video/mp4"
        if not filename.lower().endswith(".mp4"):
            filename += ".mp4"
        
    response = send_file(target_file, as_attachment=True, download_name=filename, mimetype=mimetype, conditional=True)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Type"] = mimetype
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@app.route("/api/download-path-file", methods=["GET"])
def download_path_file():
    """Download any file from library to phone/device by filepath with explicit media MIME"""
    filepath = request.args.get("filepath", "").strip()
    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "File not found on disk."}), 404
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1].lower()
    mimetype = "audio/mpeg" if ext == ".mp3" else "video/mp4"
    response = send_file(filepath, as_attachment=True, download_name=filename, mimetype=mimetype, conditional=True)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Type"] = mimetype
    return response

@app.route("/api/stream-video/<task_id>", methods=["GET"])
def stream_video(task_id):
    """Streams video for HTML5 in-browser player on mobile & desktop with Range request support"""
    task = engine.get_task(task_id)
    if not task or not task.filepath or not os.path.exists(task.filepath):
        return jsonify({"error": "Video not found or still processing."}), 404
    filename = task.filename or os.path.basename(task.filepath)
    ext = os.path.splitext(filename)[1].lower()
    mimetype = "audio/mpeg" if ext == ".mp3" else "video/mp4"
    return send_file(task.filepath, mimetype=mimetype, as_attachment=False, conditional=True)

@app.route("/api/settings", methods=["GET", "POST"])
def settings_handler():
    if request.method == "POST":
        data = request.get_json() or {}
        new_dir = data.get("download_dir", "").strip()
        if new_dir:
            try:
                engine.set_download_dir(new_dir)
            except Exception as e:
                return jsonify({"error": f"Invalid directory: {e}"}), 400
        return jsonify({
            "success": True,
            "download_dir": engine.download_dir,
            "ffmpeg_available": bool(find_ffmpeg_path())
        })

    # GET
    ffmpeg_exe = find_ffmpeg_path()
    return jsonify({
        "download_dir": engine.download_dir,
        "ffmpeg_available": bool(ffmpeg_exe),
        "ffmpeg_path": ffmpeg_exe or "Not Installed (1080p+ merging may be limited)"
    })

if __name__ == "__main__":
    # Support cloud environment PORT (Render, Railway, Heroku) or default 5055
    port = int(os.environ.get("PORT", 5055))
    is_cloud = bool(os.environ.get("PORT") and (os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("DYNO") or not sys.platform.startswith("win")))

    if is_cloud:
        print(f"\n[*] UltraGrab HD is RUNNING IN CLOUD 24/7 on port {port}!")
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        import webbrowser
        all_ips = get_local_ips()
        local_ip = all_ips[0]

        # Start public tunnel for global mobile access in background
        threading.Thread(target=start_public_tunnel, args=(port,), daemon=True).start()

        print(f"\n========================================================")
        print(f"  UltraGrab HD Universal Video Downloader is RUNNING!")
        print(f"  [PC]     Computer Link : http://127.0.0.1:{port}")
        print(f"  [Mobile] Wi-Fi Link    : http://{local_ip}:{port}")
        if len(all_ips) > 1:
            for alt in all_ips[1:]:
                print(f"  [Mobile] Alt Wi-Fi Link: http://{alt}:{port}")
        print(f"========================================================")
        print(f"  Tip: Mobile phone me Wi-Fi ya Online link open karein!\n")

        # Auto-open browser on computer after 1.2 seconds
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        app.run(host="0.0.0.0", port=port, debug=False)


