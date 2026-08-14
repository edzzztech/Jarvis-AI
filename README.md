# J.A.R.V.I.S. Ultimate — Setup & Command Guide

A local, voice-controlled AI HUD for Windows. Runs on a local Ollama model,
controls your PC and hardware, sends email through Gmail-in-Chrome, generates
beautifully-styled documents, displays images/websites/documents inside its own
interface, and remembers your conversations.

---

## 1. Requirements

**Required packages:**
```
pip install pywebview pyautogui psutil pillow requests
```

**Recommended extras** (features degrade gracefully without them):
```
pip install pywin32 pystray pyperclip pyttsx3 SpeechRecognition spotipy reportlab
```

**Ollama** must be running locally:
```
ollama pull llama3.2
ollama pull llava      # only for screen "vision"
ollama serve
```

If a required package is missing, JARVIS prints exactly which one and the
`pip install` line to fix it instead of crashing.

---

## 2. First run

```
python jarvis_ultimate.py
```

On first launch it creates **jarvis_config.json** next to the script. Close
JARVIS, fill in your details, and restart. A sample copy
(jarvis_config.sample.json) shows the full structure.

### Where files live
Only jarvis_config.json sits next to the script. Everything else — memory,
logs, the Spotify cache, screenshots, and generated documents — goes into a
**jarvis_data/** folder (documents land in jarvis_data/documents/).

---

## 3. What's new in this version

- **Redesigned UI** matching the cleaner HUD look: darker grid background, cyan
  accent, rounded glass widgets, softer arc-reactor, and a **custom cool title
  bar** (J.A.R.V.I.S · HUD SYSTEM with a pulsing orb and live equalizer).
- **Corner overlay is now the arc-reactor "JARVIS" logo** — a spinning reactor
  that pulses red while he speaks. Click it for the main window, double-click for
  a vision scan, × to close.
- **Faster responses** — JARVIS speaks each sentence the instant it finishes
  streaming, so he starts talking almost immediately.
- **Interrupt him anytime** — start typing a new command and he stops instantly;
  by voice, say "Jarvis", "stop", "quiet", or "enough" while he's talking.
- **Gemini-style documents** — generated reports have a gradient header, accent
  rules, styled headings and lists, and a footer, exported as a designed PDF
  (and matching styled HTML). The in-app editor has **EDIT / PREVIEW** tabs.
- **More hardware control** — volume level, brightness, display sleep, PC
  sleep/restart/shutdown, Wi-Fi toggle, media keys.

---

## 4. Commands (say "Jarvis ..." or type them)

### Email
- "send an email to John about the meeting tomorrow"
- "email sarah@work.com saying I'll be late"
- "draft an email to my boss about the report"  (draft = review before send)
- "remember email for John as john@example.com"

### Documents (generate -> view -> edit -> export)
- "create a PDF on black holes" / "make me a report about the Roman Empire"
- "edit the document to add a conclusion"
- "export to PDF"
- Use the EDIT / PREVIEW tabs to see the styled version.

### Show things inside JARVIS
- "show me how to tie a tie"  (instruction image)
- "show me a diagram of the water cycle"
- "open bbc.com in jarvis"

### PC & hardware control
- "open chrome / notepad / calculator / vs code"
- "set volume to 40 percent", "volume up", "mute"
- "set brightness to 60 percent"
- "turn off the display"
- "lock the computer"
- "go to sleep", "restart the computer", "shut down the pc"
- "turn off wifi" / "enable wifi"
- "take a screenshot", "system status"
- "type hello world", "read my clipboard"
- "empty the recycle bin"

### Web & knowledge
- "google best pizza near me", "search for python tutorials"
- "play lofi beats on youtube"
- "wikipedia the Eiffel Tower", "what's the weather in Tokyo"
- "give me the news", "price of bitcoin"

### Memory
- "remember that my Wi-Fi password is hunter2"
- "what do you remember?"
- Full timestamped transcript is kept in jarvis_data/jarvis_memory.json.

### Extras
- "what is 45 * 12", "set a timer for 5 minutes"
- "flip a coin", "roll a dice"
- "remind me to call mum in 10 minutes"
- Spotify: "play <song>", "pause music", "next track"

### Customise the bottom bar
Click the gear button to open Control Bar Settings, tick the buttons you want,
press SAVE LAYOUT. It persists in the config and reloads next launch.

---

## 5. Notes & gotchas
- **App vs PC shutdown:** "shutdown jarvis" / "exit jarvis" closes the app;
  "shut down the pc" powers off the machine (5-second delay).
- **First email:** test with a throwaway recipient. The browser flow waits ~6s
  for Gmail to load before sending; set "auto_send": false until you trust it.
- **Embedded websites:** some sites refuse to load in a frame (Google login,
  banking) — that's the site, not a bug. Open those in a normal browser.
- **Brightness / Wi-Fi / power** commands are Windows-oriented and may need
  appropriate permissions; Wi-Fi toggling tries common adapter names.
- **Voice quality:** JARVIS auto-picks the best British male voice installed. Add
  "Microsoft Ryan" or "George" via Windows Speech settings for the best sound.