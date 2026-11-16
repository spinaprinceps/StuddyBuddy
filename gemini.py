"""Turn a messy brain dump into study tasks with minute estimates.

Uses the Gemini API when a key is available, otherwise a simple offline parser.
Standard library only.
"""
import base64
import json
import re
import socket
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta"
LEVELS = ("low", "medium", "high")


class GeminiError(Exception):
    pass


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tasks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "minutes": {"type": "INTEGER"},
                    "difficulty": {"type": "STRING", "enum": list(LEVELS)},
                    "first_step": {"type": "STRING"},
                },
                "required": ["name", "minutes", "difficulty", "first_step"],
            },
        },
    },
    "required": ["tasks"],
}

PROMPT = """You are the study planner for a student. {goal}

Current local date and time: {now} ({weekday}).
They can study about {hours} hours today, until roughly {end}.

Below is their messy, unordered plan for today (subjects, chapters, assignments, revision, practice...).
Turn it into tasks:
- name: short and clear, at most 8 words.
- minutes: a realistic estimate for a focused student, including about 20% buffer. If they stated a duration, respect it (convert hours to minutes). Merge duplicates. Split anything longer than 4 hours into logical parts.
- Order the tasks in the best execution order (prerequisites first, then the most important or closest deadline).
- difficulty: how mentally demanding it is (low, medium or high).
- first_step: ONE concrete physical action that starts the task in under 2 minutes, e.g. "Open the DBMS notes at chapter 3 and read the first heading".

Do not include breaks, meals or sleep as tasks.

Plan:
\"\"\"
{dump}
\"\"\"
"""


# ---------------- HTTP ----------------
def _request(url, key, body=None, timeout=45):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _error_text(e):
    try:
        msg = json.loads(e.read().decode("utf-8"))["error"]["message"]
    except Exception:
        msg = str(e)
    return msg[:160]


def pick_flash_model(key):
    """Newest stable 'flash' model that supports generateContent."""
    data = _request(f"{API}/models?pageSize=200", key)
    models = [m["name"].split("/", 1)[1] for m in data.get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])]
    bad = ("lite", "image", "tts", "audio", "live", "thinking", "exp", "embedding")
    flash = [n for n in models if "flash" in n and not any(b in n for b in bad)]
    stable = [n for n in flash if "preview" not in n] or flash
    if not stable:
        raise GeminiError("No Gemini flash model is available for this key.")

    def version(n):
        nums = re.findall(r"\d+(?:\.\d+)?", n)
        return float(nums[0]) if nums else 0.0
    return max(stable, key=version)


def _generate(body, key, model, fallbacks=(), per_try=20, budget=75):
    """Try the preferred model, then the fallbacks, skipping any that are slow or overloaded.
    Returns (response json, model used)."""
    candidates = [model] + [m for m in fallbacks if m != model]
    problems, started, all_404 = [], time.time(), True
    for m in candidates:
        if time.time() - started > budget:
            break
        try:
            return _request(f"{API}/models/{m}:generateContent", key, body, timeout=per_try), m
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise GeminiError(f"key rejected or bad request: {_error_text(e)}")
            if e.code != 404:
                all_404 = False
            problems.append(f"{m} {'busy' if e.code in (429, 500, 503) else e.code}")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                all_404 = False
                problems.append(f"{m} slow")
                continue
            raise GeminiError(f"no internet connection ({e.reason})")
        except (TimeoutError, socket.timeout):
            all_404 = False
            problems.append(f"{m} slow")
    if all_404 and problems:
        m = pick_flash_model(key)
        return _request(f"{API}/models/{m}:generateContent", key, body, timeout=per_try * 2), m
    raise GeminiError("Gemini is overloaded right now (" + ", ".join(problems[:4]) + ")")


def _text(res):
    try:
        parts = res["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise GeminiError("empty response (possibly blocked)")
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def analyze(dump, key, model, now, hours, end, deadline, fallbacks=()):
    """Returns (result dict, model actually used). Raises GeminiError."""
    prompt = PROMPT.format(goal=(f"Their next deadline/exam is {deadline}." if deadline else ""), now=now.strftime("%Y-%m-%d %H:%M"),
                           weekday=now.strftime("%A"), hours=hours, end=end.strftime("%H:%M"),
                           dump=dump.strip())
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA,
                             "temperature": 0.2},
    }
    res, used = _generate(body, key, model, fallbacks)
    try:
        return normalize(json.loads(_text(res)), now), used
    except ValueError:
        raise GeminiError("unexpected response format")


