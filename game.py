"""Game state for The Boss: missions (in minutes), focus levels, re-planning,
wins/losses, XP, ranked ladder, punishments."""
import datetime as dt
import json
import os
import random

import ladder as LD

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")

RANKS = [
    (0, "Recruit"),
    (300, "Field Agent"),
    (800, "Operative"),
    (1500, "Specialist"),
    (2500, "Elite"),
    (4000, "Ghost"),
]

XP_PER_MIN = 4          # 25 min -> 100 XP, 50 -> 200, 90 -> 360
LOSS_XP_MIN = 50
STREAK_BONUS_XP = 50
MISSION_XP = 150
PUNISH_XP = 25
LEVELS = ("low", "medium", "high")
DIFF_ORDER = {"high": 0, "medium": 1, "low": 2}

DEFAULT_CONFIG = {
    "start_date": "",
    "deadline": "",
    "wake_time": "07:30",
    "study_start": "08:00",
    "study_end": "23:00",
    "focus_levels": {
        "low": {"focus": 25, "break": 5, "starter": 10},
        "medium": {"focus": 50, "break": 10},
        "high": {"focus": 90, "break": 20},
    },
    "default_level": "medium",
    "fold_leftover_min": 15,
    "order_timeout_min": 5,
    "nag_every_min": 1,
    "off_duty_nag_min": 20,
    "idle_min": 10,
    "alarm_sound": True,
    "alarm_volume": 100,
    "focus_sound": "rain",
    "focus_sound_volume": 35,
    "gemini_api_key": "",
    "gemini_model": "gemini-3.5-flash-lite",
    "gemini_fallback_models": [
        "gemini-3.5-flash-lite", "gemini-3.8-flash-lite", "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash",
    ],
    "distraction_keywords": [
        "instagram", "youtube", "netflix", "reddit", "facebook", "twitter",
        "snapchat", "tiktok", "prime video", "hotstar", "jiocinema", "twitch",
    ],
    "punishments": [
        {"text": "Do {n} squats.", "base": 20, "step": 10},
        {"text": "Do {n} push-ups (knee push-ups are fine).", "base": 15, "step": 5},
        {"text": "Hold a plank for {n} minute(s).", "base": 1, "step": 1, "timed": True},
        {"text": "Go splash cold water on your face. {n} times.", "base": 3, "step": 2},
        {"text": "Sit for {n} minutes doing NOTHING. No phone, no screen.", "base": 5, "step": 2, "timed": True},
        {"text": "Walk for {n} minutes. Leave your phone behind.", "base": 5, "step": 2, "timed": True},
        {"text": "Put your phone in another room until your next break.", "base": 1, "step": 0},
        {"text": "On paper, write {n} lines: why this subject matters to me.", "base": 3, "step": 2},
        {"text": "Take {n} slow, deep breaths. Eyes closed.", "base": 10, "step": 5},
    ],
}


# settings from the hackathon build (mentor rounds, 3-part day, workouts): dropped on load
OBSOLETE_KEYS = {"parts", "workouts", "absence_cap_min", "deliverable_edit_xp", "focus_min", "break_min",
                 "long_break_min", "blocks_before_long_break"}
OBSOLETE_PREFIXES = ("mentor_", "auto_mentor_", "part_", "gap_", "tired_", "workout_")


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    file_cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                file_cfg = json.load(f)
        except (ValueError, OSError):
            return cfg  # broken config: run on defaults, don't overwrite the user's file
    stale = [k for k in file_cfg if k in OBSOLETE_KEYS or k.startswith(OBSOLETE_PREFIXES)]
    for k in stale:
        file_cfg.pop(k)
    cfg.update(file_cfg)
    if stale or any(k not in file_cfg for k in DEFAULT_CONFIG):
        save_config(cfg)  # add new settings so the user can see and edit them
    return cfg


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def hm(text):
    h, m = text.strip().split(":")
    return dt.time(int(h), int(m))


def at(date, text):
    return dt.datetime.combine(date, hm(text))


