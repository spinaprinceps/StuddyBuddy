"""The Boss - a spy-mission desktop study companion.

Run:   pythonw boss.py           (normal)
       pythonw boss.py --demo    (every timer ~30x faster, separate save file)
"""
import ctypes
import datetime as dt
import os
import queue
import random
import re
import socket
import sys
import tempfile
import threading
import tkinter as tk
import traceback
import winsound
from ctypes import wintypes

import game as G
import ladder as LD
import gemini
import messages as T
import sounds as SND

DEMO = "--demo" in sys.argv

# ---------- one copy at a time ----------
_PORT = 47654 if DEMO else 47653
_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", _PORT))
    _lock.listen(4)
except OSError:
    # Already running: poke the running copy so it shows itself, then exit.
    try:
        socket.create_connection(("127.0.0.1", _PORT), timeout=2).close()
    except OSError:
        pass
    sys.exit(0)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ---------- Windows helpers ----------
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.GetTickCount.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    return ((kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def foreground_title():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def yt_key(title):
    """Stable key for a YouTube window: drop the '(3) ' notification count and the browser name."""
    t = re.sub(r"^\(\d+\)\s*", "", title.strip())
    return t.split(" - YouTube")[0].strip().lower()


def work_area():
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def virtual_screen():
    x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    return x, y, x + user32.GetSystemMetrics(78), y + user32.GetSystemMetrics(79)


def play(kind="SystemExclamation"):
    try:
        winsound.PlaySound(kind, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass


LOG_PATH = os.path.join(G.BASE, "boss_error.log")


def log_error():
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(dt.datetime.now().isoformat() + "\n" + traceback.format_exc() + "\n")


# ---------- look ----------
TRANS = "#010203"  # transparent key colour
BG, PANEL, FG, MUTED = "#151821", "#232937", "#e9ecef", "#9aa3b2"
ACCENT, ACCENT_DK, GOOD, WARN = "#ef233c", "#b5172b", "#2ec27e", "#f4b942"
LEVEL_COLORS = {"low": "#3d6b8f", "medium": "#1f6f43", "high": "#8a3ffc"}

PALETTE = {
    "H": "#2b2d42",  # hat
    "h": "#ef233c",  # hat band
    "F": "#f4a261",  # face / hands
    "S": "#14141a",  # sunglasses / brows
    "s": "#ffffff",  # glint
    "C": "#c8a165",  # trench coat
    "c": "#8d6e3f",  # collar / belt
    "L": "#3a3a3a",  # legs
    "M": "#7a2e1f",  # mouth / closed eyes
}
ANGRY_FACE = "#e76f51"

BASE_ROWS = [
    "....HHHHHH....",
    "....HHHHHH....",
    "...hhhhhhhh...",
    ".HHHHHHHHHHHH.",
    "..FFFFFFFFFF..",
    ".FSSSsFFSSSsF.",
    ".FSSSSFFSSSSF.",
    ".FFFFFFFFFFFF.",
    ".FFFFFMMFFFFF.",
    "..FFFFFFFFFF..",
    "..CCCccccCCC..",
    ".CCCCCcCCCCCC.",
    "FCCCCCcCCCCCCF",
    ".CccccccccccC.",
    ".CCCCCcCCCCCC.",
    "..LL......LL..",
]

# Small on-the-spot actions (seconds they last)
ACTION_LEN = {"peek": 1.2, "look_left": 2.2, "look_right": 2.2, "tap_foot": 2.6, "adjust_hat": 1.4,
              "check_watch": 2.2, "dossier": 4.0, "stretch": 2.0, "sip": 2.4, "yawn": 1.6}
ACTION_POOL = {
    "idle": ["peek", "look_left", "look_right", "tap_foot", "adjust_hat", "check_watch", "dossier", "stretch", "sip"],
    "focus": [],  # quiet during focus: no idle actions
    "break": ["sip", "yawn", "stretch", "sip"],
    "order": ["check_watch", "tap_foot", "check_watch"],
    "punish": ["tap_foot", "check_watch"],
}


def shift_row(row, d):
    return ("." + row[:-1]) if d > 0 else (row[1:] + ".")


def sprite_rows(face, action, frame):
    rows = list(BASE_ROWS)
    if face == "happy":
        rows[8] = ".FFFFMFFMFFFF."
        rows[9] = "..FFFFMMFFFF.."
    elif face == "angry":
        rows[4] = "..FSSFFFFSSF.."
        rows[8] = ".FFFFMMMMFFFF."
    elif face == "sleepy":
        rows[4] = "..SSSSSSSSSS.."
        rows[5] = ".FFFFFFFFFFFF."
        rows[6] = ".FMMMFFFFMMMF."
    if action == "peek" and face in ("neutral", "happy"):  # peeks over the sunglasses
        rows[5] = ".FFSSFFFFSSFF."
        rows[6] = ".FSSSsFFSSSsF."
        rows[7] = ".FSSSSFFSSSSF."
    elif action in ("look_left", "look_right"):
        d = -1 if action == "look_left" else 1
        for i in range(4, 10):
            rows[i] = shift_row(rows[i], d)
    elif action == "yawn":
        rows[8] = ".FFFFMMMFFFFF."
        rows[9] = "..FFFMMMFFFF.."
    elif action == "stretch":
        rows[9] = "F.FFFFFFFFFF.F"
        rows[12] = ".CCCCCcCCCCCC."
    elif action == "check_watch":
        rows[11] = "FCCCCCcCCCCCC."
        rows[12] = ".CCCCCcCCCCCCF"
    elif action in ("sip", "dossier"):
        rows[12] = ".CCCCCcCCCCCC."
    if action == "tap_foot" and frame % 2:
        rows[15] = "..LL.....LL..."
    return rows


class Boss:
    def __init__(self):
        self.cfg = G.load_config()
        self.g = G.Game(self.cfg, DEMO)
        g = self.g

        self.root = tk.Tk()
        self.root.title("The Boss")
        s = self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.P = int(6 * s)
        self.W, self.H = int(340 * s), int(330 * s)
        self.font = ("Segoe UI", 10)
        self.font_b = ("Segoe UI", 10, "bold")

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=TRANS)
        self.root.attributes("-transparentcolor", TRANS)
        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H, bg=TRANS, highlightthickness=0)
        self.canvas.pack()

        wa = work_area()
        self.x, self.y = wa[2] - self.W - int(40 * s), wa[3] - self.H
        pos = g.data.get("pos")
        if pos:
            vx1, vy1, vx2, vy2 = virtual_screen()
            if vx1 - self.W // 2 <= pos[0] <= vx2 - self.W // 2 and vy1 - self.H // 2 <= pos[1] <= vy2 - self.H // 2:
                self.x, self.y = pos
        self.root.geometry(f"{self.W}x{self.H}+{self.x}+{self.y}")

        # animation / UI state
        now = dt.datetime.now()
        self.frame = 0
        self.action = None
        self.action_until = None
        self.next_action_at = now + dt.timedelta(seconds=5)
        self.dragging = False
        self.drag = None
        self.dragged = False
        self.celebrate_until = None
        self.mood_override = None
        self.mood_until = None
        self.bubble_job = None
        self.bubble_frame = None
        self.bubble_sticky = False
        self.bubble_kind = None
        self.windows = {}
        self.analyzing = False
        self.recording = False
        self.recorder = None

        # game-flow state
        self.state = "idle"  # idle | order | focus | break | punish
        self.session = None
        self.last_activity = now - dt.timedelta(seconds=max(0, g.secs("off_duty_nag_min") - g.M))
        self.paused_until = None
        self.off_duty_date = None
        self.stand_down_from = None
        self.load_duty(now)
        self.focus_end = self.break_end = None
        self.break_start = None
        self.paused = None  # focus session on hold during a mid-session break
        self.player = SND.Player()
        self.preview_until = None
        threading.Thread(target=SND.ensure_all, daemon=True).start()
        self.order_t0 = None
        self.nag_level = 0
        self.next_nag = None
        self.block_strikes = 0
        self.distracted_since = None
        self.grace = 20 if DEMO else 60
        self.yt_ok, self.yt_bad = set(), set()
        self.yt_pending = None
        self.yt_grace_until = None
        self.yt_lecture = False
        self.idle_prompt_at = None
        self.woke_date = None
        self.brief_snooze_until = None
        self.punish_next_nag = None
        self.p_job = None
        self.today = now.date()
        self.tick_n = 0

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Button-3>", self.on_right)
        c.bind("<Button-2>", lambda e: self.open_brain_dump())  # middle-click: quick note

        self.summoned = False
        threading.Thread(target=self._listen_for_summons, daemon=True).start()

        self.draw()
        self.root.after(800, self.startup)
        self.root.after(1000, self.tick)
        self.root.after(160, self.animate)

    # ================= helpers =================
    def ctx(self, **extra):
        g = self.g
        d = g.day()
        days, day_no, total = g.countdown()
        m = g.current_mission()
        c = dict(
            days=days, day=day_no, total=total, wasted=d["wasted_sec"] // 60,
            wins=d["wins"], losses=d["losses"], streak=g.data["streak"],
            rank=g.rank_name(), xp=g.data["xp"],
            mission=m["name"] if m else "general prep",
        )
        c.update(extra)
        return c

    def on_duty(self, now):
        if self.off_duty_date == now.date():
            return False
        if self.paused_until and now < self.paused_until:
            return False
        t = now.time()
        if not (G.hm(self.cfg["study_start"]) <= t < G.hm(self.cfg["study_end"])):
            return False
        return now < self.g.day_end(now)

    def order_soon(self, ms):
        def go():
            if self.state == "idle" and self.on_duty(dt.datetime.now()) and not self.g.data["pending_punishment"]:
                self.issue_order()
        self.root.after(ms, go)

    def celebrate(self, seconds=4):
        self.celebrate_until = dt.datetime.now() + dt.timedelta(seconds=seconds)

    LADDER_EVENTS = {
        "div_up": ("PROMOTED to {t}! Keep climbing.", "happy"),
        "promo_start": ("PROMO SERIES! Win {w} of the next {g} sessions to reach the next tier.", "happy"),
        "tier_up": ("TIER UP! Welcome to {t}. That took real discipline.", "happy"),
        "promo_failed": ("Promo series lost. Back to {t}. Run it back.", "angry"),
        "shielded": ("Demotion shield held. You stay at {t}... this time.", None),
        "div_down": ("DEMOTED to {t}. Earn it back.", "angry"),
        "tier_down": ("Dropped a tier: {t}. That hurts. Fix it.", "angry"),
    }

    def ladder_line(self, lad):
        """One line for a match result, e.g. '+22 SP · Scholar II (64/100 SP) · ON FIRE'."""
        st = self.g.data["ladder"]
        if st.get("promo"):
            p = st["promo"]
            tail = f"Promo series {p['w']}W-{p['l']}L (need {LD.PROMO_WINS} wins)"
        else:
            tail = f"{lad['title']} ({st['sp']}/{LD.SP_PER_DIV} SP)"
        sp = f"{lad['sp']:+d} SP · " if lad["sp"] else ""
        line = f"{sp}{tail}" + (" · 🔥 ON FIRE" if lad.get("on_fire") else "")
        for ev in lad["events"]:
            text, mood = self.LADDER_EVENTS.get(ev, (None, None))
            if text:
                big = ev in ("div_up", "tier_up", "promo_start")
                self.root.after(7000, lambda t=text, m=mood, b=big: (
                    self.celebrate(6) if b else None,
                    self.say(t.format(t=lad["title"], w=LD.PROMO_WINS, g=LD.PROMO_GAMES),
                             mood=m, sound=True, timeout=10)))
        return line

    def announce_quests(self, quests, delay=10000):
        for i, (text, sp) in enumerate(quests):
            self.root.after(delay + i * 6000, lambda t=text, p=sp: (
                self.celebrate(3), self.say(f"⭐ Daily quest complete: {t}. +{p} SP", mood="happy", timeout=7)))

    def rank_change(self, change, delay=6000):
        if not change:
            return

        def go():
            if change == "promoted":
                self.celebrate(5)
                self.say(T.PROMOTED.format(rank=self.g.rank_name()), mood="happy", sound=True, timeout=10)
            else:
                self.say(T.DEMOTED.format(rank=self.g.rank_name()), mood="angry", sound=True, timeout=10)
        self.root.after(delay, go)

    @staticmethod
    def mmss(td):
        total = max(0, int(td.total_seconds()))
        m, s = divmod(total, 60)
        return f"{m:02d}:{s:02d}"

    def level_buttons(self):
        """Buttons on an order bubble: resume / continue / choose."""
        if self.paused:
            left = self.mmss(self.paused["remaining"])
            return [(f"▶ Resume ({left})", self.resume_session), ("Choose task…", self.open_session_picker)]
        if self.g.current_mission():
            return [("▶ Continue", self.quick_start), ("Choose task…", self.open_session_picker)]
        return [("Choose task…", self.open_session_picker)]

    def worked_min(self, now, session=None, until=None):
        s = session or self.session
        if not s:
            return 0
        return max(0, int(((until or now) - s["start"]).total_seconds() // self.g.M))

    def drop_paused(self, now):
        """A paused session is abandoned (new session, stand-down...): keep the minutes worked."""
        p = self.paused
        if not p:
            return
        self.paused = None
        m = self.g.find(p["session"]["mission"])
        worked = self.worked_min(now, p["session"], until=p["at"])
        if m and worked > 0:
            m["done_min"] += worked
            self.g.save()

    def credit_partial(self, now):
        """Credit minutes already worked when a session is cut short without a win or loss."""
        if self.state == "focus" and self.session:
            m = self.g.find(self.session["mission"])
            worked = int((now - self.session["start"]).total_seconds() / self.g.M)
            if m and worked > 0:
                m["done_min"] += worked
                self.g.save()

    # ================= sounds =================
    def alarm(self, kind="done"):
        """Timer-ended chime. Plays on top of the ambient loop."""
        if not self.cfg.get("alarm_sound", True):
            return
        try:
            self.player.chime(kind, self.cfg.get("alarm_volume", 100))
        except Exception:
            log_error()

    def sync_ambient(self, now):
        """Ambient noise plays during focus sessions (or briefly, as a preview)."""
        choice = self.cfg.get("focus_sound", "off")
        previewing = self.preview_until and now < self.preview_until
        want = choice if choice in SND.AMBIENT and (self.state == "focus" or previewing) else None
        if want == self.player.ambient:
            return
        if not want:
            self.player.stop_ambient()
            return
        if not self.player.start_ambient(want, self.cfg.get("focus_sound_volume", 35)):
            # file not generated yet: build it in the background, next tick will pick it up
            if not getattr(self, "_gen_" + want, False):
                setattr(self, "_gen_" + want, True)
                self.run_bg(lambda: SND.ensure(want), lambda ok, v: setattr(self, "_gen_" + want, False))

    def set_focus_sound(self, name):
        self.cfg["focus_sound"] = name
        G.save_config(self.cfg)
        if name == "off":
            self.preview_until = None
            self.say("Focus sounds off. Silence it is.", timeout=4)
        else:
            if self.state != "focus":
                self.preview_until = dt.datetime.now() + dt.timedelta(seconds=6)
            self.say(f"Focus sound: {SND.AMBIENT[name]}. Plays during focus sessions"
                     + (" (6-second preview now)." if self.state != "focus" else "."), timeout=6)
        self.player.stop_ambient()
        self.sync_ambient(dt.datetime.now())

    def set_focus_volume(self, vol):
        self.cfg["focus_sound_volume"] = vol
        G.save_config(self.cfg)
        self.player.set_ambient_volume(vol)
        if self.state != "focus" and self.cfg.get("focus_sound", "off") != "off":
            self.preview_until = dt.datetime.now() + dt.timedelta(seconds=5)

    def toggle_alarm(self):
        self.cfg["alarm_sound"] = not self.cfg.get("alarm_sound", True)
        G.save_config(self.cfg)
        if self.cfg["alarm_sound"]:
            self.alarm("done")
            self.say("Timer alarm on. You'll hear it when a session or break ends.", timeout=5)
        else:
            self.say("Timer alarm off. Watch the clock yourself, then.", timeout=5)

    # ================= speech bubble =================
    def say(self, text, buttons=None, mood=None, timeout=10, sound=False, kind=None):
        self.clear_bubble()
        c, s, W = self.canvas, self.scale, self.W
        pad = int(10 * s)
        bottom = self.H - 16 * self.P - 4 - int(30 * s)
        tid = c.create_text(W // 2, 0, text=text, width=W - int(20 * s) - 2 * pad, font=self.font,
                            fill="#111111", justify="center", anchor="n", tags="bubble")
        x1, y1, x2, y2 = c.bbox(tid)
        th = y2 - y1
        bh = 0
        if buttons:
            f = tk.Frame(c, bg="white")
            rows = buttons if isinstance(buttons[0], list) else [buttons]  # one row, or several
            for i, row in enumerate(rows):
                rf = tk.Frame(f, bg="white")
                rf.pack(pady=(0 if i == 0 else 4, 0))
                for label, cb in row:
                    tk.Button(rf, text=label, command=lambda cb=cb: self._bubble_click(cb), bg=ACCENT, fg="white",
                              activebackground=ACCENT_DK, activeforeground="white", relief="flat", bd=0,
                              font=self.font_b, padx=9, pady=2, cursor="hand2").pack(side="left", padx=3)
            f.update_idletasks()
            bh = f.winfo_reqheight() + int(8 * s)
            self.bubble_frame = f
        total = th + 2 * pad + bh
        top = max(2, bottom - total)
        left, right = int(10 * s), W - int(10 * s)
        self.round_rect(left, top, right, top + total, int(12 * s), fill="white", outline="#222222",
                        width=2, tags="bubble")
        tw = int(8 * s)
        c.create_polygon(W // 2 - tw, top + total - 1, W // 2 + tw, top + total - 1, W // 2,
                         top + total + int(12 * s), fill="white", outline="#222222", width=2, tags="bubble")
        c.create_rectangle(W // 2 - tw + 2, top + total - 3, W // 2 + tw - 2, top + total + 1,
                           fill="white", outline="", tags="bubble")
        c.coords(tid, W // 2, top + pad)
        c.tag_raise(tid)
        if buttons:
            c.create_window(W // 2, top + pad + th + int(4 * s), window=self.bubble_frame, anchor="n", tags="bubble")
        self.bubble_sticky = bool(buttons)
        self.bubble_kind = kind
        if mood:
            self.mood_override = mood
            self.mood_until = dt.datetime.now() + dt.timedelta(seconds=timeout or 10)
        if sound:
            play()
        if timeout and not buttons:
            self.bubble_job = self.root.after(int(timeout * 1000), self.clear_bubble)

    def round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def clear_bubble(self):
        if self.bubble_job:
            self.root.after_cancel(self.bubble_job)
            self.bubble_job = None
        self.canvas.delete("bubble")
        if self.bubble_frame:
            self.bubble_frame.destroy()
            self.bubble_frame = None
        self.bubble_sticky = False
        self.bubble_kind = None

    def _bubble_click(self, cb):
        self.clear_bubble()
        cb()

    # ================= drawing =================
    def current_mood(self):
        now = dt.datetime.now()
        if self.celebrate_until and now < self.celebrate_until:
            return "celebrate"
        if self.mood_override and self.mood_until and now < self.mood_until:
            return self.mood_override
        st = self.state
        if st == "punish" or (st == "order" and self.nag_level >= 3):
            return "angry"
        if st == "break":
            return "sleepy"
        if st == "focus":
            return "focus"
        return "neutral"

    def pick_action(self, now):
        if self.action and now >= self.action_until:
            self.action = None
            self.next_action_at = now + dt.timedelta(seconds=random.uniform(8, 20))
        if self.analyzing:
            self.action = "dossier"
            self.action_until = now + dt.timedelta(seconds=1)
            return
        if not self.action and now >= self.next_action_at and not self.dragging:
            pool = ACTION_POOL.get(self.state, ACTION_POOL["idle"])
            if not pool:
                self.next_action_at = now + dt.timedelta(seconds=20)
                return
            self.action = random.choice(pool)
            self.action_until = now + dt.timedelta(seconds=ACTION_LEN[self.action])

    def draw(self):
        c, P, s = self.canvas, self.P, self.scale
        c.delete("sprite")
        now = dt.datetime.now()
        mood = self.current_mood()
        face = {"focus": "neutral", "celebrate": "happy"}.get(mood, mood)
        action = self.action
        rows = sprite_rows(face, action, self.frame)
        if mood == "celebrate":
            bounce = int(abs((self.frame % 6) - 3) * P * 0.5)
        elif action == "stretch":
            bounce = P // 2
        else:
            bounce = 1 if (self.frame // 6) % 2 else 0  # gentle breathing
        sw, sh = 14 * P, 16 * P
        x0 = (self.W - sw) // 2
        base_y0 = self.H - sh - 4
        y0 = base_y0 - bounce
        pal = dict(PALETTE)
        if mood == "angry":
            pal["F"] = ANGRY_FACE
        hat_lift = P // 2 if action == "adjust_hat" and self.frame % 4 < 2 else 0
        for r, row in enumerate(rows):
            dy = -hat_lift if r <= 3 else 0
            for col, ch in enumerate(row):
                if ch != ".":
                    c.create_rectangle(x0 + col * P, y0 + r * P + dy, x0 + (col + 1) * P, y0 + (r + 1) * P + dy,
                                       fill=pal[ch], outline="", tags="sprite")

        def rect(a, b, cc, d, color):
            c.create_rectangle(int(x0 + a * P), int(y0 + b * P), int(x0 + cc * P), int(y0 + d * P),
                               fill=color, outline="", tags="sprite")

        if mood == "focus":  # laptop + typing hands
            rect(3, 10, 11, 12.5, "#6c757d")
            if (self.frame // 12) % 2:
                rect(6.5, 10.8, 7.5, 11.6, "#4cc9f0")
            rect(2, 12.5, 12, 13.5, "#adb5bd")
            hx = 3.5 if (self.frame // 6) % 2 else 4.5  # slow, calm typing
            rect(hx, 12, hx + 1, 12.8, PALETTE["F"])
            rect(13 - hx - 1 + 0.5, 12, 13 - hx + 0.5, 12.8, PALETTE["F"])
        elif mood == "sleepy" and action != "sip":  # coffee on the side
            rect(14, 11, 16, 14, "#f8f9fa")
            rect(16, 11.5, 16.6, 13, "#f8f9fa")
            rect(14.3, 11, 15.7, 11.6, "#6f4518")
            off = 0.4 if (self.frame // 4) % 2 else 0
            rect(14.6 + off, 9.4, 15 + off, 10.4, "#adb5bd")
        elif mood == "angry":  # steam
            off = (self.frame // 2) % 2
            rect(0.5, -1.5 - off, 1.2, -0.5 - off, ACCENT)
            rect(12.8, -1.5 - (1 - off), 13.5, -0.5 - (1 - off), ACCENT)
        elif mood == "celebrate":  # sparkles
            for i, (a, b) in enumerate([(-2, 2), (15, 1), (-1, 8), (16, 7), (6.5, -2.5)]):
                if (self.frame + i) % 3:
                    rect(a, b, a + 0.8, b + 0.8, WARN)

        if action == "sip":  # cup to the mouth
            rect(10.5, 8, 12.5, 10.6, "#f8f9fa")
            rect(10.7, 8, 12.3, 8.5, "#6f4518")
            rect(12.5, 8.4, 13, 9.8, "#f8f9fa")
            rect(12, 10.6, 13, 11.4, PALETTE["F"])
        elif action == "dossier":  # reading a case file
            rect(2.5, 10.4, 11.5, 13.6, "#e9c46a")
            rect(3.2, 11, 10.8, 11.3, "#b08d2c")
            rect(3.2, 11.8, 9.5, 12.1, "#b08d2c")
            rect(3.2, 12.6, 10.2, 12.9, "#b08d2c")
            if self.frame % 6 < 3:
                rect(7, 10.4, 11.5, 13.6, "#f1dca7")  # page flip
            rect(1.6, 11.5, 2.6, 12.5, PALETTE["F"])
            rect(11.4, 11.5, 12.4, 12.5, PALETTE["F"])
        elif action == "check_watch":
            rect(0, 11.2, 1, 11.8, "#4cc9f0")

        if self.analyzing:  # thinking dots
            for i in range(3):
                if (self.frame // 2) % 4 > i:
                    rect(5 + i * 1.6, -2.2, 5.8 + i * 1.6, -1.4, "#e9ecef")

        if self.player.ambient:  # headphones while focus noise is playing
            rect(-0.5, 2.6, 14.5, 3.1, "#2b2d42")
            rect(-0.5, 2.6, 0.1, 5.2, "#2b2d42")
            rect(13.9, 2.6, 14.5, 5.2, "#2b2d42")
            rect(-0.9, 5, 0.6, 7.6, "#4cc9f0")
            rect(13.4, 5, 14.9, 7.6, "#4cc9f0")
            if (self.frame // 4) % 3 == 0:  # little music note
                rect(15.6, 1.2, 16.4, 2.0, "#4cc9f0")
                rect(16.2, -0.6, 16.5, 1.6, "#4cc9f0")

        # status pill above the hat; for timed phases it doubles as a progress bar
        txt, color = None, PANEL
        span = None  # (start, end) of the timed phase -> progress fill
        st = self.state
        if self.recording:
            txt, color = "● RECORDING", (ACCENT if self.frame % 6 < 3 else "#7a1020")
        elif self.analyzing:
            txt, color = "ANALYZING INTEL", "#5a4a8a"
        elif st == "focus" and self.focus_end and self.session:
            lvl = self.session["level"]
            tag = "STARTER" if self.session.get("starter") else lvl.upper()
            span = (self.session["start"], self.focus_end)
            color = LEVEL_COLORS.get(lvl, "#1f6f43")
            if self.block_strikes:
                tag += f" · STRIKE {self.block_strikes}/2"
                color = "#8a5a00"
        elif st == "break" and self.break_end:
            tag, color, span = "BREAK", "#2b4a7a", (self.break_start, self.break_end)
        elif st == "order":
            txt, color = "ORDER WAITING", (ACCENT if self.frame % 6 < 3 else "#7a1020")
        elif st == "punish":
            txt, color = "PENALTY", ACCENT
        elif self.paused_until and now < self.paused_until:
            txt, color = f"STAND DOWN {self.mmss(self.paused_until - now)}", "#444a57"
        elif self.off_duty_date == now.date():
            txt, color = "OFF DUTY", "#444a57"
        frac = None
        if span:
            start, end = span
            start = start or now
            M = self.g.M
            total = max(1.0, (end - start).total_seconds())
            frac = min(1.0, max(0.0, (now - start).total_seconds() / total))
            done_m = int(max(0, (now - start).total_seconds()) // M)
            total_m = max(1, round(total / M))
            txt = f"{tag}  {done_m}/{total_m} min  ·  {self.mmss(end - now)} left"
        if txt:
            tid = c.create_text(self.W // 2, base_y0 - int(8 * s) - (int(14 * s) if self.analyzing else 0),
                                text=txt, anchor="s", fill="white", font=("Segoe UI", 9, "bold"), tags="sprite")
            bx1, by1, bx2, by2 = c.bbox(tid)
            x1, y1, x2, y2 = bx1 - int(7 * s), by1 - int(2 * s), bx2 + int(7 * s), by2 + int(2 * s)
            if frac is None:
                c.create_rectangle(x1, y1, x2, y2, fill=color, outline="", tags="sprite")
            else:
                minw = int(230 * s)  # keep the bar wide enough to read at a glance
                if x2 - x1 < minw:
                    grow = (minw - (x2 - x1)) // 2
                    x1, x2 = x1 - grow, x2 + grow
                c.create_rectangle(x1, y1, x2, y2, fill="#1b1f2a", outline=color, width=1, tags="sprite")
                if frac > 0:
                    c.create_rectangle(x1, y1, x1 + int((x2 - x1) * frac), y2, fill=color, outline="",
                                       tags="sprite")
                # bright leading edge so movement is visible
                ex = x1 + int((x2 - x1) * frac)
                c.create_rectangle(max(x1, ex - 2), y1, ex, y2, fill="#ffffff" if self.frame % 6 < 3 else color,
                                   outline="", tags="sprite")
            c.tag_raise(tid)
        c.tag_raise("bubble")

    def animate(self):
        try:
            self.frame += 1
            self.pick_action(dt.datetime.now())
            self.draw()
        except Exception:
            log_error()
        self.root.after(160, self.animate)

    # ================= mouse =================
    def on_press(self, e):
        self.drag = (e.x_root, e.y_root, self.x, self.y)
        self.dragged = False

    def on_motion(self, e):
        if not self.drag:
            return
        dx, dy = e.x_root - self.drag[0], e.y_root - self.drag[1]
        if abs(dx) + abs(dy) > 4:
            self.dragged = self.dragging = True
            self.x, self.y = self.drag[2] + dx, self.drag[3] + dy
            self.root.geometry(f"+{self.x}+{self.y}")

    def on_release(self, e):
        self.dragging = False
        self.drag = None
        if self.dragged:
            self.g.data["pos"] = [self.x, self.y]
            self.g.save()
        else:
            self.on_click()

    def on_click(self):
        if self.bubble_kind == "controls":  # click again to hide the controls
            self.clear_bubble()
            return
        if self.bubble_sticky:
            return
        now = dt.datetime.now()
        st = self.state
        if st == "order":
            self.show_order()
        elif st == "focus":
            self.session_controls()
        elif st == "break":
            self.break_controls()
        elif st == "punish":
            self.ensure_punish_window(now, force_lift=True)
            self.say(T.pick(T.PUNISH_NAG), mood="angry", timeout=6)
        else:
            msg = T.pick(T.STATUS_IDLE, **self.ctx())
            self.say(msg, timeout=8)

    def on_right(self, e):
        m = tk.Menu(self.root, tearoff=0)
        st = self.state
        if st in ("order", "idle", "break"):
            if self.paused:
                m.add_command(label=f"▶ Resume paused session ({self.mmss(self.paused['remaining'])} left)",
                              command=self.resume_session)
            m.add_command(label="Start a session (choose task)…", command=self.open_session_picker)
        if st == "focus":
            ext = tk.Menu(m, tearoff=0)
            for n in (5, 10, 15, 30):
                ext.add_command(label=f"+{n} minutes", command=lambda x=n: self.extend_session(x))
            m.add_cascade(label="Extend session", menu=ext)
            m.add_command(label="✔ Task done (finish early)", command=self.finish_task)
            brk = tk.Menu(m, tearoff=0)
            for n in (5, 10, 15, 20):
                brk.add_command(label=f"{n} minutes", command=lambda x=n: self.take_break(x))
            m.add_cascade(label="Take a break (pause session)", menu=brk)
            m.add_command(label="Stop session (keep the minutes)", command=self.stop_session)
            if self.yt_lecture:
                m.add_command(label="📺 Lecture mode ON — turn off", command=lambda: self.set_yt_lecture(False))
            else:
                m.add_command(label="📺 I'm watching a lecture on YouTube", command=lambda: self.set_yt_lecture(True))
            m.add_command(label="Abandon order (counts as LOSS)", command=lambda: self.lose("abandoned"))
        if st == "break":
            m.add_command(label="End break now", command=lambda: self.break_over(dt.datetime.now()))
            m.add_command(label="Extend break +5 min", command=lambda: self.extend_break(5))
        m.add_separator()
        n_notes = len(self.g.data.get("notes", []))
        m.add_command(label=f"📝 Brain dump ({n_notes})" if n_notes else "📝 Brain dump",
                      command=self.open_brain_dump)
        m.add_command(label="Mission board", command=self.open_board)
        m.add_command(label="Edit plan / reschedule", command=self.open_plan_editor)
        m.add_command(label="Morning briefing", command=self.open_briefing)
        m.add_command(label=f"🏆 Ranked: {self.g.ladder_title()}", command=self.open_scoreboard)
        m.add_command(label="📊 Weekly status", command=self.open_weekly)
        m.add_separator()
        snd = tk.Menu(m, tearoff=0)
        cur = self.cfg.get("focus_sound", "off")
        for key, label in [("off", "Off")] + list(SND.AMBIENT.items()):
            snd.add_command(label=("● " if key == cur else "    ") + label,
                            command=lambda k=key: self.set_focus_sound(k))
        snd.add_separator()
        vol = self.cfg.get("focus_sound_volume", 35)
        for v, label in [(15, "Quiet"), (35, "Medium"), (60, "Loud")]:
            snd.add_command(label=("● " if v == vol else "    ") + f"Volume: {label}",
                            command=lambda x=v: self.set_focus_volume(x))
        snd.add_separator()
        snd.add_command(label=("● " if self.cfg.get("alarm_sound", True) else "    ") + "Alarm when a timer ends",
                        command=self.toggle_alarm)
        snd.add_command(label="    Test the alarm", command=lambda: self.alarm("done"))
        m.add_cascade(label="Sounds", menu=snd)
        m.add_separator()
        sd = tk.Menu(m, tearoff=0)
        for mins in (15, 30, 60):
            sd.add_command(label=f"{mins} minutes", command=lambda x=mins: self.stand_down(x))
        sd.add_command(label="Off duty for today", command=self.go_off_duty)
        m.add_cascade(label="Stand down", menu=sd)
        if self.paused_until or self.off_duty_date == dt.date.today():
            m.add_command(label="Back on duty", command=self.back_on_duty)
        m.add_command(label="End the day (debrief)", command=self.end_day)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    # ================= main loop =================
    def startup(self):
        days, day_no, total = self.g.countdown()
        msg = (f"The Boss is online. Day {day_no} of {total}, {days} days to the deadline.\n"
               "Right-click me for orders and missions. Drag me anywhere.")
        if DEMO:
            msg = "DEMO MODE: timers run about 30x faster.\n" + msg
        if getattr(self, "duty_note", None):
            msg = self.duty_note + "\n" + msg
            self.duty_note = None
        if self.restore_active():
            return
        self.say(msg, timeout=10)

    def _listen_for_summons(self):
        while True:
            try:
                conn, _ = _lock.accept()
                conn.close()
                self.summoned = True
            except OSError:
                return

    def summon(self):
        """Launched again (e.g. from the taskbar): come to the front, on a visible screen."""
        vx1, vy1, vx2, vy2 = virtual_screen()
        if not (vx1 - self.W // 2 <= self.x <= vx2 - self.W // 2 and vy1 - self.H // 2 <= self.y <= vy2 - self.H // 2):
            wa = work_area()
            self.x, self.y = wa[2] - self.W - int(40 * self.scale), wa[3] - self.H
            self.root.geometry(f"+{self.x}+{self.y}")
        self.root.attributes("-topmost", True)
        self.root.lift()
        for key in ("punish", "briefing", "debrief"):
            if self.windows_open(key):
                self.lift_if_open(key)
                return
        if not self.bubble_sticky:
            self.say("Already on duty, agent. Right-click me for options.", timeout=5)

    def tick(self):
        try:
            if self.summoned:
                self.summoned = False
                self.summon()
            self._tick()
        except Exception:
            log_error()
        self.root.after(1000, self.tick)

    def _tick(self):
        now = dt.datetime.now()
        g = self.g
        self.tick_n += 1
        if now.date() != self.today:
            self.today = now.date()
            self.yt_ok.clear()
            self.yt_bad.clear()
        if self.tick_n % 5 == 0:
            self.root.attributes("-topmost", True)
            self.root.lift()
        if self.paused_until and now >= self.paused_until:
            self.paused_until = None
            self.save_duty()
            self.last_activity = now - dt.timedelta(seconds=max(0, g.secs("off_duty_nag_min") - g.M))
            self.say("Stand-down over. Back on duty, agent.", timeout=6)
            self.alarm("start")

        self.check_wake(now)

        if g.data["pending_punishment"]:
            self.state = "punish"
            self.ensure_punish_window(now)

        st = self.state
        if st == "focus":
            self.tick_focus(now)
        elif st == "break":
            if now >= self.break_end:
                self.break_over(now)
        elif st == "order":
            self.tick_order(now)
        elif st == "idle":
            self.tick_idle(now)

        self.sync_ambient(now)
        self.check_day_end(now)
        snap = self.active_snapshot(now)
        changed = (snap is None) != (g.data.get("active") is None)
        g.data["active"] = snap
        if changed or (snap and self.tick_n % 5 == 0) or self.tick_n % 30 == 0:
            g.save()

    # ================= crash recovery =================
    def active_snapshot(self, now):
        """The running (or paused) session, in a form that survives the app closing."""
        if self.state == "focus" and self.session and self.focus_end:
            s, rem, strikes = self.session, self.focus_end - now, self.block_strikes
        elif self.paused:
            s, rem, strikes = self.paused["session"], self.paused["remaining"], self.paused["strikes"]
        else:
            return None
        s = dict(s)
        s["start"] = s["start"].isoformat()
        return {"session": s, "remaining_sec": max(0, int(rem.total_seconds())), "strikes": strikes,
                "yt_lecture": self.yt_lecture, "saved_at": now.isoformat(timespec="seconds")}

    def restore_active(self):
        a = self.g.data.get("active")
        if not a:
            return False
        now = dt.datetime.now()
        try:
            saved_at = dt.datetime.fromisoformat(a["saved_at"])
            s = dict(a["session"])
            s["start"] = dt.datetime.fromisoformat(s["start"])
            remaining = dt.timedelta(seconds=int(a["remaining_sec"]))
        except (KeyError, ValueError, TypeError):
            self.g.data["active"] = None
            return False
        if saved_at.date() != now.date() or remaining.total_seconds() < 30:
            self.g.data["active"] = None
            self.g.save()
            return False
        if s.get("mission") and not self.g.find(s["mission"]):
            s["mission"] = None
        # Restore as a paused session: time the app was closed counts neither as work nor as a loss.
        self.paused = {"session": s, "remaining": remaining, "strikes": a.get("strikes", 0), "at": now}
        self.yt_lecture = bool(a.get("yt_lecture"))
        self.say(T.RESTORED.format(mission=s.get("mission") or "general prep", left=self.mmss(remaining)),
                 buttons=[("▶ Resume", self.resume_session), ("Discard", self.discard_restored)],
                 sound=True, kind="restore")
        return True

    def discard_restored(self):
        self.paused = None
        self.g.data["active"] = None
        self.g.save()
        self.say("Session discarded. Right-click me when you're ready for the next one.", timeout=6)

    def tick_idle(self, now):
        if self.bubble_kind == "restore":
            return
        if not self.on_duty(now) or self.windows_open("briefing") or self.analyzing:
            return
        if (now - self.last_activity).total_seconds() >= self.g.secs("off_duty_nag_min"):
            self.issue_order()

    # ================= orders =================
    def order_text(self, nag_line=None):
        m = self.g.current_mission()
        text = nag_line or T.pick(T.ORDER, **self.ctx())
        if m and m.get("first_step"):
            text += "\n" + T.FIRST_MOVE.format(first=m["first_step"])
        return text + "\n" + T.FOCUS_ASK

    def show_order(self, nag_line=None, sound=False, mood=None):
        self.say(self.order_text(nag_line), buttons=self.level_buttons(), sound=sound, mood=mood)

    def issue_order(self, prefix=None):
        g = self.g
        if g.data["pending_punishment"]:
            self.say(T.pick(T.PUNISH_NAG), mood="angry", timeout=6)
            return
        now = dt.datetime.now()
        self.state = "order"
        self.break_end = None
        self.order_t0 = now
        self.nag_level = 0
        self.next_nag = now + g.td("nag_every_min")
        line = T.pick(T.ORDER, **self.ctx())
        if prefix:
            line = prefix + "\n" + line
        self.show_order(line, sound=True)

    def tick_order(self, now):
        g = self.g
        g.day()["wasted_sec"] += 1
        if self.windows_open("session"):  # they're choosing a task: no nagging, no timeout
            self.order_t0 += dt.timedelta(seconds=1)
            self.next_nag += dt.timedelta(seconds=1)
            return
        if (now - self.order_t0).total_seconds() >= g.secs("order_timeout_min"):
            self.lose("ignored")
            return
        if now >= self.next_nag:
            self.nag_level = min(4, self.nag_level + 1)
            self.next_nag = now + g.td("nag_every_min")
            self.show_order(T.pick(T.NAG[self.nag_level], **self.ctx()), sound=True,
                            mood="angry" if self.nag_level >= 3 else None)

    def accept_order(self, level=None):
        self.open_session_picker()

    # ================= starting a session (you choose the task and the length) =================
    @staticmethod
    def level_for(minutes):
        return "low" if minutes <= 30 else ("medium" if minutes <= 60 else "high")

    def default_length(self):
        d = self.g.day()
        return int(d.get("last_minutes") or self.g.lv(self.cfg["default_level"])["focus"])

    def can_start(self):
        if self.g.data["pending_punishment"]:
            self.say(T.pick(T.PUNISH_NAG), mood="angry", timeout=6)
            return False
        return self.state in ("order", "idle", "break")

    def quick_start(self):
        m = self.g.current_mission()
        self.start_session(m["name"] if m else None, self.default_length())

    def open_session_picker(self):
        if not self.can_start():
            return
        if self.lift_if_open("session"):
            return
        g = self.g
        open_ms = g.open_missions()
        cur = g.current_mission()
        if self.paused:
            cur = g.find(self.paused["session"]["mission"]) or cur
        w = self.dialog("session", "Start a Session")
        self.L(w, "START A SESSION", 14, True, ACCENT).pack(anchor="w")
        self.L(w, "What are you working on?", 10, True).pack(anchor="w", pady=(8, 2))
        choice = tk.StringVar(value=cur["name"] if cur else "")
        radio = dict(bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                     font=self.font, anchor="w")
        for m in open_ms:
            over = "  · over estimate" if m["done_min"] >= m["est_min"] else ""
            tk.Radiobutton(w, text=f"{m['name']}    {m['done_min']}/{m['est_min']} min  ·  {m['difficulty']}{over}",
                           variable=choice, value=m["name"], **radio).pack(anchor="w")
        tk.Radiobutton(w, text="Something not on the list / general prep", variable=choice, value="",
                       **radio).pack(anchor="w")
        newr = tk.Frame(w, bg=BG)
        newr.pack(anchor="w", pady=(4, 0))
        self.L(newr, "Or a new task:", 9, fg=MUTED).pack(side="left")
        new_name = self.E(newr, 30)
        new_name.pack(side="left", padx=6, ipady=2)
        self.L(newr, "estimate", 9, fg=MUTED).pack(side="left")
        new_est = self.SPIN(newr, 5, 600, 5, 50)
        new_est.pack(side="left", padx=4)
        self.L(newr, "min", 9, fg=MUTED).pack(side="left")

        self.L(w, "How long is this session?", 10, True).pack(anchor="w", pady=(12, 2))
        length = tk.StringVar(value=str(self.default_length()))
        pr = tk.Frame(w, bg=BG)
        pr.pack(anchor="w")
        for v in (15, 25, 45, 50, 60, 90):
            tk.Radiobutton(pr, text=f"{v} min", variable=length, value=str(v), indicatoron=0, bg=PANEL, fg=FG,
                           selectcolor=ACCENT, activebackground="#30384a", activeforeground="white", relief="flat",
                           bd=0, font=self.font, padx=8, pady=3).pack(side="left", padx=2)
        self.L(pr, "  or", 9, fg=MUTED).pack(side="left")
        tk.Spinbox(pr, from_=5, to=240, increment=5, width=5, textvariable=length, font=self.font, bg=PANEL,
                   fg=FG, buttonbackground=PANEL, insertbackground=FG, relief="flat").pack(side="left", padx=6)
        self.L(w, "Estimates are guesses. During the session, click me to add +5 / +10 / +15 min, mark the task "
                  "done early, or pause for a break.", 9, fg=MUTED, wraplength=int(520 * self.scale)
               ).pack(anchor="w", pady=(10, 0))
        err = self.L(w, "", 9, fg=ACCENT)
        err.pack(anchor="w")

        def go():
            try:
                mins = int(float(length.get()))
            except ValueError:
                err.config(text="Length must be minutes, like 25 or 40.")
                return
            if not 1 <= mins <= 600:
                err.config(text="Pick between 1 and 600 minutes.")
                return
            name = choice.get()
            nn = new_name.get().strip()
            if nn:
                if not g.find(nn):
                    try:
                        est = int(float(new_est.get()))
                    except ValueError:
                        est = 50
                    g.day()["missions"].append(G.Game.make_mission(nn, est))
                    g.day()["briefed"] = True
                name = nn
            w.destroy()
            self.start_session(name or None, mins)
            self.refresh_board()

        btns = tk.Frame(w, bg=BG)
        btns.pack(anchor="e", pady=(8, 0))
        self.B(btns, "Cancel", w.destroy, accent=False).pack(side="left", padx=6)
        self.B(btns, "Start  ▶", go).pack(side="left")
        w.bind("<Return>", lambda e: go())
        self.show(w)

    def start_session(self, name, minutes):
        if not self.can_start():
            return
        g = self.g
        now = dt.datetime.now()
        if self.paused:
            self.drop_paused(now)
        d = g.day()
        m = g.find(name)
        level = self.level_for(minutes)
        d["last_level"] = level
        d["last_minutes"] = minutes
        if m:
            d["active"] = m["name"]
        g.save()
        self.session = {"level": level, "mission": m["name"] if m else None, "minutes": minutes,
                        "starter": None, "start": now}
        self.state = "focus"
        self.focus_end = now + g.tdm(minutes)
        self.break_end = self.order_t0 = None
        self.nag_level = 0
        self.block_strikes = 0
        self.distracted_since = None
        self.yt_pending = None
        self.yt_lecture = False
        self.idle_prompt_at = None
        if self.windows_open("session"):
            self.windows["session"].destroy()
        self.say(T.SESSION_START.format(minutes=minutes, mission=m["name"] if m else "general prep")
                 + "\n" + T.SESSION_HINT, mood="happy", timeout=6)

    # ================= during a session =================
    def session_controls(self):
        now = dt.datetime.now()
        s = self.session
        name = s["mission"] or "general prep"
        text = (f"{self.worked_min(now)} min in, {self.mmss(self.focus_end - now)} left on: {name}.\n"
                f"Strikes: {self.block_strikes}/2. Need more time, done early, or a break?")
        done_label = "✔ Task done" if s["mission"] else "✔ Done"
        self.say(text, buttons=[[("+5 min", lambda: self.extend_session(5)),
                                 ("+10 min", lambda: self.extend_session(10)),
                                 ("+15 min", lambda: self.extend_session(15))],
                                [(done_label, self.finish_task), ("Break", self.ask_break),
                                 ("■ Stop", self.stop_session)]], kind="controls")
        self.root.after(15000, lambda: self.bubble_kind == "controls" and self.clear_bubble())

    def ask_break(self):
        if self.state != "focus":
            return
        self.say("Pause and take a break. How long?",
                 buttons=[(f"{n} min", lambda n=n: self.take_break(n)) for n in (5, 10, 15)]
                 + [("Cancel", lambda: None)], kind="controls")
        self.root.after(15000, lambda: self.bubble_kind == "controls" and self.clear_bubble())

    def extend_session(self, n):
        if self.state != "focus" or not self.session:
            return
        s = self.session
        self.focus_end += self.g.tdm(n)
        s["minutes"] += n
        s["level"] = self.level_for(s["minutes"])
        self.say(T.EXTENDED.format(n=n, minutes=s["minutes"], mission=s["mission"] or "general prep"),
                 mood="happy", timeout=4)

    def finish_task(self):
        """Task finished before the timer: log the real minutes, mark it done, take the break."""
        if self.state != "focus" or not self.session:
            return
        now = dt.datetime.now()
        self.complete_block(now, max(1, self.worked_min(now)), mark_done=True)

    def stop_session(self):
        """End the session early without finishing the task. Long enough = win, short = no penalty."""
        if self.state != "focus" or not self.session:
            return
        now = dt.datetime.now()
        s = self.session
        worked = self.worked_min(now)
        if worked >= min(25, max(10, s["minutes"] // 2)):
            self.complete_block(now, worked, note=T.STOPPED_WIN.format(worked=worked))
            return
        m = self.g.find(s["mission"])
        if m and worked > 0:
            m["done_min"] += worked
        self.g.save()
        self.state = "idle"
        self.focus_end = None
        self.session = None
        self.last_activity = now
        self.player.stop_ambient()
        self.say(T.STOPPED_SHORT.format(worked=worked), timeout=8)
        self.open_debrief(m)

    def take_break(self, mins):
        """Pause the running session for a break; it resumes with the same time left."""
        if self.state != "focus" or not self.session:
            return
        now = dt.datetime.now()
        s = self.session
        self.paused = {"session": s, "remaining": self.focus_end - now, "strikes": self.block_strikes, "at": now}
        self.state = "break"
        self.focus_end = None
        self.break_start = now
        self.break_end = now + self.g.tdm(mins)
        self.last_activity = now
        self.idle_prompt_at = None
        self.player.stop_ambient()
        self.say(T.PAUSED.format(worked=self.worked_min(now), left=self.mmss(self.paused["remaining"]),
                                 mission=s["mission"] or "general prep", mins=mins), timeout=8)

    def resume_session(self):
        p = self.paused
        if not p or not self.can_start():
            return
        now = dt.datetime.now()
        s = p["session"]
        s["start"] += now - p["at"]  # the pause doesn't count as work time
        if s["mission"] and not self.g.find(s["mission"]):
            s["mission"] = None
        self.paused = None
        self.session = s
        self.state = "focus"
        self.focus_end = now + p["remaining"]
        self.block_strikes = p["strikes"]
        self.break_end = self.order_t0 = None
        self.nag_level = 0
        self.distracted_since = None
        self.idle_prompt_at = None
        if self.windows_open("session"):
            self.windows["session"].destroy()
        self.say(T.RESUMED.format(mission=s["mission"] or "general prep", left=self.mmss(p["remaining"])),
                 mood="happy", timeout=5)

    def break_controls(self):
        now = dt.datetime.now()
        text = f"Break: {self.mmss(self.break_end - now)} left. Hydrate. Stretch."
        if self.paused:
            text += f"\nPaused: {self.paused['session']['mission'] or 'general prep'}."
        self.say(text, buttons=[("End break now", lambda: self.break_over(dt.datetime.now())),
                                ("+5 min", lambda: self.extend_break(5))], kind="controls")
        self.root.after(12000, lambda: self.bubble_kind == "controls" and self.clear_bubble())

    def extend_break(self, n):
        if self.state == "break" and self.break_end:
            self.break_end += self.g.tdm(n)
            self.say(f"Break extended. {self.mmss(self.break_end - dt.datetime.now())} left.", timeout=4)

    def tick_focus(self, now):
        self.g.day()["focus_sec"] += 1
        if now >= self.focus_end:
            if self.session.get("starter"):
                self.starter_reached(now)
            else:
                self.complete_block(now)
            return
        if self.tick_n % 2 == 0:
            self.check_distraction(now)
        if self.state == "focus":
            self.check_idle(now)

    def starter_reached(self, now):
        s = self.session
        starter = s["starter"]
        s["starter"] = None
        self.focus_end = now + self.g.tdm(s["minutes"] - starter)
        self.say(T.STARTER_DONE.format(starter=starter, minutes=s["minutes"]),
                 buttons=[(T.STARTER_STOP.format(starter=starter),
                           lambda: self.state == "focus" and self.complete_block(dt.datetime.now(), starter))],
                 mood="happy", kind="starter")
        self.alarm("soft")
        self.root.after(30000, lambda: self.bubble_kind == "starter" and self.clear_bubble())

    def check_distraction(self, now):
        title = foreground_title()
        low = title.lower()
        if not title or title.startswith("The Boss"):
            self.distracted_since = None
            return
        hits = [k for k in self.cfg["distraction_keywords"] if k.lower() in low]
        if not hits:
            self.distracted_since = None
            return
        app = next((k for k in hits if k.lower() != "youtube"), None)
        if app is None:  # only YouTube: could be a lecture
            if self.yt_lecture:  # you already said "lecture" this session
                self.distracted_since = None
                return
            key = yt_key(title)
            if key in self.yt_ok:
                self.distracted_since = None
                return
            if key not in self.yt_bad:
                # Not answered yet: keep asking, never penalise silence.
                if self.bubble_kind != "yt" and (self.yt_pending is None or
                                                 (now - self.yt_pending[1]).total_seconds() >= 45):
                    self.yt_pending = (key, now)
                    self.say(T.YT_ASK, buttons=[("📚 Lecture", lambda k=key: self.yt_answer(k, True)),
                                                ("🎮 Timepass", lambda k=key: self.yt_answer(k, False))],
                             sound=True, kind="yt")
                self.distracted_since = None
                return
            if self.yt_grace_until and now < self.yt_grace_until:
                return
            app = "YouTube"
        self.g.day()["wasted_sec"] += 2
        if self.distracted_since is None or (now - self.distracted_since).total_seconds() >= self.grace:
            self.distracted_since = now
            self.strike(T.pick(T.DISTRACTED, **self.ctx(app=app.title() if app != "YouTube" else app)))

    def yt_answer(self, key, lecture):
        self.yt_pending = None
        if lecture:
            self.yt_lecture = True  # trust YouTube for the rest of this session (next videos too)
            self.yt_ok.add(key)
            self.say(T.YT_LECTURE_OK, mood="happy", timeout=5)
        else:
            self.yt_bad.add(key)
            self.yt_grace_until = dt.datetime.now() + dt.timedelta(seconds=15)
            self.say("Honest. Respect. Now close it. You have 15 seconds.", mood="angry", timeout=8)

    def set_yt_lecture(self, on):
        self.yt_lecture = on
        self.yt_pending = None
        if self.bubble_kind == "yt":
            self.clear_bubble()
        self.say(T.YT_LECTURE_OK if on else "Lecture mode off. YouTube is watched again.", timeout=5)

    def check_idle(self, now):
        idle = idle_seconds()
        lim = self.g.secs("idle_min")
        if idle < lim:
            if self.idle_prompt_at:
                self.idle_prompt_at = None
                if self.bubble_kind == "idle":
                    self.clear_bubble()
            return
        if self.idle_prompt_at is None:
            self.idle_prompt_at = now
            self.g.day()["idle_pokes"] += 1
            self.say(T.pick(T.IDLE_ASK), buttons=[("I'm here", self.idle_ack)], sound=True, kind="idle")
        elif (now - self.idle_prompt_at).total_seconds() >= lim:
            self.idle_prompt_at = now
            self.strike(T.IDLE_STRIKE)

    def idle_ack(self):
        self.idle_prompt_at = None
        self.say("Copy that. Carry on.", timeout=3)

    def strike(self, message):
        g = self.g
        self.block_strikes += 1
        g.day()["distractions"] += 1
        g.save()
        if self.block_strikes >= 2:
            self.lose("compromised")
        else:
            self.say(message, mood="angry", sound=True, timeout=12)

    def complete_block(self, now, minutes=None, mark_done=False, note=None):
        if self.state != "focus" or not self.session:
            return
        g = self.g
        d = g.day()
        s = self.session
        minutes = minutes or s["minutes"]
        d["focus_blocks"] += 1
        m = g.find(s["mission"])
        if m:
            m["done_min"] += minutes
        res = g.win(minutes)
        change = res["change"]
        # break scales with how long you actually worked
        brk = g.lv(self.level_for(minutes))["break"]
        self.state = "break"
        self.focus_end = None
        self.session = None
        self.break_start = now
        self.break_end = now + g.tdm(brk)
        self.last_activity = now
        msg = T.pick(T.WIN, **self.ctx(gain=res["gain"]))
        if note:
            msg = note + "\n" + msg
        if mark_done:
            if m and not m["completed"]:
                m["completed"] = True
                if d.get("active") == m["name"]:
                    d["active"] = None
                change = g.add_xp(G.MISSION_XP) or change
                msg = T.DONE_EARLY.format(mission=m["name"], worked=minutes) + f" +{G.MISSION_XP} XP.\n" + msg
            g.save()
        if res["bonus"]:
            msg += "\n" + T.pick(T.STREAK_BONUS, **self.ctx())
        msg += "\n" + self.ladder_line(res["ladder"])
        quests = res["quests"] + (g.after_change() if mark_done else [])
        self.announce_quests(quests, delay=16000)
        msg += f"\n{brk}-minute break. " + T.pick(T.BREAK)
        self.celebrate(4)
        self.say(msg, mood="happy", timeout=15)
        self.player.stop_ambient()
        self.alarm("done")
        self.rank_change(change, delay=8000)
        self.open_debrief(m)
        if mark_done and d["missions"] and not g.open_missions():
            self.root.after(5000, lambda: self.say(T.ALL_DONE, mood="happy", timeout=10))

    def break_over(self, now):
        if self.state != "break":
            return
        self.state = "idle"
        self.break_end = None
        self.last_activity = now
        self.alarm("start")
        if self.paused:
            p = self.paused
            self.issue_order(prefix=T.RESUME.format(left=self.mmss(p["remaining"]),
                                                    mission=p["session"]["mission"] or "general prep"))
        elif self.on_duty(now):
            self.issue_order(prefix=T.BREAK_OVER)
        else:
            self.say("Break over. You're off duty now. Right-click me if you want another session.", timeout=8)

    def lose(self, reason):
        g = self.g
        self.drop_paused(dt.datetime.now())
        if self.session and self.state == "focus":
            minutes = self.session["minutes"]
        else:
            minutes = g.lv(g.day().get("last_level") or self.cfg["default_level"])["focus"]
        self.state = "punish"
        self.focus_end = None
        self.order_t0 = None
        self.nag_level = 0
        res = g.loss(reason, minutes)
        self.say(T.pick(T.LOSS[reason], **self.ctx()) + f" (-{res['lost']} XP)\n"
                 + self.ladder_line(res["ladder"]) + "\n" + T.PUNISH_INTRO,
                 mood="angry", timeout=15)
        play("SystemHand")
        self.rank_change(res["change"], delay=9000)
        self.open_punishment()
        self.root.after(16000, self.warn_at_risk)

    def warn_at_risk(self):
        _, risk = self.g.replan()
        if risk and self.state != "focus":
            self.say(T.pick(T.AT_RISK, title=", ".join(risk[:2])), mood="angry", timeout=10)

    # ================= stand down =================
    def stand_down(self, mins):
        now = dt.datetime.now()
        self.paused_until = now + self.g.tdm(mins)
        self.stand_down_from = now
        self.save_duty()
        msg = T.pick(T.STAND_DOWN, mins=mins)
        self.drop_paused(now)
        if self.state == "focus":
            self.credit_partial(now)
            msg = T.STAND_DOWN_MID + "\n" + msg
        if self.state in ("focus", "order", "break"):
            self.state = "idle"
            self.focus_end = self.break_end = self.order_t0 = None
        self.say(msg, timeout=8)

    def go_off_duty(self):
        now = dt.datetime.now()
        self.off_duty_date = now.date()
        self.save_duty()
        self.drop_paused(now)
        self.credit_partial(now)
        if self.state in ("focus", "order", "break"):
            self.state = "idle"
            self.focus_end = self.break_end = self.order_t0 = None
        self.say(T.OFF_DUTY, mood="angry", timeout=8)

    def save_duty(self):
        """Stand-down / off-duty survive a shutdown: stored as real clock times, not countdowns."""
        d = self.g.data
        d["stand_down_until"] = self.paused_until.isoformat(timespec="seconds") if self.paused_until else None
        d["stand_down_from"] = (self.stand_down_from.isoformat(timespec="seconds")
                                if self.paused_until and self.stand_down_from else None)
        d["off_duty_date"] = self.off_duty_date.isoformat() if self.off_duty_date else None
        self.g.save()

    def load_duty(self, now):
        d = self.g.data
        try:
            until = d.get("stand_down_until")
            self.paused_until = dt.datetime.fromisoformat(until) if until else None
            frm = d.get("stand_down_from")
            self.stand_down_from = dt.datetime.fromisoformat(frm) if frm else None
            off = d.get("off_duty_date")
            self.off_duty_date = dt.date.fromisoformat(off) if off else None
        except ValueError:
            self.paused_until = self.stand_down_from = self.off_duty_date = None
        self.duty_note = None
        if self.paused_until:
            since = f" at {self.stand_down_from:%H:%M}" if self.stand_down_from else ""
            if now < self.paused_until:
                self.duty_note = (f"Still standing down: you called it{since}, it ends at "
                                  f"{self.paused_until:%H:%M}. {self.mmss(self.paused_until - now)} left.")
            else:
                self.duty_note = f"Your stand-down{since} ended at {self.paused_until:%H:%M} while I was offline."
                self.paused_until = self.stand_down_from = None
                d["stand_down_until"] = d["stand_down_from"] = None
                self.g.save()
        if self.off_duty_date and self.off_duty_date != now.date():
            self.off_duty_date = None  # yesterday's "off duty" doesn't carry over

    def back_on_duty(self):
        now = dt.datetime.now()
        self.paused_until = None
        self.off_duty_date = None
        self.save_duty()
        self.last_activity = now - dt.timedelta(seconds=max(0, self.g.secs("off_duty_nag_min") - self.g.M))
        self.say("Back on duty. Orders incoming.", mood="happy", timeout=6)

    # ================= wake / day end =================
    def check_wake(self, now):
        d = self.g.day()
        if d["briefed"] or self.windows_open("briefing") or self.off_duty_date == now.date():
            return
        t = now.time()
        if t < G.hm(self.cfg["wake_time"]) or t >= G.hm(self.cfg["study_end"]):
            return
        if self.brief_snooze_until and now < self.brief_snooze_until:
            return
        if self.woke_date != now.date():
            self.woke_date = now.date()
            self.say(T.pick(T.WAKE, **self.ctx()), sound=True, timeout=30)
            for i in range(3):
                self.root.after(i * 1300, lambda: play("SystemHand"))
        self.open_briefing()

    def check_day_end(self, now):
        d = self.g.day()
        if not d["briefed"] or d["debriefed"] or self.state in ("focus", "punish"):
            return
        if now >= self.g.day_end(now):
            self.open_day_debrief()

    def end_day(self):
        now = dt.datetime.now()
        d = self.g.day()
        d["day_end"] = now.isoformat(timespec="seconds")
        if self.state in ("order", "break", "idle"):
            self.state = "idle"
            self.break_end = self.order_t0 = None
        self.g.save()
        self.open_day_debrief()

    # ================= AI analysis =================
    def run_analysis(self, text, hours, end, done_cb):
        """Calls Gemini in a background thread; done_cb(result, note) runs on the UI thread."""
        cfg = self.cfg
        now = dt.datetime.now()
        key = cfg.get("gemini_api_key", "").strip()
        if not key:
            done_cb(gemini.offline_parse(text, now), T.NO_KEY)
            return
        self.analyzing = True
        self.say(T.ANALYZING, timeout=40)
        q = queue.Queue()

        def work():
            try:
                res, model = gemini.analyze(text, key, cfg["gemini_model"], now, hours, end, cfg["deadline"],
                                            cfg.get("gemini_fallback_models", []))
                q.put((res, None, model))
            except Exception as e:
                q.put((gemini.offline_parse(text, now), T.AI_FAIL.format(err=str(e)[:90]), None))

        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                res, note, model = q.get_nowait()
            except queue.Empty:
                self.root.after(200, poll)
                return
            self.analyzing = False
            if self.bubble_kind is None:
                self.clear_bubble()
            if model and model != cfg["gemini_model"]:
                cfg["gemini_model"] = model
                G.save_config(cfg)
            try:
                done_cb(res, note)
            except Exception:
                log_error()
        self.root.after(200, poll)

    def run_bg(self, fn, done):
        """Run fn() in a background thread; done(ok, result_or_error) runs on the UI thread."""
        q = queue.Queue()

        def work():
            try:
                q.put((True, fn()))
            except Exception as e:
                q.put((False, str(e)[:120]))
        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                ok, val = q.get_nowait()
            except queue.Empty:
                self.root.after(200, poll)
                return
            try:
                done(ok, val)
            except Exception:
                log_error()
        self.root.after(200, poll)

    # ================= voice =================
    def toggle_voice(self, btn, dump, status, key_entry):
        cfg = self.cfg
        if self.recorder and self.recorder.active:
            try:
                wav = self.recorder.stop()
            except Exception as e:
                self.recording = False
                btn.config(text="🎤 Speak")
                status.config(text=str(e), fg=ACCENT)
                return
            self.recording = False
            btn.config(text="Transcribing...", state="disabled")
            status.config(text="Sending your voice note to Gemini...", fg=MUTED)
            self.analyzing = True
            key = cfg["gemini_api_key"].strip()

            def work():
                return gemini.transcribe(wav, key, cfg["gemini_model"], cfg.get("gemini_fallback_models", []))

            def done(ok, val):
                self.analyzing = False
                try:
                    btn.config(text="🎤 Speak", state="normal")
                except tk.TclError:
                    return  # window was closed
                if ok:
                    text, model = val
                    if model != cfg["gemini_model"]:
                        cfg["gemini_model"] = model
                        G.save_config(cfg)
                    sep = "\n" if dump.get("1.0", "end").strip() else ""
                    dump.insert("end", sep + text)
                    status.config(text="Got it. Fix anything I misheard, then press Analyze.", fg=GOOD)
                else:
                    status.config(text=f"Couldn't transcribe ({val}). Try again, or use Win+H to dictate.", fg=ACCENT)
            self.run_bg(work, done)
            return

        if key_entry is not None and key_entry.get().strip():
            cfg["gemini_api_key"] = key_entry.get().strip()
            G.save_config(cfg)
        if not cfg.get("gemini_api_key", "").strip():
            status.config(text="Voice needs your Gemini key. Or press Win+H to use Windows voice typing.", fg=ACCENT)
            return
        try:
            self.recorder = gemini.Recorder(os.path.join(tempfile.gettempdir(), "boss_voice.wav"))
            self.recorder.start()
        except Exception as e:
            status.config(text=f"{e}. Check your mic, or press Win+H to dictate instead.", fg=ACCENT)
            return
        self.recording = True
        btn.config(text="■  Stop & transcribe")
        status.config(text="Recording... say your whole plan, then press Stop (max 3 min).", fg=WARN)

        def auto_stop():
            if self.recorder and self.recorder.active:
                try:
                    if btn.winfo_exists():
                        self.toggle_voice(btn, dump, status, None)
                        return
                except tk.TclError:
                    pass
                self.stop_recording()
        self.root.after(180000, auto_stop)

    def stop_recording(self):
        self.recording = False
        if self.recorder and self.recorder.active:
            try:
                self.recorder.stop()
            except Exception:
                pass

    # ================= windows =================
    def windows_open(self, key):
        w = self.windows.get(key)
        return bool(w and w.winfo_exists())

    def lift_if_open(self, key):
        if self.windows_open(key):
            w = self.windows[key]
            w.deiconify()
            w.lift()
            w.focus_force()
            return True
        return False

    def dialog(self, key, title, on_close=None):
        s = self.scale
        w = tk.Toplevel(self.root)
        w.withdraw()
        w.title(f"The Boss - {title}")
        w.configure(bg=BG, padx=int(18 * s), pady=int(14 * s))
        w.attributes("-topmost", True)
        w.resizable(False, False)
        if on_close:
            w.protocol("WM_DELETE_WINDOW", on_close)
        self.windows[key] = w
        return w

    def show(self, w):
        w.update_idletasks()
        x = (w.winfo_screenwidth() - w.winfo_reqwidth()) // 2
        y = max(0, (w.winfo_screenheight() - w.winfo_reqheight()) // 4)
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        w.lift()
        w.focus_force()

    def L(self, parent, text, size=10, bold=False, fg=FG, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg, justify="left",
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kw)

    def B(self, parent, text, cmd, accent=True):
        return tk.Button(parent, text=text, command=cmd, bg=ACCENT if accent else PANEL, fg="white",
                         activebackground=ACCENT_DK if accent else "#30384a", activeforeground="white",
                         relief="flat", bd=0, font=self.font_b, padx=12, pady=4, cursor="hand2")

    def TXT(self, parent, height, width=54):
        return tk.Text(parent, height=height, width=width, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                       font=self.font, padx=8, pady=6, wrap="word")

    def E(self, parent, width, value="", **kw):
        e = tk.Entry(parent, width=width, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=self.font, **kw)
        e.insert(0, value)
        return e

    def SPIN(self, parent, lo, hi, step, value, width=5):
        sp = tk.Spinbox(parent, from_=lo, to=hi, increment=step, width=width, font=self.font, bg=PANEL, fg=FG,
                        buttonbackground=PANEL, insertbackground=FG, relief="flat")
        sp.delete(0, "end")
        sp.insert(0, str(value))
        return sp

    def OPT(self, parent, var, values):
        o = tk.OptionMenu(parent, var, *values)
        o.config(bg=PANEL, fg=FG, activebackground="#30384a", activeforeground=FG, relief="flat", bd=0,
                 highlightthickness=0, font=self.font, width=7)
        o["menu"].config(bg=PANEL, fg=FG, font=self.font)
        return o

    # ---------- editable task tables (briefing review + plan editor) ----------
    @staticmethod
    def when_str(when, now):
        return when.strftime("%H:%M" if when.date() == now.date() else "%Y-%m-%d %H:%M")

    @staticmethod
    def parse_when(s, now):
        """'15:00', '3pm', '3:30 pm' (today) or '2026-11-08 15:00'."""
        s = s.strip()
        try:
            return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
        low = s.lower().replace(" ", "").replace(".", "")
        for fmt in ("%H:%M", "%I:%M%p", "%I%p", "%H"):
            try:
                return dt.datetime.combine(now.date(), dt.datetime.strptime(low, fmt).time())
            except ValueError:
                pass
        raise ValueError(s)

    def _row_buttons(self, r, rows, row, box):
        def move(step):
            i = rows.index(row)
            j = i + step
            if not 0 <= j < len(rows):
                return
            rows[i], rows[j] = rows[j], rows[i]
            for x in rows:
                x["frame"].pack_forget()
            for x in rows:
                x["frame"].pack(in_=box, anchor="w", pady=1)

        def remove():
            r.destroy()
            rows.remove(row)
        self.B(r, "↑", lambda: move(-1), accent=False).pack(side="left", padx=(4, 1))
        self.B(r, "↓", lambda: move(1), accent=False).pack(side="left", padx=1)
        self.B(r, "✕", remove, accent=False).pack(side="left", padx=1)

    def task_table(self, w, show_done=False):
        head = tk.Frame(w, bg=BG)
        head.pack(anchor="w", pady=(10, 2))
        cols = [("Task", 30), ("Min", 6), ("Difficulty", 10), ("First move", 36)]
        if show_done:
            cols.insert(2, ("Done", 7))
        for txt, width in cols:
            self.L(head, txt, 9, True, MUTED, width=width, anchor="w").pack(side="left")
        box = tk.Frame(w, bg=BG)
        box.pack(anchor="w")
        rows = []

        def add(name="", minutes=50, difficulty="medium", first="", ref=None):
            r = tk.Frame(box, bg=BG)
            r.pack(anchor="w", pady=1)
            row = {"frame": r, "ref": ref, "name": self.E(r, 30, name),
                   "min": self.SPIN(r, 5, 600, 5, minutes, 5),
                   "diff": tk.StringVar(value=difficulty if difficulty in G.LEVELS else "medium"),
                   "first": self.E(r, 36, first)}
            row["name"].pack(side="left", ipady=2)
            row["min"].pack(side="left", padx=4)
            if show_done:
                txt = (f"{ref['done_min']}m" + (" ✔" if ref["completed"] else "")) if ref else "new"
                self.L(r, txt, 9, fg=GOOD if ref and ref["completed"] else MUTED, width=7,
                       anchor="w").pack(side="left")
            self.OPT(r, row["diff"], G.LEVELS).pack(side="left", padx=2)
            row["first"].pack(side="left", padx=4, ipady=2)
            self._row_buttons(r, rows, row, box)
            rows.append(row)
            if not name:
                row["name"].focus_set()
        return box, rows, add

    def read_task_rows(self, rows, err):
        """[(mission, original_or_None)], or None after showing an error."""
        out, seen = [], set()
        for r in rows:
            name = r["name"].get().strip()
            if not name:
                continue
            try:
                mins = int(float(r["min"].get()))
            except ValueError:
                err.config(text=f"Minutes for '{name}' must be a number.")
                return None
            if name.lower() in seen:
                err.config(text=f"Two tasks are called '{name}'. Give them different names.")
                return None
            seen.add(name.lower())
            out.append((G.Game.make_mission(name, mins, r["diff"].get(), r["first"].get()), r.get("ref")))
        return out

    def open_briefing(self):
        if self.lift_if_open("briefing"):
            return
        w = self.dialog("briefing", "Mission Briefing", on_close=lambda: self.brief_later(w))
        self.brief_step1(w)
        self.show(w)

    def brief_step1(self, w, text=None, hours_val=None):
        for child in w.winfo_children():
            child.destroy()
        g = self.g
        d = g.day()
        now = dt.datetime.now()
        days, day_no, total = g.countdown()
        self.L(w, f"MISSION BRIEFING  //  DAY {day_no} OF {total}", 14, True, ACCENT).pack(anchor="w")
        self.L(w, f"{days} days to the deadline. Give me everything, agent.", 10, fg=MUTED).pack(anchor="w", pady=(0, 10))

        key_entry = None
        if not self.cfg.get("gemini_api_key"):
            self.L(w, "Gemini API key (saved on this PC only, sent only to Google):", 10, True).pack(anchor="w")
            key_entry = self.E(w, 54, show="•")
            key_entry.pack(anchor="w", ipady=3, pady=(2, 10))

        top = tk.Frame(w, bg=BG)
        top.pack(anchor="w", fill="x")
        self.L(top, "Dump everything for today, in any order, typed or spoken:", 10, True).pack(side="left")
        mic = self.B(top, "🎤 Speak", None, accent=False)
        mic.pack(side="right")
        self.L(w, "tasks, subjects, chapters, rough times if you know them ('DBMS ch 3, 2 hours')...",
               9, fg=MUTED).pack(anchor="w")
        dump = self.TXT(w, 9)
        dump.pack(anchor="w", pady=(2, 2))
        dump.insert("1.0", text if text is not None else (d.get("dump") or g.carry_over()))
        voice_status = self.L(w, "", 9, fg=MUTED)
        voice_status.pack(anchor="w", pady=(0, 8))
        mic.config(command=lambda: self.toggle_voice(mic, dump, voice_status, key_entry))

        row = tk.Frame(w, bg=BG)
        row.pack(anchor="w", pady=(0, 6))
        self.L(row, "Hours you can work today:", 10, True).pack(side="left")
        left_h = max(1.0, (G.at(now.date(), self.cfg["study_end"]) - now).total_seconds() / 3600)
        hours = self.SPIN(row, 1, 16, 0.5, hours_val if hours_val is not None else min(8.0, round(left_h * 2) / 2))
        hours.pack(side="left", padx=8)

        err = self.L(w, "", 9, fg=ACCENT)
        err.pack(anchor="w")
        btns = tk.Frame(w, bg=BG)
        btns.pack(anchor="e", pady=(8, 0))
        self.B(btns, "Later (10 min)", lambda: self.brief_later(w), accent=False).pack(side="left", padx=6)
        go = self.B(btns, "Analyze  →", None)
        go.config(command=lambda: self.brief_analyze(w, dump, hours, err, key_entry, go))
        go.pack(side="left")
        dump.focus_set()

    def brief_later(self, w):
        self.stop_recording()
        self.brief_snooze_until = dt.datetime.now() + self.g.tdm(10)
        w.destroy()

    def brief_analyze(self, w, dump, hours, err, key_entry, btn):
        text = dump.get("1.0", "end").strip()
        if not text:
            err.config(text="Tell me something, agent. What's on your plate today?")
            return
        try:
            h = float(hours.get())
        except ValueError:
            err.config(text="Hours must be a number, like 6 or 7.5.")
            return
        if key_entry is not None and key_entry.get().strip():
            self.cfg["gemini_api_key"] = key_entry.get().strip()
            G.save_config(self.cfg)
        d = self.g.day()
        d["dump"] = text
        self.g.save()
        btn.config(state="disabled", text="Analyzing...")
        now = dt.datetime.now()
        end = min(now + dt.timedelta(hours=h), G.at(now.date(), self.cfg["study_end"]))

        def done(res, note):
            if w.winfo_exists():
                self.brief_step2(w, res, note, text, h)
        self.run_analysis(text, h, end, done)

    # ---------- morning briefing: step 2 (review) ----------
    def brief_step2(self, w, res, note, text, hours):
        for child in w.winfo_children():
            child.destroy()
        now = dt.datetime.now()
        total = sum(t["minutes"] for t in res["tasks"])
        avail = int(hours * 60)
        self.L(w, "REVIEW THE PLAN", 14, True, ACCENT).pack(anchor="w")
        self.L(w, f"{len(res['tasks'])} tasks  ·  {G.fmt_minutes(total)} of work  ·  {G.fmt_minutes(avail)} available",
               10, True, ACCENT if total > avail else GOOD).pack(anchor="w")
        if total > avail:
            self.L(w, "That's more than you have. Cut, shrink, or accept that the last ones roll to tomorrow.",
                   9, fg=MUTED).pack(anchor="w")
        if note:
            self.L(w, note, 9, fg=WARN, wraplength=int(620 * self.scale)).pack(anchor="w", pady=(4, 0))

        # tasks
        tbox, task_rows, add_task_row = self.task_table(w)
        for t in res["tasks"]:
            add_task_row(t["name"], t["minutes"], t["difficulty"], t["first_step"])
        self.B(w, "+ Add task", lambda: add_task_row(), accent=False).pack(anchor="w", pady=(4, 0))

        err = self.L(w, "", 9, fg=ACCENT)
        err.pack(anchor="w")
        btns = tk.Frame(w, bg=BG)
        btns.pack(anchor="e", pady=(8, 0))
        self.B(btns, "←  Back", lambda: self.brief_step1(w, text, hours), accent=False).pack(side="left", padx=6)
        self.B(btns, "Lock it in", lambda: self.brief_lock(w, task_rows, hours, err)
               ).pack(side="left")
        self.show(w)
        if not note:
            self.say(T.AI_OK.format(n=len(res["tasks"]), work=G.fmt_minutes(total)), timeout=8)

    def brief_lock(self, w, task_rows, hours, err):
        g = self.g
        d = g.day()
        now = dt.datetime.now()
        tasks = self.read_task_rows(task_rows, err)
        if tasks is None:
            return
        tasks = [m for m, _ in tasks]
        if not tasks:
            err.config(text="Give me at least one task, agent.")
            return
        old = {m["name"]: m for m in d["missions"]}
        for m in tasks:
            if m["name"] in old:
                m["done_min"] = old[m["name"]]["done_min"]
                m["completed"] = old[m["name"]]["completed"]
        d["missions"] = tasks
        d["briefed"] = True
        d["debriefed"] = False
        if not g.find(d.get("active")):
            d["active"] = None
        end = min(now + dt.timedelta(hours=hours), G.at(now.date(), self.cfg["study_end"]))
        d["day_end"] = end.isoformat(timespec="seconds")
        msg = T.BRIEF_LOCKED.format(mins=len(tasks))
        g.after_change()
        g.save()
        w.destroy()
        _, risk = g.replan(now)
        if risk:
            msg += "\n" + T.AT_RISK.format(title=", ".join(risk[:2]))
        self.say(msg, mood="happy", timeout=9)
        self.last_activity = now
        self.order_soon(9000)

    # ---------- per-session debrief ----------
    def open_debrief(self, m):
        if self.windows_open("debrief"):
            self.windows["debrief"].destroy()
        name = m["name"] if m else "general prep"
        w = self.dialog("debrief", "Debrief")
        energy = tk.IntVar(value=3)
        note = self.TXT(w, 3, 50)
        nxt = self.E(w, 50)
        ticks = {}
        more = {}

        def submit():
            extra = {}
            for k, sp in more.items():
                try:
                    extra[k] = max(0, int(float(sp.get())))
                except (ValueError, tk.TclError):
                    pass
            self.debrief_submit(w, m, note.get("1.0", "end").strip(), nxt.get().strip(), energy.get(),
                                [k for k, v in ticks.items() if v.get()], extra)
        w.protocol("WM_DELETE_WINDOW", submit)

        self.L(w, "DEBRIEF", 14, True, ACCENT).pack(anchor="w")
        self.L(w, f"Report, agent. What got done on: {name}?", 10).pack(anchor="w", pady=(0, 4))
        note.pack(anchor="w")
        self.L(w, "Next step (exactly where to resume):", 10, True).pack(anchor="w", pady=(10, 2))
        nxt.pack(anchor="w", ipady=3)
        self.L(w, "Energy right now:", 10, True).pack(anchor="w", pady=(10, 2))
        er = tk.Frame(w, bg=BG)
        er.pack(anchor="w")
        for i, lab in enumerate(["1 dead", "2 low", "3 ok", "4 good", "5 fired up"], start=1):
            tk.Radiobutton(er, text=lab, variable=energy, value=i, indicatoron=0, bg=PANEL, fg=FG,
                           selectcolor=ACCENT, activebackground="#30384a", activeforeground="white",
                           relief="flat", bd=0, font=self.font, padx=8, pady=3).pack(side="left", padx=2)
        g = self.g
        if m and m["completed"]:
            self.L(w, f"✔ {name}: done in {m['done_min']} min total.", 10, True, GOOD).pack(anchor="w", pady=(10, 0))
        open_ms = g.open_missions()
        if open_ms:
            self.L(w, "Tick off everything that's finished:", 10, True).pack(anchor="w", pady=(10, 2))
            for x in open_ms:
                v = tk.BooleanVar(value=False)
                ticks[x["name"]] = v
                over = "  · over estimate" if x["done_min"] >= x["est_min"] else ""
                tk.Checkbutton(w, text=f"{x['name']}   ({x['done_min']}/{x['est_min']} min{over})", variable=v,
                               bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                               font=self.font).pack(anchor="w")
        if m and not m["completed"] and m["done_min"] >= m["est_min"]:
            r = tk.Frame(w, bg=BG)
            r.pack(anchor="w", pady=(8, 0))
            self.L(r, f"'{name}' ran past its estimate. If it's not done, about how much more?", 9,
                   fg=WARN).pack(side="left")
            sp = self.SPIN(r, 0, 600, 5, 25)
            sp.pack(side="left", padx=6)
            self.L(r, "min", 9, fg=MUTED).pack(side="left")
            more[m["name"]] = sp
        self.B(w, "Report", submit).pack(anchor="e", pady=(10, 0))
        self.show(w)
        note.focus_set()

    def debrief_submit(self, w, m, note, nxt, energy, done_names, more=None):
        g = self.g
        d = g.day()
        d["log"].append({"time": dt.datetime.now().strftime("%H:%M"), "mission": m["name"] if m else None,
                         "note": note, "next": nxt, "energy": energy})
        if m and nxt:
            m["first_step"] = nxt
        for name, extra in (more or {}).items():
            x = g.find(name)
            if x and name not in done_names and extra > 0:
                x["est_min"] = x["done_min"] + extra  # re-estimate so the plan stays honest
        delay = 0
        finished = []
        for name in done_names:
            x = g.find(name)
            if x and not x["completed"]:
                x["completed"] = True
                finished.append(x["name"])
                if d.get("active") == x["name"]:
                    d["active"] = None
        if finished:
            change = g.add_xp(G.MISSION_XP * len(finished))
            delay = 6000
            label = ", ".join(finished)
            self.root.after(1500, lambda: (self.celebrate(4), self.say(
                T.pick(T.MISSION_DONE, **self.ctx(mission=label)) + f" +{G.MISSION_XP * len(finished)} XP",
                mood="happy", sound=True, timeout=8)))
            self.rank_change(change, delay=9000)
            if d["missions"] and not g.open_missions():
                self.root.after(5000, lambda: self.say(T.ALL_DONE, mood="happy", timeout=10))
        g.save()
        self.announce_quests(g.after_change(), delay=delay + 4000)
        w.destroy()
        if energy <= 2:
            self.root.after(delay + 2000, lambda: self.say(T.LOW_ENERGY, timeout=12))
        self.root.after(delay + 15000, self.warn_at_risk)

    # ---------- punishment ----------
    def open_punishment(self):
        g = self.g
        p = g.data["pending_punishment"]
        if not p:
            return
        if self.lift_if_open("punish"):
            return
        w = self.dialog("punish", "Reset Protocol", on_close=lambda: None)
        self.L(w, "PENALTY  //  RESET PROTOCOL", 14, True, ACCENT).pack(anchor="w")
        self.L(w, "You lost an order. Bring your dopamine back to baseline:", 10, fg=MUTED).pack(anchor="w", pady=(0, 10))
        self.p_text = self.L(w, p["text"], 14, True, wraplength=int(420 * self.scale))
        self.p_text.pack(anchor="w")
        self.p_timer = self.L(w, "", 18, True, WARN)
        self.p_timer.pack(anchor="w", pady=8)
        row = tk.Frame(w, bg=BG)
        row.pack(anchor="w")
        self.p_done = self.B(row, "Done", self.punish_done)
        self.p_done.config(state="disabled")
        self.p_done.pack(side="left")
        self.p_swap = self.B(row, "Swap (1 per day)", self.punish_swap, accent=False)
        if g.day()["swap_used"]:
            self.p_swap.config(state="disabled")
        self.p_swap.pack(side="left", padx=8)
        self.L(w, "Honor system. The only person you can cheat here is you.", 9, fg=MUTED).pack(anchor="w", pady=(10, 0))
        self.punish_next_nag = dt.datetime.now() + self.g.tdm(2)
        self.start_punish_countdown(p)
        self.show(w)

    def start_punish_countdown(self, p):
        if self.p_job:
            self.root.after_cancel(self.p_job)
        self.p_timed = bool(p["seconds"])
        self.p_remaining = p["seconds"] if self.p_timed else (5 if DEMO else 20)
        self.p_done.config(state="disabled")
        self.punish_countdown()

    def punish_countdown(self):
        self.p_job = None
        if not self.windows_open("punish"):
            return
        if self.p_remaining > 0:
            m, s = divmod(self.p_remaining, 60)
            self.p_timer.config(text=f"{m}:{s:02d}" if self.p_timed else f"'Done' unlocks in {self.p_remaining}s")
            self.p_remaining -= 1
            self.p_job = self.root.after(1000, self.punish_countdown)
        else:
            self.p_timer.config(text="Time. Confirm when you've finished." if self.p_timed else "Confirm when you've finished.")
            self.p_done.config(state="normal")
            if self.p_timed:
                self.alarm("done")

    def punish_swap(self):
        p = self.g.swap_punishment()
        if not p:
            return
        self.p_text.config(text=p["text"])
        self.p_swap.config(state="disabled")
        self.start_punish_countdown(p)

    def punish_done(self):
        change = self.g.punishment_done()
        if self.p_job:
            self.root.after_cancel(self.p_job)
            self.p_job = None
        self.windows["punish"].destroy()
        self.state = "idle"
        self.last_activity = dt.datetime.now()
        self.say(T.PUNISH_DONE, mood="happy", timeout=6)
        self.rank_change(change)
        self.order_soon(6000)

    def ensure_punish_window(self, now, force_lift=False):
        w = self.windows.get("punish")
        if not (w and w.winfo_exists()):
            self.open_punishment()
            return
        if w.state() == "withdrawn":
            w.deiconify()
        if force_lift or (self.punish_next_nag and now >= self.punish_next_nag):
            self.punish_next_nag = now + self.g.tdm(2)
            w.lift()
            w.attributes("-topmost", True)
            if not force_lift:
                self.say(T.pick(T.PUNISH_NAG), mood="angry", sound=True, timeout=8)

    # ---------- mission board ----------
    def open_board(self):
        if self.lift_if_open("board"):
            self.refresh_board()
            return
        w = self.dialog("board", "Mission Board")
        self.L(w, "MISSION BOARD", 14, True, ACCENT).pack(anchor="w")
        self.b_list = tk.Listbox(w, width=60, height=8, bg=PANEL, fg=FG, selectbackground=ACCENT,
                                 relief="flat", font=self.font, activestyle="none", highlightthickness=0)
        self.b_list.pack(anchor="w", pady=(6, 6))
        self.b_list.bind("<Double-Button-1>", lambda e: self.open_plan_editor())
        row = tk.Frame(w, bg=BG)
        row.pack(anchor="w")
        self.B(row, "✎ Edit plan / reschedule", self.open_plan_editor).pack(side="left", padx=(0, 8))
        for label, fn in [("Complete", self.b_complete), ("Up", lambda: self.b_move(-1)),
                          ("Down", lambda: self.b_move(1)), ("+15 min", self.b_more), ("Delete", self.b_delete)]:
            self.B(row, label, fn, accent=False).pack(side="left", padx=(0, 4))

        self.L(w, "Add with AI (describe it any way you like):", 10, True).pack(anchor="w", pady=(12, 2))
        ai = tk.Frame(w, bg=BG)
        ai.pack(anchor="w")
        ai_entry = self.E(ai, 46)
        ai_entry.pack(side="left", ipady=3)
        ai_btn = self.B(ai, "Add via AI", None)
        ai_btn.config(command=lambda: self.b_ai_add(ai_entry, ai_btn))
        ai_btn.pack(side="left", padx=6)

        self.L(w, "Or add manually (name, minutes):", 10, True).pack(anchor="w", pady=(8, 2))
        add = tk.Frame(w, bg=BG)
        add.pack(anchor="w")
        name = self.E(add, 38)
        name.pack(side="left", ipady=3)
        est = self.SPIN(add, 5, 600, 5, 50)
        est.pack(side="left", padx=6)
        self.B(add, "Add", lambda: self.b_add(name, est)).pack(side="left")

        self.L(w, "Rest of today's operation:", 10, True).pack(anchor="w", pady=(12, 2))
        self.b_tl = self.TXT(w, 10, 60)
        self.b_tl.pack(anchor="w")
        self.b_warn = self.L(w, "", 10, True)
        self.b_warn.pack(anchor="w", pady=(6, 0))
        self.refresh_board()
        self.show(w)

    def refresh_board(self):
        if not self.windows_open("board"):
            return
        g = self.g
        d = g.day()
        timeline, risk = g.replan()
        self.b_list.delete(0, "end")
        for m in d["missions"]:
            mark = "✔" if m["completed"] else ("⚠" if m["name"] in risk else ("▶" if m["name"] == d.get("active") else "•"))
            self.b_list.insert("end", f" {mark}  {m['name']}   {m['done_min']}/{m['est_min']} min   [{m['difficulty']}]")
        self.b_tl.config(state="normal")
        self.b_tl.delete("1.0", "end")
        fmt = "%H:%M:%S" if DEMO else "%H:%M"
        level = (d.get("last_level") or self.cfg["default_level"]).upper()
        if not d["missions"]:
            self.b_tl.insert("1.0", "No missions yet. Run the morning briefing (right-click me).")
        else:
            lines = [f"(projected at {level} focus)"] + [f"{t.strftime(fmt)}   {label}" for t, label in timeline]
            self.b_tl.insert("1.0", "\n".join(lines) if timeline else "Nothing left.")
        self.b_tl.config(state="disabled")
        if risk:
            self.b_warn.config(text="At risk today: " + ", ".join(risk), fg=ACCENT)
        elif d["missions"]:
            self.b_warn.config(text="On track. Keep moving.", fg=GOOD)
        else:
            self.b_warn.config(text="")

    def b_sel(self):
        sel = self.b_list.curselection()
        return sel[0] if sel else None

    def b_complete(self):
        i = self.b_sel()
        if i is None:
            return
        g = self.g
        d = g.day()
        m = d["missions"][i]
        m["completed"] = not m["completed"]
        if m["completed"]:
            if d.get("active") == m["name"]:
                d["active"] = None
            change = g.add_xp(G.MISSION_XP)
            self.celebrate(4)
            self.say(T.pick(T.MISSION_DONE, **self.ctx(mission=m["name"])), mood="happy", sound=True, timeout=8)
            self.rank_change(change)
        else:
            g.add_xp(-G.MISSION_XP)
        g.save()
        self.refresh_board()

    def b_move(self, step):
        i = self.b_sel()
        ms = self.g.day()["missions"]
        if i is None or not (0 <= i + step < len(ms)):
            return
        ms[i], ms[i + step] = ms[i + step], ms[i]
        self.g.save()
        self.refresh_board()
        self.b_list.selection_set(i + step)

    def b_more(self):
        i = self.b_sel()
        if i is None:
            return
        self.g.day()["missions"][i]["est_min"] += 15
        self.g.save()
        self.refresh_board()
        self.b_list.selection_set(i)

    def b_delete(self):
        i = self.b_sel()
        if i is None:
            return
        d = self.g.day()
        if d["missions"][i]["name"] == d.get("active"):
            d["active"] = None
        del d["missions"][i]
        self.g.save()
        self.refresh_board()

    def b_add(self, name_entry, est_spin):
        name = name_entry.get().strip()
        if not name:
            return
        try:
            est = int(float(est_spin.get()))
        except ValueError:
            est = 50
        d = self.g.day()
        d["missions"].append(G.Game.make_mission(name, est))
        d["briefed"] = True
        self.g.save()
        name_entry.delete(0, "end")
        self.refresh_board()

    def b_ai_add(self, entry, btn):
        text = entry.get().strip()
        if not text:
            return
        g = self.g
        now = dt.datetime.now()
        end = g.day_end(now)
        hours = max(0.5, round((end - now).total_seconds() / 3600, 1))
        btn.config(state="disabled", text="Analyzing...")

        def done(res, note):
            d = g.day()
            added = 0
            for t in res["tasks"]:
                if not g.find(t["name"]):
                    d["missions"].append(G.Game.make_mission(t["name"], t["minutes"], t["difficulty"], t["first_step"]))
                    added += 1
            d["briefed"] = True
            g.save()
            try:
                entry.delete(0, "end")
                btn.config(state="normal", text="Add via AI")
            except tk.TclError:
                pass
            self.refresh_board()
            self.say((note + "\n" if note else "") + T.AI_ADDED.format(n=added), timeout=8)
        self.run_analysis(text, hours, end, done)

    # ---------- edit plan / reschedule ----------
    def open_plan_editor(self):
        if self.lift_if_open("plan"):
            return
        g = self.g
        d = g.day()
        now = dt.datetime.now()
        w = self.dialog("plan", "Edit Plan")
        self.L(w, "EDIT TODAY'S PLAN", 14, True, ACCENT).pack(anchor="w")
        self.L(w, "Fix anything: rename, change minutes or difficulty, reorder with ↑ ↓, add or delete tasks. "
                  "Progress you've already logged is kept.",
               9, fg=MUTED, wraplength=int(700 * self.scale)).pack(anchor="w")

        _, task_rows, add_task = self.task_table(w, show_done=True)
        for m in d["missions"]:
            add_task(m["name"], m["est_min"], m["difficulty"], m["first_step"], ref=m)
        self.B(w, "+ Add task", lambda: add_task(), accent=False).pack(anchor="w", pady=(4, 0))

        row = tk.Frame(w, bg=BG)
        row.pack(anchor="w", pady=(12, 0))
        self.L(row, "Stop working today at (HH:MM):", 10, True).pack(side="left")
        end_text = g.day_end(now).strftime("%H:%M")
        end_e = self.E(row, 8, end_text)
        end_e.pack(side="left", padx=8, ipady=2)
        self.L(row, f"(latest {self.cfg['study_end']})", 9, fg=MUTED).pack(side="left")

        err = self.L(w, "", 9, fg=ACCENT)
        err.pack(anchor="w", pady=(6, 0))
        btns = tk.Frame(w, bg=BG)
        btns.pack(anchor="e", pady=(8, 0))
        self.B(btns, "Cancel", w.destroy, accent=False).pack(side="left", padx=6)
        self.B(btns, "Save changes", lambda: self.plan_save(w, task_rows, end_e, end_text, err)
               ).pack(side="left")
        self.show(w)

    def plan_save(self, w, task_rows, end_e, end_text, err):
        g = self.g
        d = g.day()
        now = dt.datetime.now()
        tasks = self.read_task_rows(task_rows, err)
        if tasks is None:
            return
        end_s = end_e.get().strip()
        new_end = None
        if end_s != end_text:
            try:
                new_end = self.parse_when(end_s, now)
            except ValueError:
                err.config(text=f"Can't read the stop time '{end_s}'. Use 21:30 or 9:30pm.")
                return

        # --- tasks: keep logged progress, follow renames ---
        active = d.get("active")
        new = []
        for m, ref in tasks:
            if ref:
                m["done_min"] = ref["done_min"]
                m["completed"] = ref["completed"]
                if ref["name"] == active:
                    active = m["name"]
                if self.session and self.session.get("mission") == ref["name"]:
                    self.session["mission"] = m["name"]
            new.append(m)
        d["missions"] = new
        d["active"] = active if g.find(active) else None
        if self.session and self.session.get("mission") and not g.find(self.session["mission"]):
            self.session["mission"] = None
        if new:
            d["briefed"] = True

        msg = "Plan updated. New orders follow the new plan."
        if new_end:
            d["day_end"] = new_end.isoformat(timespec="seconds")
            d["debriefed"] = False

        g.save()
        g.after_change()
        w.destroy()
        self.refresh_board()
        _, risk = g.replan(now)
        if risk:
            msg += "\n" + T.AT_RISK.format(title=", ".join(risk[:2]))
        self.say(msg, mood="happy", timeout=9)

    # ---------- scoreboard ----------
    def open_scoreboard(self):
        if self.windows_open("score"):
            self.windows["score"].destroy()
        g = self.g
        d = g.day()
        s = self.scale
        rank, cur_need, nxt = g.rank_info()
        xp = g.data["xp"]
        w = self.dialog("score", "Ranked")
        lad = g.data["ladder"]
        bar_w, bar_h = int(360 * s), int(16 * s)
        # ranked ladder
        self.L(w, f"RANKED  //  {LD.title(lad).upper()}", 14, True, ACCENT).pack(anchor="w")
        bar = tk.Canvas(w, width=bar_w, height=bar_h, bg=PANEL, highlightthickness=0)
        bar.pack(anchor="w", pady=(8, 2))
        if lad.get("promo"):
            p = lad["promo"]
            for i in range(LD.PROMO_GAMES):
                res = "W" if i < p["w"] else ("L" if i < p["w"] + p["l"] else "")
                x0 = i * bar_w // LD.PROMO_GAMES
                bar.create_rectangle(x0 + 2, 0, x0 + bar_w // LD.PROMO_GAMES - 2, bar_h,
                                     fill={"W": GOOD, "L": ACCENT}.get(res, "#333a47"), outline="")
            sub = f"PROMO SERIES  ·  win {LD.PROMO_WINS} of {LD.PROMO_GAMES}"
        else:
            bar.create_rectangle(0, 0, int(bar_w * min(1, lad["sp"] / LD.SP_PER_DIV)), bar_h, fill=GOOD, outline="")
            sub = f"{lad['sp']} / {LD.SP_PER_DIV} SP"
        if lad.get("shield"):
            sub += f"  ·  🛡 demotion shield ({lad['shield']})"
        peak = LD.title({"tier": lad["peak"][0], "div": lad["peak"][1]})
        self.L(w, f"{sub}  ·  peak {peak}", 9, fg=MUTED).pack(anchor="w")
        hist = lad["history"][-10:]
        if hist:
            hc = tk.Canvas(w, width=bar_w, height=int(22 * s), bg=BG, highlightthickness=0)
            hc.pack(anchor="w", pady=(6, 0))
            cell = bar_w // 10
            for i, mt in enumerate(hist):
                x0 = i * cell
                hc.create_rectangle(x0 + 2, 0, x0 + cell - 2, int(22 * s),
                                    fill=GOOD if mt["w"] else ACCENT, outline="")
                hc.create_text(x0 + cell // 2, int(11 * s), text="W" if mt["w"] else "L", fill="white",
                               font=(self.font[0], max(8, int(9 * s)), "bold"))
            self.L(w, "Match history (last 10 sessions)", 8, fg=MUTED).pack(anchor="w")
        # daily quests
        self.L(w, "DAILY QUESTS", 10, True).pack(anchor="w", pady=(10, 2))
        today = dt.date.today()
        for text, have, goal, done in LD.quest_progress(lad, today.isoformat(), g.day_stats(today)):
            mark = "✔" if done else "○"
            self.L(w, f"  {mark} {text}  ({have}/{goal})", 9, fg=GOOD if done else FG).pack(anchor="w")
        # XP rank (career)
        if nxt:
            sub = f"Career: {rank}  ·  {xp} XP  ·  {nxt[0] - xp} XP to {nxt[1]}"
        else:
            sub = f"Career: {rank}  ·  {xp} XP  ·  max career rank"
        self.L(w, sub, 9, fg=MUTED).pack(anchor="w", pady=(10, 6))
        days, _, _ = g.countdown()
        rows = [
            ("Today", f"{d['wins']} W  /  {d['losses']} L"),
            ("All time", f"{g.data['wins_total']} W  /  {g.data['losses_total']} L"),
            ("Streak", f"{g.data['streak']}  (best {g.data['best_streak']})"),
            ("Focus today", G.fmt_minutes(d["focus_sec"] // 60)),
            ("Distractions caught", str(d["distractions"])),
            ("Minutes wasted today", str(d["wasted_sec"] // 60)),
            ("Penalties done", str(d["punishments_done"])),
            ("Days to the deadline", str(days)),
        ]
        grid = tk.Frame(w, bg=BG)
        grid.pack(anchor="w")
        for r, (k, v) in enumerate(rows):
            self.L(grid, k, 10, fg=MUTED).grid(row=r, column=0, sticky="w", padx=(0, 24), pady=1)
            self.L(grid, v, 10, True).grid(row=r, column=1, sticky="w", pady=1)
        btns = tk.Frame(w, bg=BG)
        btns.pack(anchor="e", pady=(12, 0))
        self.B(btns, "📊 Weekly status", lambda: (w.destroy(), self.open_weekly()), accent=False
               ).pack(side="left", padx=6)
        self.B(btns, "Close", w.destroy, accent=False).pack(side="left")
        self.show(w)

    # ---------- weekly status (Neon, falls back to data.json) ----------
    def open_weekly(self, monday=None):
        if self.windows_open("weekly"):
            self.windows["weekly"].destroy()
        g = self.g
        today = dt.date.today()
        monday = monday or today - dt.timedelta(days=today.weekday())
        w = self.dialog("weekly", "Weekly status")
        head = tk.Frame(w, bg=BG)
        head.pack(fill="x")
        self.B(head, "◀", lambda: (w.destroy(), self.open_weekly(monday - dt.timedelta(days=7))),
               accent=False).pack(side="left")
        self.L(head, f"  WEEK OF {monday:%d %b}  –  {monday + dt.timedelta(days=6):%d %b}  ",
               14, True, ACCENT).pack(side="left")
        if monday + dt.timedelta(days=7) <= today:
            self.B(head, "▶", lambda: (w.destroy(), self.open_weekly(monday + dt.timedelta(days=7))),
                   accent=False).pack(side="left")
        status = self.L(w, "Loading from Neon…" if g.db and g.db.enabled else "Local data (Neon not linked)",
                        9, fg=MUTED)
        status.pack(anchor="w", pady=(2, 6))
        body = tk.Frame(w, bg=BG)
        body.pack(anchor="w", fill="x")
        self.B(w, "Close", w.destroy, accent=False).pack(anchor="e", pady=(10, 0))
        self.show(w)

        def render(rows, done, src):
            try:
                if not body.winfo_exists():
                    return
            except tk.TclError:
                return
            status.config(text=src)
            self.render_week(body, monday, rows, done)

        if g.db and g.db.enabled:
            def work():
                try:
                    rows, done = g.db.week(monday)
                    src = "Synced with Neon"
                except Exception as e:
                    rows, done = g.week_local(monday)
                    src = f"Neon unreachable ({type(e).__name__}), showing local data"
                self.root.after(0, lambda: render(rows, done, src))
            threading.Thread(target=work, daemon=True).start()
        else:
            rows, done = g.week_local(monday)
            render(rows, done, "Local data (Neon not linked)")

    def render_week(self, body, monday, rows, done):
        s = self.scale
        per = {}
        for day, focus, wins, losses in rows:
            per[day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day))] = (focus, wins, losses)
        week = [monday + dt.timedelta(days=i) for i in range(7)]
        prev = [d - dt.timedelta(days=7) for d in week]
        mins = [per.get(d, (0, 0, 0))[0] for d in week]
        total, prev_total = sum(mins), sum(per.get(d, (0, 0, 0))[0] for d in prev)
        wins = sum(per.get(d, (0, 0, 0))[1] for d in week)
        losses = sum(per.get(d, (0, 0, 0))[2] for d in week)
        # bar chart: hours per day
        cw, ch = int(420 * s), int(150 * s)
        c = tk.Canvas(body, width=cw, height=ch, bg=PANEL, highlightthickness=0)
        c.pack(anchor="w")
        top = max(60, max(mins))
        col = cw // 7
        today = dt.date.today()
        for i, (d, m) in enumerate(zip(week, mins)):
            x0 = i * col + int(8 * s)
            hgt = int((ch - 36 * s) * m / top)
            fill = ACCENT if d == today else GOOD
            if m:
                c.create_rectangle(x0, ch - int(20 * s) - hgt, x0 + col - int(16 * s), ch - int(20 * s),
                                   fill=fill, outline="")
                c.create_text(x0 + (col - int(16 * s)) // 2, ch - int(26 * s) - hgt,
                              text=f"{m / 60:.1f}h", fill=FG, font=(self.font[0], max(7, int(8 * s))))
            c.create_text(x0 + (col - int(16 * s)) // 2, ch - int(10 * s), text=d.strftime("%a"),
                          fill=MUTED, font=(self.font[0], max(7, int(8 * s))))
        diff = total - prev_total
        trend = (f"▲ {G.fmt_minutes(diff)} more than last week" if diff > 0 else
                 f"▼ {G.fmt_minutes(-diff)} less than last week" if diff < 0 else "same as last week")
        days_studied = sum(1 for m in mins if m)
        best = max(range(7), key=lambda i: mins[i]) if total else None
        rows_ = [
            ("Total study", f"{G.fmt_minutes(total)}  ({trend})"),
            ("Days studied", f"{days_studied} / 7"),
            ("Daily average", G.fmt_minutes(total / max(1, days_studied))),
            ("Best day", f"{week[best]:%A} · {G.fmt_minutes(mins[best])}" if best is not None else "—"),
            ("Sessions", f"{wins} W  /  {losses} L"
                         + (f"  ({round(100 * wins / (wins + losses))}% win rate)" if wins + losses else "")),
            ("Tasks finished", str(len(done))),
            ("Rank", self.g.ladder_title()),
        ]
        grid = tk.Frame(body, bg=BG)
        grid.pack(anchor="w", pady=(8, 0))
        for r, (k, v) in enumerate(rows_):
            self.L(grid, k, 10, fg=MUTED).grid(row=r, column=0, sticky="w", padx=(0, 24), pady=1)
            self.L(grid, v, 10, True).grid(row=r, column=1, sticky="w", pady=1)
        if done:
            self.L(body, "Done this week", 10, True).pack(anchor="w", pady=(8, 0))
            for d, title in list(done)[-12:]:
                d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d))
                self.L(body, f"  ✔ {d:%a}  {title}", 9, fg=FG).pack(anchor="w")

    # ---------- brain dump (quick notes for random thoughts) ----------
    def open_brain_dump(self):
        if self.lift_if_open("notes"):
            self.windows["notes"].entry.focus_set()
            return
        notes = self.g.data.setdefault("notes", [])
        w = self.dialog("notes", "Brain dump")
        self.L(w, "BRAIN DUMP", 14, True, ACCENT).pack(anchor="w")
        self.L(w, "Get it out of your head, then get back to work.\n"
                  "Enter saves  ·  Shift+Enter new line  ·  Esc closes", 9, fg=MUTED).pack(anchor="w", pady=(0, 6))

        entry = self.TXT(w, 3)
        entry.pack(fill="x")
        w.entry = entry
        status = self.L(w, "", 9, fg=GOOD)
        status.pack(anchor="w", pady=(2, 6))

        lst = self.TXT(w, 14)
        lst.pack(fill="both", expand=True)
        lst.tag_configure("time", foreground=MUTED, font=("Segoe UI", 8))
        lst.tag_configure("del", foreground=ACCENT, font=self.font_b)
        lst.tag_bind("del", "<Enter>", lambda e: lst.config(cursor="hand2"))
        lst.tag_bind("del", "<Leave>", lambda e: lst.config(cursor=""))

        def render():
            lst.config(state="normal")
            lst.delete("1.0", "end")
            if not notes:
                lst.insert("end", "Nothing here yet. Mind is clear.", "time")
            for i in range(len(notes) - 1, -1, -1):  # newest first
                n = notes[i]
                tag = f"del{i}"
                lst.insert("end", "✕", ("del", tag))
                lst.tag_bind(tag, "<Button-1>", lambda e, k=i: remove(k))
                lst.insert("end", f"  {n['t']}\n", "time")
                lst.insert("end", n["text"] + "\n\n")
            lst.config(state="disabled")

        def flash(msg, color=GOOD):
            status.config(text=msg, fg=color)
            w.after(2500, lambda: status.winfo_exists() and status.config(text=""))

        def add(e=None):
            text = entry.get("1.0", "end").strip()
            if text:
                notes.append({"t": dt.datetime.now().strftime("%a %d %b, %H:%M"), "text": text})
                self.g.save()
                entry.delete("1.0", "end")
                render()
                flash("Dumped. Back to it.")
            return "break"  # don't insert a newline

        def remove(k):
            if 0 <= k < len(notes):
                notes.pop(k)
                self.g.save()
                render()
                flash("Deleted.", MUTED)

        def copy_all():
            if notes:
                self.root.clipboard_clear()
                self.root.clipboard_append("\n\n".join(f"[{n['t']}] {n['text']}" for n in notes))
                flash("All notes copied to clipboard.")

        def clear_all():
            if not notes:
                return
            if getattr(w, "confirm", False):
                notes.clear()
                self.g.save()
                render()
                flash("All cleared.", MUTED)
                w.confirm = False
                clear_btn.config(text="Clear all")
            else:
                w.confirm = True
                clear_btn.config(text="Sure? Click again")
                w.after(3000, lambda: clear_btn.winfo_exists() and (setattr(w, "confirm", False),
                                                                     clear_btn.config(text="Clear all")))

        entry.bind("<Return>", add)
        entry.bind("<Shift-Return>", lambda e: None)  # default: newline
        w.bind("<Escape>", lambda e: w.destroy())

        btns = tk.Frame(w, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        self.B(btns, "Save note", add).pack(side="left")
        self.B(btns, "Copy all", copy_all, accent=False).pack(side="left", padx=(8, 0))
        clear_btn = self.B(btns, "Clear all", clear_all, accent=False)
        clear_btn.pack(side="left", padx=(8, 0))
        self.B(btns, "Close", w.destroy, accent=False).pack(side="right")

        render()
        self.show(w)
        entry.focus_set()

    # ---------- end of day ----------
    def open_day_debrief(self):
        if self.lift_if_open("dayend"):
            return
        g = self.g
        d = g.day()
        d["debriefed"] = True
        g.save()
        key = g.verdict_key()
        verdict = T.VERDICT[key].format(next=max(d["wins"] + 2, 5))
        w = self.dialog("dayend", "End of Day")
        self.L(w, "END OF DAY DEBRIEF", 14, True, ACCENT).pack(anchor="w")
        ms = d["missions"]
        done = sum(1 for m in ms if m["completed"])
        self.L(w, f"Missions: {done}/{len(ms)} complete", 11, True).pack(anchor="w", pady=(8, 2))
        for m in ms:
            self.L(w, f"   {'✔' if m['completed'] else '✘'}  {m['name']}  ({m['done_min']}/{m['est_min']} min)", 10,
                   fg=GOOD if m["completed"] else MUTED).pack(anchor="w")
        stats = (f"\n{d['wins']} wins  /  {d['losses']} losses\n"
                 f"{G.fmt_minutes(d['focus_sec'] // 60)} focused  ·  {d['distractions']} distractions  ·  "
                 f"{d['wasted_sec'] // 60} min wasted\nRank: {g.rank_name()}  ({g.data['xp']} XP)")
        self.L(w, stats, 10).pack(anchor="w")
        self.L(w, "\nTHE BOSS SAYS:", 10, True, ACCENT).pack(anchor="w")
        self.L(w, verdict, 11, True, wraplength=int(420 * self.scale)).pack(anchor="w")
        self.B(w, "Dismissed", w.destroy).pack(anchor="e", pady=(12, 0))
        self.show(w)
        self.say(verdict, mood="happy" if key in ("great", "good") else "angry", timeout=12)

    # ---------- quit ----------
    def quit(self):
        try:
            self.player.close()
            self.g.save()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        Boss().run()
    except Exception:
        log_error()
        raise
