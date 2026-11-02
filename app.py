import os
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

import pipeline

BASE = Path(os.environ.get("DATA_DIR", "/tmp/recap-data"))
BASE.mkdir(parents=True, exist_ok=True)
PASSWORD = os.environ.get("APP_PASSWORD", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3  # 2 GB

jobs = {}
q = queue.Queue()


def cleanup_old(hours=24):
    now = time.time()
    for d in BASE.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > hours * 3600:
            shutil.rmtree(d, ignore_errors=True)
            jobs.pop(d.name, None)


def worker():
    while True:
        jid = q.get()
        job = jobs[jid]
        job["status"] = "running"

        def cb(pct, msg):
            job["percent"] = pct
            job["message"] = msg
            job["log"].append(f"{pct}% {msg}")

        try:
            d = BASE / jid
            src = job["video"]
            if not src:
                cb(3, "Link ကနေ video ဒေါင်းနေပါတယ်")
                src = pipeline.download_link(job["link"], d)
            out = d / "recap_9x16.mp4"
            pipeline.process(src, d / "work", out, cb, voice=job["voice"],
                             speed=job["speed"], script=job["script"],
                             whisper_model=WHISPER_MODEL)
            shutil.rmtree(d / "work", ignore_errors=True)
            job["status"] = "done"
        except pipeline.PipelineError as e:
            job["status"], job["error"] = "error", str(e)
        except Exception as e:  # unexpected
            traceback.print_exc()
            job["status"], job["error"] = "error", f"မမျှော်လင့်တဲ့ error: {e}"
        finally:
            q.task_done()


threading.Thread(target=worker, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html", need_pw=bool(PASSWORD))


@app.route("/start", methods=["POST"])
def start():
    if PASSWORD and request.form.get("pw") != PASSWORD:
        return jsonify(error="Password မှားနေပါတယ်"), 403
    link = request.form.get("link", "").strip()
    script = request.form.get("script", "")
    f = request.files.get("file")
    if not link and not (f and f.filename):
        return jsonify(error="Video link ထည့်ပါ၊ မဟုတ်ရင် video file ရွေးပါ"), 400
    if link and not link.startswith(("http://", "https://")):
        return jsonify(error="Link က http:// သို့မဟုတ် https:// နဲ့ စရပါမယ်"), 400
    try:
        speed = max(-30, min(30, int(request.form.get("speed", 0))))
    except ValueError:
        speed = 0
    cleanup_old()
    jid = uuid.uuid4().hex[:12]
    d = BASE / jid
    d.mkdir(parents=True)
    video = None
    if f and f.filename:
        video = str(d / ("upload_" + secure_filename(f.filename) if secure_filename(f.filename) else "upload.mp4"))
        f.save(video)
    jobs[jid] = {"status": "queued", "percent": 0, "message": "တန်းစီစောင့်နေပါတယ်",
                 "log": [], "error": None, "video": video, "link": link,
                 "script": script, "voice": request.form.get("voice", "female"),
                 "speed": speed}
    q.put(jid)
    return jsonify(id=jid)


@app.route("/status/<jid>")
def status(jid):
    j = jobs.get(jid)
    if not j:
        return jsonify(error="Job မတွေ့ပါ (server restart ဖြစ်နိုင်ပါတယ်)"), 404
    return jsonify(status=j["status"], percent=j["percent"], message=j["message"],
                   log=j["log"][-30:], error=j["error"],
                   queue_ahead=max(0, q.qsize() - (0 if j["status"] != "queued" else 0)))


@app.route("/video/<jid>")
def video(jid):
    p = BASE / jid / "recap_9x16.mp4"
    if not p.exists():
        return "not found", 404
    return send_file(p, mimetype="video/mp4", conditional=True)


@app.route("/download/<jid>")
def download(jid):
    p = BASE / jid / "recap_9x16.mp4"
    if not p.exists():
        return "not found", 404
    return send_file(p, as_attachment=True, download_name="recap_9x16.mp4")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
