"""Everything The Boss says. Placeholders like {mission} are filled in at runtime.

Available placeholders: days, day, total, wasted, wins, losses, streak, rank, xp,
mission, app, title, mins, gain, minutes, starter, first
"""
import random


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def pick(lines, **ctx):
    line = random.choice(lines) if isinstance(lines, (list, tuple)) else lines
    return line.format_map(_Safe(ctx))


ORDER = [
    "Objective: {mission}.",
    "Agent. New orders: {mission}.",
    "Your target: {mission}. Phone in the other room.",
    "Intel says {mission} isn't finished. Fix that.",
    "{days} days to the deadline. Next objective: {mission}.",
]
FIRST_MOVE = "First move: {first}"
FOCUS_ASK = "Continue this one, or choose your target. Your call."
SESSION_START = "{minutes} minutes on {mission}. Locked in. I'll stay quiet unless you drift."
SESSION_HINT = "Click me anytime to extend, finish early or take a break."
EXTENDED = "+{n} min. {minutes} min total on {mission}. Keep the momentum."
DONE_EARLY = "{mission}: done in {worked} min. Estimates are guesses; results aren't."
PAUSED = "Paused at {worked} min. {left} left on {mission}. {mins}-minute break. Back soon."
RESUME = "Break over. {left} left on {mission}. Resume, or switch targets?"
RESUMED = "Resumed: {mission}. {left} to go."
STOPPED_WIN = "Stopped at {worked} min. Solid block. Counts as a win."
STOPPED_SHORT = "Stopped at {worked} min. Too short for a win, but the minutes are logged. No penalty."
LEVEL_BUTTONS = [("Low", "low"), ("Medium", "medium"), ("High", "high")]

LEVEL_ACCEPT = {
    "low": "Low focus. Fine. Just {starter} minutes on {mission}. That's all I ask. Go.",
    "medium": "{minutes} minutes on {mission}. Locked in.",
    "high": "{minutes} minutes of deep work. Hardest target first: {mission}. Make me proud.",
}
STARTER_DONE = "{starter} minutes down. Momentum's yours. Continuing to {minutes}."
STARTER_STOP = "Stop at {starter}"

ANALYZING = "Decrypting your intel..."
AI_OK = "Intel analyzed: {n} tasks, {work} of work. Review my estimates, then lock it in."
AI_FAIL = "Gemini didn't answer ({err}). Offline estimates. Check them."
NO_KEY = "No Gemini key. I'm using rough offline estimates. Check them."
AI_ADDED = "Added {n} new task(s) to the board."

# Escalating pressure while an order is being ignored (1 = nudge ... 4 = full roast)
NAG = {
    1: [
        "Agent. Your objective is waiting. So am I.",
        "Order still open: {mission}. Clock's ticking.",
        "I don't repeat orders often, agent. {mission}. Now.",
    ],
    2: [
        "{days} days to the deadline, and you've already given {wasted} minutes today to nothing.",
        "Every minute you stall is a minute someone else in your class is using.",
        "You said you wanted those grades. This is what wanting it looks like: pressing Accept.",
    ],
    3: [
        "Someone else is finishing this chapter right now. You're... doing this.",
        "You have real potential, agent. Right now you're spending it on nothing.",
        "The exam won't ask how many reels you watched. It'll ask what you studied.",
    ],
    4: [
        "You told me you wanted this. Your screen time says otherwise. Prove the screen time wrong.",
        "Last warning. Ignore this order and it goes down as a LOSS.",
        "{wins} wins, {losses} losses today. Is that the agent you want to be on the 12th?",
    ],
}

ACCEPTED = [
    "THERE you are. Move out.",
    "Clock's running. Make it count.",
    "Good. I'm watching.",
    "That's the agent I recruited. Go.",
    "Locked in. No distractions, no excuses.",
]

WIN = [
    "Order complete. +{gain} XP. That's how agents get promoted.",
    "Clean execution. +{gain} XP. {wins} wins today.",
    "Mission block secured. +{gain} XP. Keep this up and the 12th is yours.",
    "Good work, agent. +{gain} XP. Streak: {streak}.",
]

