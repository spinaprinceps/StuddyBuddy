"""Sounds for The Boss: end-of-timer chimes and looping ambient focus noise.

Everything is synthesised once into ./sounds/*.wav (no downloads), then played
through Windows MCI so the ambient loop and the alarm chimes can overlap.
"""
import array
import ctypes
import math
import os
import random
import wave
import winsound

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")

AMBIENT = {"rain": "Rain", "brown": "Brown noise (deep)", "pink": "Pink noise (soft)", "white": "White noise"}
VERSION = 1  # bump to regenerate files after changing the synthesis

_winmm = ctypes.windll.winmm


def _mci(cmd):
    buf = ctypes.create_unicode_buffer(256)
    return _winmm.mciSendStringW(cmd, buf, 255, 0), buf.value


def _write(path, samples, rate):
    peak = max(1e-9, max(abs(x) for x in samples))
    scale = 32000 / peak
    data = array.array("h", (int(x * scale) for x in samples))
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    os.replace(tmp, path)


# ---------- chimes ----------
def _bell(out, rate, start, freq, length, amp=1.0):
    n0 = int(start * rate)
    for i in range(int(length * rate)):
        t = i / rate
        env = math.exp(-3.2 * t) * min(1.0, t * 200)  # quick attack, long ring
        v = (math.sin(2 * math.pi * freq * t) + 0.45 * math.sin(2 * math.pi * freq * 2.01 * t)
             + 0.2 * math.sin(2 * math.pi * freq * 3.0 * t))
        j = n0 + i
        if j < len(out):
            out[j] += amp * env * v


def _chime(notes, total, rate=44100):
    out = [0.0] * int(total * rate)
    for start, freq in notes:
        _bell(out, rate, start, freq, 1.6)
    return out


CHIMES = {
    # focus session finished: bright rising arpeggio, twice
    "done": lambda: _chime([(0.0, 1046.5), (0.18, 1318.5), (0.36, 1568.0), (0.54, 2093.0),
                            (1.4, 1046.5), (1.58, 1318.5), (1.76, 1568.0), (1.94, 2093.0)], 3.6),
    # break / mentor / stand-down over: "back to work" double ding, three times
    "start": lambda: _chime([(0.0, 880.0), (0.25, 1174.7), (0.9, 880.0), (1.15, 1174.7),
                             (1.8, 880.0), (2.05, 1174.7)], 3.8),
    # gentle heads-up (mentor reminder, starter reached)
    "soft": lambda: _chime([(0.0, 1318.5), (0.3, 1046.5)], 2.0),
}


# ---------- ambient noise ----------
def _ambient(kind, seconds=40, rate=22050, fade=2.0):
    rnd = random.Random(7)
    n, f = int(seconds * rate), int(fade * rate)
    total = n + f
    x = [0.0] * total
    if kind == "white":
        for i in range(total):
            x[i] = rnd.uniform(-1, 1)
    elif kind == "brown":
        b = 0.0
        for i in range(total):
            b = (b + 0.02 * rnd.uniform(-1, 1)) / 1.02
            x[i] = b
    elif kind == "pink":  # Paul Kellet's economy filter
        b0 = b1 = b2 = 0.0
        for i in range(total):
            w = rnd.uniform(-1, 1)
            b0 = 0.99765 * b0 + w * 0.0990460
            b1 = 0.96300 * b1 + w * 0.2965164
            b2 = 0.57000 * b2 + w * 1.0526913
            x[i] = b0 + b1 + b2 + w * 0.1848
    else:  # rain: hiss with a slow swell, plus scattered droplets
        lp = lo = 0.0
        for i in range(total):
            w = rnd.uniform(-1, 1)
            lp += 0.35 * (w - lp)       # soften the hiss
            lo += 0.004 * (lp - lo)     # remove the rumble
            swell = 0.8 + 0.2 * math.sin(2 * math.pi * i / (rate * 10.0))
            x[i] = (lp - lo) * swell
        for _ in range(int(seconds * 55)):
            at = rnd.randrange(total)
            freq = rnd.uniform(1800, 4500)
            amp = rnd.uniform(0.05, 0.35)
            for k in range(int(rate * 0.012)):
                j = at + k
                if j >= total:
                    break
                x[j] += amp * math.exp(-k / (rate * 0.0025)) * math.sin(2 * math.pi * freq * k / rate)
    # crossfade the tail into the head so the loop is seamless
    out = x[:n]
    for i in range(f):
        a = i / f
        out[i] = out[i] * math.sqrt(a) + x[n + i] * math.sqrt(1 - a)
    return out, rate


def path(name):
    return os.path.join(DIR, f"{name}.v{VERSION}.wav")


def ensure(name):
    """Make sure sounds/<name>.wav exists; returns its path. Slow-ish for ambient (~1-2 s)."""
    p = path(name)
    if os.path.exists(p):
        return p
    os.makedirs(DIR, exist_ok=True)
    if name in CHIMES:
        _write(p, CHIMES[name](), 44100)
    else:
        samples, rate = _ambient(name)
        _write(p, samples, rate)
    return p


def ensure_all():
    for name in list(CHIMES) + list(AMBIENT):
        ensure(name)


# ---------- playback (call from the UI thread only) ----------
class Player:
    def __init__(self):
        self.ambient = None  # name of the loop currently playing
        self._fx = 0

    def chime(self, name, volume=100):
        try:
            p = ensure(name)
        except Exception:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return
        alias = f"bossfx{self._fx % 3}"
        self._fx += 1
        _mci(f"close {alias}")
        err, _ = _mci(f'open "{p}" type mpegvideo alias {alias}')
        if err:
            # MCI unavailable: fall back to winsound (this interrupts the ambient loop)
            winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC)
            if self.ambient:
                self.ambient = None
            return
        _mci(f"setaudio {alias} volume to {int(max(0, min(100, volume)) * 10)}")
        _mci(f"play {alias} from 0")

    def start_ambient(self, name, volume=40):
        if self.ambient == name:
            return True
        self.stop_ambient()
        p = path(name)
        if not os.path.exists(p):
            return False
        err, _ = _mci(f'open "{p}" type mpegvideo alias bossamb')
        if err:
            return False
        _mci(f"setaudio bossamb volume to {int(max(0, min(100, volume)) * 10)}")
        _mci("play bossamb repeat")
        self.ambient = name
        return True

    def set_ambient_volume(self, volume):
        if self.ambient:
            _mci(f"setaudio bossamb volume to {int(max(0, min(100, volume)) * 10)}")

    def stop_ambient(self):
        if self.ambient:
            _mci("stop bossamb")
            _mci("close bossamb")
            self.ambient = None

    def close(self):
        self.stop_ambient()
        for i in range(3):
            _mci(f"close bossfx{i}")
