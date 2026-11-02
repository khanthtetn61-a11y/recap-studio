"""Offline test: fake TTS/translate/transcribe, real ffmpeg sync + 9:16 render."""
import subprocess, sys, shutil
from pathlib import Path
import pipeline

W = Path("/tmp/recap-test"); shutil.rmtree(W, ignore_errors=True); W.mkdir()
src = W / "src.mp4"
subprocess.run(["ffmpeg","-y","-f","lavfi","-i","testsrc2=size=1280x720:rate=24","-f","lavfi",
  "-i","sine=frequency=300","-t","30","-c:v","libx264","-c:a","aac",str(src)],check=True,capture_output=True)

def fake_synth(text, voice, rate, out):
    d = 0.08 * len(text)          # voice length depends on text length
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",f"sine=frequency=440:duration={d:.2f}",str(out)],check=True,capture_output=True)

def fake_transcribe(v, w, m):
    return [{"start":1,"end":5,"text":"x"*30},{"start":6,"end":9,"text":"y"*120},
            {"start":12,"end":20,"text":"z"*10},{"start":22,"end":29,"text":"w"*60}], "en"

hooks = {"synthesize": fake_synth, "transcribe": fake_transcribe, "translate": lambda t: t}
for name, script in [("script", "စာကြောင်းတစ်။ " * 8 + "နောက်ဆုံးစာကြောင်း။"), ("auto", "")]:
    out = W / f"{name}.mp4"
    r = pipeline.process(src, W / f"w_{name}", out, lambda p, m: None, script=script, hooks=hooks)
    info = subprocess.run(["ffprobe","-v","error","-show_entries","stream=codec_type,width,height,duration,r_frame_rate",
        "-of","csv=p=0",str(out)],capture_output=True,text=True).stdout
    print(name, r); print(info)
