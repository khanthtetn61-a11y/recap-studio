import os
import queue
import re
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


def cleanup_old(hours=48):
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


LINK_HTML = ('<p style="text-align:center;margin:16px 0">'
             '<a href="/videos" style="color:#ff7b95;font-size:1.05rem">📁 ပြီးသား video များကြည့်ရန် / ဒေါင်းရန်</a></p>')

RESUME_JS = """<script>
(function(){
  var of = window.fetch;
  window.fetch = function(u){
    var p = of.apply(this, arguments);
    if (typeof u === 'string' && u.indexOf('/start') === 0) {
      p.then(function(r){ return r.clone().json(); })
       .then(function(j){ if (j && j.id) { try { localStorage.setItem('lastJob', j.id); } catch(e){} } })
       .catch(function(){});
    }
    return p;
  };
  try {
    var id = localStorage.getItem('lastJob');
    if (id) {
      of('/status/' + id).then(function(r){ return r.ok ? r.json() : null; }).then(function(j){
        if (j && j.status !== 'error') {
          document.getElementById('prog').hidden = false;
          document.getElementById('go').disabled = (j.status !== 'done');
          poll(id);
        }
      }).catch(function(){});
    }
  } catch(e){}
})();
</script>"""


@app.route("/")
def index():
    html = render_template("index.html", need_pw=bool(PASSWORD))
    return html.replace("</main>", LINK_HTML + "</main>").replace("</body>", RESUME_JS + "</body>")


JID_RE = re.compile(r"^[0-9a-f]{12}$")


def _auth_ok():
    return (not PASSWORD) or request.headers.get("X-PW", "") == PASSWORD


@app.route("/api/videos")
def api_videos():
    if not _auth_ok():
        return jsonify(error="Password မှားနေပါတယ်"), 403
    items = []
    for d in BASE.iterdir():
        f = d / "recap_9x16.mp4"
        if d.is_dir() and JID_RE.match(d.name) and f.exists():
            st = f.stat()
            items.append({"id": d.name, "mtime": st.st_mtime, "mb": round(st.st_size / 1048576, 1)})
    items.sort(key=lambda x: -x["mtime"])
    return jsonify(items=items)


@app.route("/delete/<jid>", methods=["POST"])
def delete(jid):
    if not _auth_ok():
        return jsonify(error="Password မှားနေပါတယ်"), 403
    if not JID_RE.match(jid):
        return jsonify(error="bad id"), 400
    shutil.rmtree(BASE / jid, ignore_errors=True)
    jobs.pop(jid, None)
    return jsonify(ok=True)


VIDEOS_PAGE = """<!doctype html><html lang="my"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ပြီးသား video များ</title>
<style>
body{margin:0;background:#0f1115;color:#eceef2;font-family:"Noto Sans Myanmar","Myanmar Text",system-ui,sans-serif;line-height:1.6}
main{max-width:520px;margin:0 auto;padding:16px}
.card{background:#181b22;border:1px solid #2a2f3a;border-radius:14px;padding:12px;margin-bottom:12px}
video{width:100%;max-height:60vh;border-radius:10px;background:#000}
a.btn,button{display:block;width:100%;text-align:center;text-decoration:none;padding:12px;border-radius:10px;border:0;
 font-size:1rem;font-weight:600;margin-top:8px;font-family:inherit}
a.dl{background:#2ecc71;color:#06240f} button{background:#2a2f3a;color:#ff9a9a}
small{color:#9aa3b2} a.back{color:#ff7b95}
</style></head><body><main>
<p><a class="back" href="/">← ပြန်သွားရန်</a></p>
<h2>📁 ပြီးသား video များ</h2>
<p><small>Video တွေကို ၄၈ နာရီအထိ သိမ်းထားပါတယ်။ Server restart ဖြစ်ရင် ပျက်သွားနိုင်လို့ ပြီးတာနဲ့ ဒေါင်းထားပါ။</small></p>
<div id="list">ခဏစောင့်ပါ...</div>
<script>
function hdr(){var h={};try{var p=localStorage.getItem('pw');if(p)h['X-PW']=p;}catch(e){}return h;}
async function load(){
  var box=document.getElementById('list');
  var r=await fetch('/api/videos',{headers:hdr()});
  var j=await r.json();
  if(!r.ok){box.textContent='❌ '+(j.error||'error');return;}
  if(!j.items.length){box.textContent='ပြီးသား video မရှိသေးပါ';return;}
  box.innerHTML='';
  j.items.forEach(function(it){
    var d=new Date(it.mtime*1000).toLocaleString();
    var c=document.createElement('div');c.className='card';
    c.innerHTML='<small>'+d+' · '+it.mb+' MB</small>'+
      '<video controls playsinline preload="none" src="/video/'+it.id+'"></video>'+
      '<a class="btn dl" href="/download/'+it.id+'">⬇ Download</a>'+
      '<button data-id="'+it.id+'">🗑 ဖျက်မယ်</button>';
    c.querySelector('button').onclick=async function(){
      if(!confirm('ဒီ video ကို ဖျက်မလား?'))return;
      await fetch('/delete/'+it.id,{method:'POST',headers:hdr()});load();};
    box.appendChild(c);
  });
}
load();
</script></main></body></html>"""


@app.route("/videos")
def videos_page():
    return VIDEOS_PAGE


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
        if JID_RE.match(jid) and (BASE / jid / "recap_9x16.mp4").exists():
            return jsonify(status="done", percent=100, message="ပြီးပါပြီ", log=[], error=None, queue_ahead=0)
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