STREAK_BONUS = "Streak of {streak}! +50 bonus XP. You're dangerous when you're focused."

LOSS = {
    "ignored": [
        "Order ignored. That's a LOSS. You just handed time to the competition.",
        "You let the order expire. LOSS recorded. I expected better.",
    ],
    "abandoned": [
        "Abandoning mid-mission? LOSS. Agents finish what they start.",
        "You walked away from the objective. LOSS recorded.",
    ],
    "compromised": [
        "Order COMPROMISED. Two strikes. LOSS recorded.",
        "You got caught twice. Mission compromised. LOSS.",
    ],
}

DISTRACTED = [
    "STRIKE ONE. {app} during an order? Close it within 60 seconds or the order is compromised.",
    "I see {app}, agent. Strike one. Get back to {mission}. Now.",
    "{app}? Seriously? With {days} days left? Strike one. Close it.",
]

YT_ASK = "YouTube detected. Lecture or timepass? Be honest. I'll know."

YT_LECTURE_OK = "Lecture it is. YouTube is cleared for this session. Take notes. I'll be checking."

IDLE_ASK = [
    "Agent, report your status. No movement on your end for a while.",
    "Are you still there, or did the couch win?",
]
IDLE_STRIKE = "No response. That counts as a strike."

BREAK = [
    "Break. Stand up, drink water, look out a window. NOT at your phone.",
    "Break time. Stretch. Walk. Your phone is not a break, it's a trap.",
    "Rest your eyes. Breathe. Back in a few, agent.",
]
LONG_BREAK = "Long break, agent. You earned it. Eat something real. No scrolling."

BREAK_OVER = "Break's over. Back to the field."

WAKE = [
    "Rise, agent. Day {day} of {total}. {days} days to the deadline. Sunlight, water, then report for briefing.",
    "Up. Now. Your classmates are already awake. Briefing in 2 minutes.",
    "Morning, agent. Don't touch your phone. Face the sun, drink water, then brief me.",
]

BRIEF_LOCKED = "Plan locked: {mins} missions. First order coming up. Let's work."


MISSION_DONE = [
    "MISSION COMPLETE: {mission}. +150 XP. That's one off the board.",
    "{mission}: DONE. +150 XP. Next.",
]
ALL_DONE = "Every mission on today's board is complete. Outstanding work, agent."

LOW_ENERGY = "Energy low. Pick Low focus next and take something lighter: flashcards, notes cleanup, or easy revision."

AT_RISK = "At this rate {title} won't get done today. Move faster or cut scope."

PROMOTED = "PROMOTION. You are now: {rank}. Don't let it go to your head."
DEMOTED = "DEMOTED to {rank}. Earn it back."

PUNISH_INTRO = "Reset protocol. Do this before your next order."
PUNISH_NAG = [
    "Penalty first, agent. Then orders.",
    "That penalty isn't going to do itself.",
    "Stalling on a penalty is still stalling.",
]
PUNISH_DONE = "Penalty complete. Baseline restored. +25 XP. Now, back to work."

STAND_DOWN = "Standing down for {mins} minutes. The deadline doesn't."
STAND_DOWN_MID = "Leaving mid-mission. No win for this one. Noted."
OFF_DUTY = "Off duty for today. Fine. I'll see you at briefing tomorrow."
NO_PLAN = "No mission briefing yet. Brief me first."

STATUS_IDLE = "Standing by. {wins} W / {losses} L today. Rank: {rank}."

VERDICT = {
    "great": "Exceptional. That's how you win the 12th. Rest well. Same energy tomorrow.",
    "good": "Solid day, agent. Tomorrow I want {next} wins.",
    "ok": "Acceptable. Not great. Tomorrow I want {next} wins.",
    "bad": "That was a bad day. It happens once. It does NOT happen twice. Tomorrow: {next} wins.",
    "none": "No orders completed today. I'm not angry. I'm disappointed. Tomorrow we start early.",
}

RESTORED = ("I went down, but I kept your session: {mission}, {left} left.\n"
            "The time I was offline doesn't count against you. Resume?")
