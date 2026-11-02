"""Movie recap pipeline: video -> Burmese voice -> 9:16 (1080x1920) with audio/video in sync."""
import asyncio
import glob
import math
import re
import subprocess
import sys
import time
from pathlib import Path

W, H, FPS = 1080, 1920, 30
VOICES = {
    "female": "my-MM-NilarNeural",
    "male": "my-MM-ThihaNeural",
}
MAX_VIDEO_SLOW = 2.0   # slow the video down at most 2x to wait for the voice
MAX_AUDIO_FAST = 1.6   # speed the voice up at most 1.6x if still too long


class PipelineError(Exception):
    pass


def run(cmd, timeout=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise PipelineError(f"'{cmd[0]}' မတွေ့ပါ (server မှာ install လုပ်ထားရမယ်)")
    except subprocess.TimeoutExpired:
        raise PipelineError(f"'{cmd[0]}' အချိန်ကြာလွန်းလို့ ရပ်လိုက်ပါတယ်")
    if p.returncode != 0:
        raise PipelineError(f"{cmd[0]} error: {p.stderr[-700:]}")
    return p.stdout


def probe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def has_video(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", str(path)])
    return "video" in out


# ---------------------------------------------------------------- input
def download_link(url, outdir, timeout=900):
    outdir = Path(outdir)
    run([sys.executable, "-m", "yt_dlp", "--no-playlist", "-f",
         "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
         "-o", str(outdir / "source.%(ext)s"), url], timeout=timeout)
    files = glob.glob(str(outdir / "source.*"))
    if not files:
        raise PipelineError("Video download မရပါ။ Video file ကို တိုက်ရိုက် upload လုပ်ပါ")
    return files[0]


# ---------------------------------------------------------------- speech -> text -> Burmese
def transcribe(path, workdir, model_size="base"):
    wav = str(Path(workdir) / "speech.wav")
    run(["ffmpeg", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", wav])
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise PipelineError("faster-whisper မတင်ထားပါ")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, info = model.transcribe(wav, vad_filter=True)
    out = [{"start": s.start, "end": s.end, "text": s.text.strip()}
           for s in segs if s.text.strip()]
    return out, info.language


def translate_my(texts, retries=3):
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise PipelineError("deep-translator မတင်ထားပါ")
    tr = GoogleTranslator(source="auto", target="my")
    result = []
    for t in texts:
        last = None
        for k in range(retries):
            try:
                result.append(tr.translate(t))
                break
            except Exception as e:  # network / rate limit
                last = e
                time.sleep(1.5 * (k + 1))
        else:
            raise PipelineError(f"ဘာသာပြန်မရပါ: {last}")
    return result


# ---------------------------------------------------------------- text -> Burmese voice
def synthesize(text, voice, rate, out_path, retries=3):
    try:
        import edge_tts
    except ImportError:
        raise PipelineError("edge-tts မတင်ထားပါ (requirements.txt အတိုင်း install လုပ်ပါ)")

    async def _go():
        await edge_tts.Communicate(text, voice, rate=rate).save(str(out_path))

    last = None
    for k in range(retries):
        try:
            asyncio.run(_go())
            if Path(out_path).exists() and Path(out_path).stat().st_size > 500:
                return
        except Exception as e:
            last = e
        time.sleep(1.5 * (k + 1))
    raise PipelineError(f"မြန်မာအသံ မထုတ်နိုင်ပါ: {last}")


# ---------------------------------------------------------------- timeline
def merge_segments(raw, total, min_len=2.0):
    """Merge short speech segments and make them contiguous over [0, total]."""
    groups = []
    cur = None
    for s in raw:
        if cur is None:
            cur = dict(s)
        else:
            cur["end"] = s["end"]
            cur["text"] += " " + s["text"]
        if cur["end"] - cur["start"] >= min_len:
            groups.append(cur)
            cur = None
    if cur:
        if groups and cur["end"] - cur["start"] < min_len:
            groups[-1]["end"] = cur["end"]
            groups[-1]["text"] += " " + cur["text"]
        else:
            groups.append(cur)
    segs = []
    for i, g in enumerate(groups):
        start = 0.0 if i == 0 else g["start"]
        end = total if i == len(groups) - 1 else groups[i + 1]["start"]
        if end - start > 0.2:
            segs.append({"start": start, "end": end, "text": g["text"]})
    return segs


def split_script(script, total):
    """Burmese script -> chunks, video time divided in proportion to text length."""
    parts = [p.strip() for p in re.split(r"(?<=[။!?.])\s*|\n+", script) if p.strip()]
    chunks, buf = [], ""
    for p in parts:
        buf = (buf + " " + p).strip()
        if len(buf) >= 40:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks and len(buf) < 15:
            chunks[-1] += " " + buf
        else:
            chunks.append(buf)
    if not chunks:
        raise PipelineError("Script ထဲမှာ စာမရှိပါ")
    weights = [max(len(c), 1) for c in chunks]
    tot = sum(weights)
    segs, t = [], 0.0
    for c, w in zip(chunks, weights):
        d = total * w / tot
        segs.append({"start": t, "end": t + d, "text": c})
        t += d
    segs[-1]["end"] = total
    return segs


# ---------------------------------------------------------------- sync one segment
def frame_round(t):
    return max(1, math.ceil(t * FPS - 1e-6)) / FPS


def plan_segment(d_v, d_a):
    """Return (T, video_factor, audio_tempo). T is a whole number of frames."""
    if d_a <= d_v:
        T = frame_round(d_v)
    else:
        T = max(d_v * min(d_a / d_v, MAX_VIDEO_SLOW), d_a / MAX_AUDIO_FAST)
        T = frame_round(T)
    tempo = max(1.0, d_a / T)
    return T, T / d_v, tempo


VF = ("[0:v]split=2[a][b];"
      "[a]scale=108:192:force_original_aspect_ratio=increase,crop=108:192,"
      "boxblur=4:2,scale={w}:{h}[bg];"
      "[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
      "[bg][fg]overlay=(W-w)/2:(H-h)/2,setpts={f:.6f}*PTS,fps={fps},tpad=stop_mode=clone:stop_duration=1,format=yuv420p")


def render_segment(src, idx, start, d_v, audio_in, d_a, work):
    T, factor, tempo = plan_segment(d_v, d_a)
    n = int(round(T * FPS))
    vid = str(Path(work) / f"v{idx:04d}.mp4")
    aud = str(Path(work) / f"a{idx:04d}.wav")
    run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{d_v:.3f}", "-i", str(src),
         "-filter_complex", VF.format(w=W, h=H, f=factor, fps=FPS),
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-r", str(FPS), "-frames:v", str(n), vid])
    af = (f"atempo={tempo:.4f}," if tempo > 1.0001 else "") + f"apad=whole_dur={T:.4f}"
    run(["ffmpeg", "-y", "-i", str(audio_in), "-af", af, "-ar", "44100", "-ac", "2",
         "-t", f"{T:.4f}", aud])
    return vid, aud, T


def concat(files, out, work, kind):
    lst = Path(work) / f"list_{kind}.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])


# ---------------------------------------------------------------- main
def process(video, work, out_path, cb, voice="female", speed=0, script="",
            whisper_model="base", hooks=None):
    """cb(percent, message). hooks lets tests replace transcribe/translate/synthesize."""
    hooks = hooks or {}
    _transcribe = hooks.get("transcribe", transcribe)
    _translate = hooks.get("translate", translate_my)
    _synth = hooks.get("synthesize", synthesize)
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)

    cb(5, "Video စစ်ဆေးနေပါတယ်")
    if not has_video(video):
        raise PipelineError("ဒီဖိုင်ထဲမှာ video မပါပါ")
    total = probe_duration(video)
    if total < 3:
        raise PipelineError("Video တိုလွန်းပါတယ်")

    if script and script.strip():
        cb(10, "Script ကို အပိုင်းတွေခွဲနေပါတယ်")
        segs = split_script(script, total)
    else:
        cb(10, "စကားပြောကို စာအဖြစ်ပြောင်းနေပါတယ် (အချိန်ကြာနိုင်ပါတယ်)")
        raw, lang = _transcribe(video, work, whisper_model)
        if not raw:
            raise PipelineError("စကားပြောမတွေ့ပါ။ Burmese script ကိုယ်တိုင်ထည့်ပေးပါ")
        segs = merge_segments(raw, total)
        if lang != "my":
            cb(30, f"မြန်မာလို ဘာသာပြန်နေပါတယ် ({lang} → my)")
            my = _translate([s["text"] for s in segs])
            for s, t in zip(segs, my):
                s["text"] = t

    rate = f"{speed:+d}%"
    v_name = VOICES.get(voice, VOICES["female"])
    n = len(segs)
    vids, auds = [], []
    for i, s in enumerate(segs):
        pct = 40 + int(50 * i / n)
        cb(pct, f"အပိုင်း {i + 1}/{n}: အသံထုတ် + video ကိုက်ညှိနေပါတယ်")
        mp3 = work / f"t{i:04d}.mp3"
        _synth(s["text"], v_name, rate, mp3)
        d_a = probe_duration(mp3)
        d_v = s["end"] - s["start"]
        v, a, _ = render_segment(video, i, s["start"], d_v, mp3, d_a, work)
        vids.append(v)
        auds.append(a)

    cb(92, "အပိုင်းတွေကို ပြန်ဆက်နေပါတယ်")
    cv, ca = work / "all_v.mp4", work / "all_a.wav"
    concat(vids, cv, work, "v")
    concat(auds, ca, work, "a")
    cb(96, "အသံနဲ့ video ပေါင်းနေပါတယ်")
    run(["ffmpeg", "-y", "-i", str(cv), "-i", str(ca), "-c:v", "copy", "-c:a", "aac",
         "-b:a", "160k", "-movflags", "+faststart", str(out_path)])

    dv, da = probe_duration(cv), probe_duration(ca)
    if abs(dv - da) > 0.15:
        raise PipelineError(f"အသံနဲ့ video အချိန်မကိုက်ပါ (video {dv:.2f}s / audio {da:.2f}s)")
    cb(100, f"ပြီးပါပြီ — {dv:.0f} စက္ကန့်၊ အသံနဲ့ video ကိုက်ပါတယ်")
    return {"duration": dv, "segments": n}