def fmt_minutes(n):
    n = int(n)
    h, m = divmod(n, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


class Game:
    def __init__(self, cfg, demo=False):
        self.cfg = cfg
        self.demo = demo
        # Seconds per "minute": demo mode makes every timer run 30x faster.
        self.M = 2 if demo else 60
        self.path = os.path.join(BASE, "data_demo.json" if demo else "data.json")
        self.data = self._load()
        self.db = None
        if not demo:
            try:
                import db
                self.db = db.Neon()
            except Exception:
                self.db = None

    # ---------- persistence ----------
    def _load(self):
        d = {
            "xp": 0, "wins_total": 0, "losses_total": 0, "streak": 0, "best_streak": 0,
            "days": {}, "pending_punishment": None, "pos": None,
        }
        try:
            with open(self.path, encoding="utf-8") as f:
                d.update(json.load(f))
        except (FileNotFoundError, ValueError):
            pass
        d.pop("mentors", None)
        for day in d["days"].values():  # hackathon build leftovers
            for k in ("parts", "workouts", "mentor_sec"):
                day.pop(k, None)
        LD.ensure(d.setdefault("ladder", LD.new_state()))
        # v1 -> v2: Pomodoro counts become minutes
        for day in d["days"].values():
            for m in day.get("missions", []):
                if "est_min" not in m:
                    m["est_min"] = int(m.pop("est", 1)) * 25
                    m["done_min"] = int(m.pop("done", 0)) * 25
                m.setdefault("difficulty", "medium")
                m.setdefault("first_step", "")
        return d

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    # ---------- durations ----------
    def secs(self, key):
        return self.cfg[key] * self.M

    def td(self, key):
        return dt.timedelta(seconds=self.secs(key))

    def tdm(self, minutes):
        return dt.timedelta(seconds=minutes * self.M)

    def lv(self, level):
        levels = self.cfg["focus_levels"]
        return levels.get(level) or levels[self.cfg["default_level"]]

    # ---------- days ----------
    def day(self, date=None):
        key = (date or dt.date.today()).isoformat()
        days = self.data["days"]
        d = days.setdefault(key, {})
        for k, v in {
            "briefed": False, "missions": [], "day_end": None, "wins": 0, "losses": 0,
            "focus_blocks": 0, "focus_sec": 0, "distractions": 0, "wasted_sec": 0,
            "idle_pokes": 0, "punishments_done": 0, "swap_used": False, "debriefed": False,
            "log": [], "last_level": None, "active": None, "dump": "",
        }.items():
            d.setdefault(k, v if not isinstance(v, list) else [])
        return d

    def countdown(self):
        today = dt.date.today()
        try:
            start = dt.date.fromisoformat(self.cfg.get("start_date") or "")
        except ValueError:
            start = today
        try:  # "deadline" is optional (next exam); without one, count down to the end of the week
            deadline = dt.date.fromisoformat(self.cfg.get("deadline") or "")
        except ValueError:
            deadline = today + dt.timedelta(days=6 - today.weekday())
        days_left = max(0, (deadline - today).days)
        total = max(1, (deadline - start).days)
        day_no = min(total, max(1, (today - start).days + 1))
        return days_left, day_no, total

    def day_end(self, now=None):
        now = now or dt.datetime.now()
        end = at(now.date(), self.cfg["study_end"])
        d = self.day(now.date())
        if d.get("day_end"):
            end = min(end, dt.datetime.fromisoformat(d["day_end"]))
        return end

    def carry_over(self):
        """Unfinished missions from the most recent earlier day, as brain-dump text."""
        today = dt.date.today().isoformat()
        for key in sorted((k for k in self.data["days"] if k < today), reverse=True):
            ms = self.data["days"][key].get("missions", [])
            left = [m for m in ms if not m["completed"]]
            if left:
                return "\n".join(f"{m['name']} (about {max(10, m['est_min'] - m['done_min'])} min left)"
                                 for m in left) + "\n"
            if ms:
                return ""
        return ""

    # ---------- missions ----------
    @staticmethod
    def make_mission(name, minutes, difficulty="medium", first_step=""):
        return {"name": name.strip(), "est_min": max(5, int(minutes)), "done_min": 0,
                "difficulty": difficulty if difficulty in LEVELS else "medium",
                "first_step": (first_step or "").strip(), "completed": False}

    def find(self, name):
        if not name:
            return None
        for m in self.day()["missions"]:
            if m["name"] == name:
                return m
        return None

    def open_missions(self):
        return [m for m in self.day()["missions"] if not m["completed"]]

    def current_mission(self):
        ms = self.open_missions()
        active = self.find(self.day().get("active"))
        if active and not active["completed"]:
            return active
        return ms[0] if ms else None

    def pick_task(self, level):
        """One task per session. High focus takes the hardest open task;
        otherwise keep going on the task already in progress."""
        ms = self.open_missions()
        if not ms:
            return None
        active = self.current_mission()
        if level == "high":
            if active and active.get("difficulty") == "high":
                return active
            return min(ms, key=lambda m: (DIFF_ORDER.get(m.get("difficulty"), 1), ms.index(m)))
        return active

    def session_minutes(self, m, level):
        length = self.lv(level)["focus"]
        if not m:
            return length
        remaining = max(5, m["est_min"] - m["done_min"])
        # Don't leave a tiny leftover for later: fold it into this session.
        return remaining if remaining <= length + self.cfg["fold_leftover_min"] else length

    # ---------- XP / ranks ----------
    def rank_info(self, xp=None):
        xp = self.data["xp"] if xp is None else xp
        idx = 0
        for i, (need, _) in enumerate(RANKS):
            if xp >= need:
                idx = i
        cur_need, name = RANKS[idx]
        nxt = RANKS[idx + 1] if idx + 1 < len(RANKS) else None
        return name, cur_need, nxt

    def rank_name(self):
        return self.rank_info()[0]

    def add_xp(self, n):
        before = self.rank_name()
        self.data["xp"] = max(0, self.data["xp"] + n)
        after = self.rank_name()
        if after == before:
            return None
        order = [r[1] for r in RANKS]
        return "promoted" if order.index(after) > order.index(before) else "demoted"

    def win(self, minutes):
        d = self.day()
        d["wins"] += 1
        self.data["wins_total"] += 1
        self.data["streak"] += 1
        self.data["best_streak"] = max(self.data["best_streak"], self.data["streak"])
        gain = max(20, int(minutes * XP_PER_MIN))
        bonus = self.data["streak"] % 3 == 0
        if bonus:
            gain += STREAK_BONUS_XP
        change = self.add_xp(gain)
        task = self.current_mission()
        lad = LD.match(self.data["ladder"], True, minutes, self.data["streak"], task["name"] if task else "")
        self.save()
        if self.db:
            self.db.log_session(dt.datetime.now(), minutes, task and task["name"], "win", lad["sp"])
        quests = self.after_change()
        return {"gain": gain, "bonus": bonus, "change": change, "ladder": lad, "quests": quests}

    def loss(self, reason, minutes=25):
        d = self.day()
        d["losses"] += 1
        self.data["losses_total"] += 1
        self.data["streak"] = 0
        lost = max(LOSS_XP_MIN, int(minutes * XP_PER_MIN / 2))
        change = self.add_xp(-lost)
        self.data["pending_punishment"] = self.make_punishment()
        d["log"].append({"time": dt.datetime.now().strftime("%H:%M"), "event": "LOSS", "reason": reason})
        task = self.current_mission()
        lad = LD.match(self.data["ladder"], False, minutes, 0, task["name"] if task else "")
        self.save()
        if self.db:
            self.db.log_session(dt.datetime.now(), minutes, task and task["name"], "loss:" + reason, lad["sp"])
        self.after_change()
        return {"change": change, "lost": lost, "ladder": lad}

    # ---------- ladder, quests, weekly status, Neon sync ----------
    def day_stats(self, date=None):
        d = self.day(date)
        return {"focus_min": d["focus_sec"] // self.M, "wins": d["wins"], "losses": d["losses"],
                "tasks_done": sum(1 for m in d["missions"] if m["completed"]),
                "distractions": d["distractions"]}

    def ladder_title(self):
        return LD.title(self.data["ladder"])

    def after_change(self):
        """Call after anything that changes today's numbers (session, task done, plan edit).
        Checks daily quests, saves, and pushes today to Neon. Returns newly completed quests."""
        today = dt.date.today()
        stats = self.day_stats(today)
        quests = LD.check_quests(self.data["ladder"], today.isoformat(), stats)
        for _, sp in quests:
            self.add_xp(sp)
        self.save()
        if self.db:
            d = self.day(today)
            lad = self.data["ladder"]
            self.db.sync_day(
                today.isoformat(),
                {"focus_min": stats["focus_min"], "xp": self.data["xp"], "wins": stats["wins"],
                 "losses": stats["losses"], "distractions": stats["distractions"], "rank": LD.title(lad)},
                [{"title": m["name"][:200], "est_min": m["est_min"], "done_min": m["done_min"],
                  "done": bool(m["completed"])} for m in d["missions"]],
                {"xp": self.data["xp"], "tier": LD.title(lad), "sp": lad["sp"], "streak": self.data["streak"],
                 "best_streak": self.data["best_streak"]})
        return quests

    def week_local(self, monday):
        """Same shape as Neon.week(), built from data.json (offline fallback)."""
        rows, done = [], []
        for i in range(-7, 7):
            day = monday + dt.timedelta(days=i)
            d = self.data["days"].get(day.isoformat())
            if d:
                rows.append((day, d.get("focus_sec", 0) // self.M, d.get("wins", 0), d.get("losses", 0)))
                if i >= 0:
                    done += [(day, m["name"]) for m in d.get("missions", []) if m.get("completed")]
        return rows, done

    # ---------- punishments ----------
    def make_punishment(self, exclude=None):
        pool = [p for p in self.cfg["punishments"] if p["text"] != exclude] or self.cfg["punishments"]
        p = random.choice(pool)
        n = p["base"] + p.get("step", 0) * max(0, self.day()["losses"] - 1)
        return {"text": p["text"].format(n=n), "template": p["text"],
                "seconds": n * self.M if p.get("timed") else 0}

    def swap_punishment(self):
        d = self.day()
        cur = self.data["pending_punishment"]
        if d["swap_used"] or not cur:
            return None
        d["swap_used"] = True
        self.data["pending_punishment"] = self.make_punishment(exclude=cur["template"])
        self.save()
        return self.data["pending_punishment"]

    def punishment_done(self):
        self.data["pending_punishment"] = None
        self.day()["punishments_done"] += 1
        change = self.add_xp(PUNISH_XP)
        self.save()
        return change

    # ---------- re-planning ----------
    def replan(self, now=None, level=None):
        """Lay out the rest of today, one task per session, at the given focus level.
        Returns (timeline [(datetime, label)], at_risk [mission names])."""
        now = now or dt.datetime.now()
        d = self.day(now.date())
        lvl = self.lv(level or d.get("last_level") or self.cfg["default_level"])
        F, B = lvl["focus"], lvl["break"]
        fold = self.cfg["fold_leftover_min"]
        end = self.day_end(now)
        timeline, at_risk = [], []
        t = now

        ms = self.open_missions()
        active = self.current_mission()
        if active in ms:
            ms.remove(active)
            ms.insert(0, active)

        for idx, m in enumerate(ms):
            if at_risk:
                at_risk.append(m["name"])
                continue
            remaining = max(5, m["est_min"] - m["done_min"])
            while remaining > 0:
                length = remaining if remaining <= F + fold else F
                if t + self.tdm(length) > end:
                    at_risk.append(m["name"])
                    break
                timeline.append((t, f"Focus {length}m: {m['name']}"))
                t += self.tdm(length)
                remaining -= length
                if remaining > 0 or idx < len(ms) - 1:
                    timeline.append((t, f"Break {B}m"))
                    t += self.tdm(B)

        timeline.sort(key=lambda x: x[0])
        return timeline, at_risk

    # ---------- end of day ----------
    def verdict_key(self):
        d = self.day()
        w, l = d["wins"], d["losses"]
        if w == 0:
            return "none"
        ratio = w / max(1, w + l)
        if w >= 6 and ratio >= 0.8:
            return "great"
        if ratio >= 0.7:
            return "good"
        if ratio >= 0.5:
            return "ok"
        return "bad"
