"""Ranked ladder: every focus session is a match.

Tiers with 4 divisions (IV -> I), Study Points (SP) per division, promo series
into a new tier, demotion shield after a promotion, win-streak bonus, and
three daily quests. Pure logic on a plain dict so it is easy to test and store.
"""
import datetime as dt

TIERS = ["Rookie", "Scholar", "Adept", "Expert", "Master", "Sage", "Legend"]
DIVS = ["IV", "III", "II", "I"]
SP_PER_DIV = 100
WIN_SP_BASE = 15           # + 1 SP per 5 focused minutes, capped
WIN_SP_CAP = 35
LOSS_SP = 18
STREAK_FROM = 3            # 3+ wins in a row = "on fire"
STREAK_SP = 5
SHIELD_GAMES = 3           # losses after a promotion that can't drop you a division
PROMO_WINS, PROMO_GAMES = 2, 3
HISTORY_KEEP = 30

QUESTS = [  # (id, text, key in day stats, goal, sp reward)
    ("focus", "Focus for 2 hours", "focus_min", 120, 15),
    ("wins", "Win 3 sessions", "wins", 3, 10),
    ("tasks", "Finish 2 tasks", "tasks_done", 2, 10),
]


def new_state():
    return {"tier": 0, "div": 0, "sp": 0, "promo": None, "shield": 0, "peak": [0, 0],
            "history": [], "quests": {}}


def ensure(state):
    base = new_state()
    for k, v in base.items():
        state.setdefault(k, v)
    return state


def title(state):
    t = TIERS[state["tier"]]
    return t if t == TIERS[-1] else f"{t} {DIVS[state['div']]}"


def _ladder_pos(state):
    return state["tier"] * len(DIVS) + state["div"]


def win_sp(minutes, streak):
    sp = min(WIN_SP_CAP, WIN_SP_BASE + int(minutes) // 5)
    on_fire = streak >= STREAK_FROM
    return sp + (STREAK_SP if on_fire else 0), on_fire


def _promote_div(s):
    """Advance one division (never across a tier: that needs a promo series)."""
    s["div"] += 1
    s["sp"] = 0
    s["shield"] = SHIELD_GAMES


def _promote_tier(s):
    s["tier"] += 1
    s["div"] = 0
    s["sp"] = 0
    s["promo"] = None
    s["shield"] = SHIELD_GAMES


def match(state, won, minutes, streak, task="", now=None):
    """Record one match. Returns {"sp": delta, "events": [...], "title": str, "on_fire": bool}."""
    s = ensure(state)
    now = now or dt.datetime.now()
    events = []
    before = _ladder_pos(s)
    s["history"] = (s["history"] + [{"t": now.isoformat(timespec="minutes"), "w": bool(won),
                                     "min": int(minutes), "task": task[:60]}])[-HISTORY_KEEP:]
    on_fire = False
    top = s["tier"] == len(TIERS) - 1

    if s["promo"] is not None:  # in a promo series: only W/L count, no SP change
        key = "w" if won else "l"
        s["promo"][key] += 1
        if s["promo"]["w"] >= PROMO_WINS:
            _promote_tier(s)
            events.append("tier_up")
        elif s["promo"]["l"] > PROMO_GAMES - PROMO_WINS:
            s["promo"] = None
            s["sp"] = SP_PER_DIV - 25
            events.append("promo_failed")
        delta = 0
    elif won:
        delta, on_fire = win_sp(minutes, streak)
        s["sp"] += delta
        if not top and s["sp"] >= SP_PER_DIV:
            if s["div"] < len(DIVS) - 1:
                _promote_div(s)
                events.append("div_up")
            else:
                s["sp"] = SP_PER_DIV
                s["promo"] = {"w": 0, "l": 0}
                events.append("promo_start")
    else:
        delta = -LOSS_SP
        s["sp"] += delta
        if s["sp"] < 0:
            if s["shield"] > 0 or (s["tier"] == 0 and s["div"] == 0):
                s["sp"] = 0
                if s["shield"] > 0:
                    events.append("shielded")
            elif s["div"] > 0:
                s["div"] -= 1
                s["sp"] = SP_PER_DIV - 25
                events.append("div_down")
            else:  # bottom division of a tier: drop to the tier below
                s["tier"] -= 1
                s["div"] = len(DIVS) - 1
                s["sp"] = SP_PER_DIV - 25
                events.append("tier_down")
        if s["shield"] > 0:
            s["shield"] -= 1

    if _ladder_pos(s) > _ladder_pos({"tier": s["peak"][0], "div": s["peak"][1]}):
        s["peak"] = [s["tier"], s["div"]]
    if _ladder_pos(s) < before and not events:
        events.append("down")
    return {"sp": delta, "events": events, "title": title(s), "on_fire": on_fire}


def check_quests(state, day_key, stats):
    """stats: {"focus_min", "wins", "tasks_done"}. Returns newly completed quests [(text, sp)]."""
    s = ensure(state)
    done = s["quests"].setdefault(day_key, [])
    for k in list(s["quests"]):
        if k != day_key:
            del s["quests"][k]
    new = []
    for qid, text, key, goal, sp in QUESTS:
        if qid not in done and stats.get(key, 0) >= goal:
            done.append(qid)
            if s["promo"] is None and s["tier"] < len(TIERS) - 1:
                s["sp"] = min(SP_PER_DIV - 1, s["sp"] + sp)  # the promotion itself comes from a win
            new.append((text, sp))
    return new


def quest_progress(state, day_key, stats):
    done = ensure(state)["quests"].get(day_key, [])
    return [(text, min(stats.get(key, 0), goal), goal, qid in done) for qid, text, key, goal, _ in QUESTS]


def record(state):
    h = ensure(state)["history"]
    return sum(m["w"] for m in h), sum(not m["w"] for m in h)