TRANSCRIBE_PROMPT = (
    "Transcribe this voice note exactly as spoken. The speaker is a student planning their study day "
    "and may use Indian English or mix in Hindi/Kannada words; write everything in English letters. "
    "Keep times and durations exactly as said. Return only the transcript, no commentary. "
    "Never guess or invent words: if the audio has no clear speech, return exactly NO_SPEECH."
)
NO_SPEECH = "NO_SPEECH"
MIN_RMS = 120  # below this the recording is basically silence


def loudness(wav_bytes):
    """RMS loudness of a 16-bit WAV (0 = silent). Returns None if it can't be measured."""
    import audioop
    import io
    import wave
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            frames = w.readframes(w.getnframes())
            return audioop.rms(frames, w.getsampwidth())
    except Exception:
        return None


def transcribe(wav_bytes, key, model, fallbacks=()):
    """Voice note (WAV bytes) -> text. Returns (text, model used). Raises GeminiError."""
    level = loudness(wav_bytes)
    if level is not None and level < MIN_RMS:
        raise GeminiError("I couldn't hear anything. Is the right microphone on and unmuted?")
    body = {"contents": [{"role": "user", "parts": [
        {"text": TRANSCRIBE_PROMPT},
        {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(wav_bytes).decode("ascii")}},
    ]}]}
    res, used = _generate(body, key, model, fallbacks, per_try=30, budget=90)
    text = _text(res).strip()
    if not text or NO_SPEECH in text.upper().replace(" ", "_"):
        raise GeminiError("no clear speech in the recording. Speak closer to the mic and try again")
    return text, used


# ---------------- microphone (Windows MCI, no extra installs) ----------------
_winmm = None


def _mci(cmd):
    global _winmm
    import ctypes
    if _winmm is None:
        _winmm = ctypes.windll.winmm
    buf = ctypes.create_unicode_buffer(256)
    err = _winmm.mciSendStringW(cmd, buf, 255, 0)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        _winmm.mciGetErrorStringW(err, msg, 255)
        raise GeminiError(f"microphone: {msg.value or err}")
    return buf.value


class Recorder:
    """Start/stop recording from the default microphone to a WAV file."""
    def __init__(self, path):
        self.path = path
        self.active = False

    def start(self):
        try:
            _mci("close bossrec")
        except GeminiError:
            pass
        _mci("open new type waveaudio alias bossrec")
        try:
            _mci("set bossrec bitspersample 16 channels 1 samplespersec 16000 bytespersec 32000 alignment 2")
        except GeminiError:
            pass  # some drivers refuse custom formats; the default still works
        _mci("record bossrec")
        self.active = True

    def stop(self):
        if not self.active:
            return None
        self.active = False
        try:
            _mci("stop bossrec")
            _mci(f'save bossrec "{self.path}"')
        finally:
            try:
                _mci("close bossrec")
            except GeminiError:
                pass
        with open(self.path, "rb") as f:
            return f.read()


# ---------------- cleanup ----------------
def normalize(data, now):
    tasks = []
    for t in data.get("tasks", []):
        name = str(t.get("name", "")).strip()
        if not name:
            continue
        try:
            minutes = int(t.get("minutes", 50))
        except (TypeError, ValueError):
            minutes = 50
        diff = str(t.get("difficulty", "medium")).lower()
        tasks.append({"name": name[:80], "minutes": min(600, max(10, minutes)),
                      "difficulty": diff if diff in LEVELS else "medium",
                      "first_step": str(t.get("first_step", "")).strip()[:160]})
    return {"tasks": tasks}


# ---------------- offline fallback ----------------
_DUR_H = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", re.I)
_DUR_M = re.compile(r"(\d+)\s*(?:m|min|mins|minute|minutes)\b", re.I)
def offline_parse(dump, now):
    chunks = [c.strip(" -*•\t") for c in re.split(r"[\n;]+|(?<=[a-z])\.\s+", dump) if c.strip(" -*•\t")]
    tasks = []
    for c in chunks:
        minutes = 50
        mh, mm = _DUR_H.search(c), _DUR_M.search(c)
        if mh:
            minutes = int(float(mh.group(1)) * 60)
        elif mm:
            minutes = int(mm.group(1))
        name = _DUR_H.sub("", _DUR_M.sub("", c))
        name = re.sub(r"\b(maybe|about|around|approx|like|for)\b", "", name, flags=re.I)
        name = re.sub(r"\(\s*\)|\s{2,}", " ", name).strip(" ,.-()")
        if name:
            tasks.append({"name": name[:80], "minutes": min(600, max(10, minutes)),
                          "difficulty": "medium", "first_step": ""})
    return {"tasks": tasks}
