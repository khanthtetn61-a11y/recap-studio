# Recap Studio (ဖုန်းနဲ့သုံးဖို့ — Render မှာ တင်ပုံ)

1. GitHub (ဖုန်း browser) မှာ repo အသစ်ဆောက်ပြီး ဒီ file တွေအားလုံး upload လုပ်ပါ။
2. render.com မှာ New → Web Service → repo ရွေးပါ → Runtime: **Docker**
3. Environment variables:
   - APP_PASSWORD = ကိုယ့်စကားဝှက် (မရှိရင် ဘယ်သူမဆို သုံးလို့ရမယ်)
   - WHISPER_MODEL = tiny (RAM နည်းရင်) သို့မဟုတ် base
4. Deploy ပြီးရင် ရလာတဲ့ URL ကို ဖုန်းမှာ ဖွင့်ပါ။

ပြောင်းနိုင်တာ: pipeline.py ထဲက MAX_VIDEO_SLOW / MAX_AUDIO_FAST (အသံနဲ့ ကိုက်ဖို့ video ကို ဘယ်လောက်နှေးအောင်လုပ်မလဲ)
