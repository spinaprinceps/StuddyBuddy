# StudyBuddy

A desktop study companion for Windows. Tell it your plan for the day and it turns that into tasks with time estimates. It runs focused study sessions, catches distractions, and keeps your study hours and a ranked ladder in a Neon Postgres database, so you can see a weekly summary.

## Features
- **Plan → tasks:** type or speak a messy plan ("DBMS ch 3 for 2h, maths problems, revise OS"). Gemini, or an offline parser if there's no key, turns it into ordered tasks with minute estimates and a first step for each.
- **Study hours tracking:** focus sessions at three levels (25, 50 or 90 min) with breaks. Every focused minute is logged.
- **Distraction control:** watches for distracting apps or sites (YouTube, Instagram...). Idling or quitting a session costs a small real-world penalty.
- **Ranked ladder:** every session is a match.
  - Win: +SP, scaled by length. A streak of 3+ wins puts you "on fire" for bonus SP.
  - Loss: −SP.
  - 100 SP promotes you a division. Moving up a tier takes a promo series (win 2 of 3).
  - After a promotion you get a demotion shield.
  - Tiers: Rookie → Scholar → Adept → Expert → Master → Sage → Legend.
  - Three daily quests each day.
- **Weekly status:** hours per day, total compared with last week, best day, win rate, tasks finished, and current rank. It reads from Neon, or from local data when offline.

## Setup
```bash
pip install -r requirements.txt
copy config.example.json config.json   # add your gemini_api_key (optional)
npm i -g neon@latest && neon login
neon init -y --agent claude-code --org-id <org-id> --project-id <project-id>   # writes .env
```
Then run `Start The Boss.vbs`, or `pythonw boss.py`.

The connection string is read from `.env` (`DATABASE_URL`). Tables are created automatically on first run. If Neon is unreachable, writes are queued in `neon_pending.json` and sent later.
