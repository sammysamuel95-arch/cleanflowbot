# SESSION PROTOCOL — How to Work with AI on CleanFlowBot
# For Sammy: follow this every time you open a new session

---

# STEP 1: PASTE THIS AT THE START OF EVERY SESSION

Copy-paste this opener:

```
Read the attached MASTER_CONTEXT.md first. It contains everything about
my bot — file map, data flow, known bugs, proven code, and rules.

Do NOT suggest changes until you've read it fully.
Do NOT rewrite any file from scratch.
Do NOT touch files marked as PROVEN.

After reading, tell me in 3 sentences what you understand about:
1. What the bot does
2. What's currently broken
3. What file(s) are relevant to today's task

Then I'll give you the task.
```

**Always attach:** `MASTER_CONTEXT.md`

---

# STEP 2: GIVE THE AI THE RIGHT FILES

Don't upload everything. Upload ONLY what's needed for the task:

### If the task is about MATCHING:
Upload: `matching/evidence.py`, `matching/alias_db.py`, `core/event_store.py`
Maybe: `matching/pair.py` (if relevant), `matching/classify.py`

### If the task is about FIRING:
Upload: `engine/fire_zone.py`, `engine/strategy.py`, `core/ev.py`
Maybe: `main.py` (just the _fire_cycle function — tell AI which lines)

### If the task is about CANCEL:
Upload: `engine/fire_zone.py` (check_cancels), `feeds/etopfun_api.py` (regret/match_show)
Context: explain the remain > 300s scenario

### If the task is about PS DATA:
Upload: `feeds/ps3838_ws.py`, `feeds/ps3838_parse.py`, `core/factory.py`
Maybe: `core/event_store.py`

### If the task is about ETOP DATA:
Upload: `feeds/etopfun_api.py`, `matching/classify.py`

### If the task is about DASHBOARD:
Upload: `main.py` (just the DASH section), explain what you want to see

### If the task is about CONFIG / PER-SPORT SETTINGS:
Upload: `config.py`, `engine/strategy.py`, `core/ev.py`

---

# STEP 3: USE THE TASK TEMPLATE

For every task, fill in this template and paste it to the AI:

```
═══════════════════════════════════════════════
TASK: [one sentence describing what needs to happen]
═══════════════════════════════════════════════

PROBLEM:
[What's broken or missing. Be specific. Include examples if possible.]

SCOPE:
[Which file(s) can be edited. Be explicit.]
- EDIT: matching/evidence.py
- DO NOT TOUCH: core/ev.py, core/factory.py, main.py

EXPECTED BEHAVIOR:
[What should happen after the fix. Be specific.]
- When X happens, the bot should do Y
- The log should show Z

VERIFY:
[How to check if the fix works.]
- Run bot, check log for [specific log line]
- Before fix: [what you see now]
- After fix: [what you should see]

CONSTRAINTS:
- Do NOT rewrite the whole file
- Make the MINIMUM change needed
- Show me the diff before applying
- Explain what each change does and why
═══════════════════════════════════════════════
```

---

# STEP 4: VERIFY BEFORE NEXT TASK

After each change:
1. Ask AI to show the exact diff (old → new)
2. Ask AI to explain what could break
3. Test: `python3 -u main.py > bot_output.log 2>&1 &`
4. Check: `tail -50 bot_output.log | grep ERROR`
5. If good → git commit → next task
6. If bad → revert: `cp file_backup.py file.py`

---

# EXAMPLE SESSIONS

## Example 1: Fix cancel tracking

```
Read MASTER_CONTEXT.md first.

TASK: Fix cancel — bot should cancel all bets when market remain extends past 300s

PROBLEM:
When a market extends (remain jumps from 30s back to 400s), the bot should
cancel all placed bets on that market. Currently it never triggers.
The logic in check_cancels looks correct but it can't find markets that
were fired. I think it loses track when fire_key changes.

SCOPE:
- EDIT: engine/fire_zone.py (check_cancels + FireState)
- READ ONLY: main.py (to understand how check_cancels is called)
- DO NOT TOUCH: core/ev.py, matching/*, feeds/*

EXPECTED BEHAVIOR:
- Market fires 5 items at remain=30s
- Etop extends market, remain=400s
- Bot detects remain > 300 AND total_fired > 0
- Bot calls get_cancellable_presses() → gets press IDs
- Bot calls regret() for each press ID
- Bot resets: total_fired=0, locked_side=0, total_value=0
- Log shows: [CANCEL] team1 vs team2 cancelled=5/5 freed=5

VERIFY:
- Search log for [CANCEL] lines
- Before: never appears even when market extends
- After: appears within 5s of market extension

CONSTRAINTS:
- Track fired markets by `mid` (never changes), not `fire_key`
- Do NOT rewrite fire_zone.py from scratch
- Show me the diff before applying
```

## Example 2: Add per-sport EV config

```
Read MASTER_CONTEXT.md first.

TASK: Add per-sport EV thresholds so NBA can fire at 0% and soccer at 2%

PROBLEM:
Currently all sports use the same MIN_EV, PHASE1_EV, PHASE2_EV, PHASE3_EV.
I want different values per sport. NBA = volume strategy (0% EV is fine).
Soccer = safety margin (need 2%+). Esports = current values.

SCOPE:
- EDIT: config.py (add sport_config section to bot_config.json)
- EDIT: main.py (_fire_cycle only — use sport-specific thresholds)
- DO NOT TOUCH: core/ev.py, matching/*, engine/fire_zone.py

EXPECTED BEHAVIOR:
- NBA market at EV=+0.3% → FIRES (because basketball phase3_ev=0.0)
- Soccer market at EV=+0.3% → BLOCKED (because soccer phase3_ev=1.5)
- Log shows: [FIRE_ZONE] phase=P3 min_ev=0.0% (basketball)

VERIFY:
- Check log for basketball markets: should fire at low EV
- Check log for soccer markets: should show FIRE_SKIP with "ev=+0.3%<P3:1.5%"

CONSTRAINTS:
- Add sport_config to bot_config.json (not hardcoded)
- Fall back to global defaults if sport not in config
- Show me the diff before applying
```

## Example 3: Add matcher self-healing

```
Read MASTER_CONTEXT.md first.

TASK: Add confidence decay — auto-unmatch markets where EV stays None too long

PROBLEM:
When matcher assigns wrong ps_event_id, compute_ev returns None forever.
The market stays MATCHED but useless. A human would notice "no odds for
this match" and try a different one. Bot should do the same.

SCOPE:
- EDIT: main.py (brain STEP 3 only — add confidence tracking)
- EDIT: matching/evidence.py (add rejected_eids support)
- DO NOT TOUCH: core/ev.py, core/factory.py, engine/*

EXPECTED BEHAVIOR:
- Market matched at T=0, ev=None
- After 5 cycles (15s) of ev=None: match_confidence drops from 100 to 50
- After 10 cycles (30s) of ev=None: confidence < 30 → UNMATCH
- ps_event_id set to None, state → UNMATCHED
- Old eid added to rejected list (never matched again for this pair)
- Matcher retries and finds a different eid

VERIFY:
- Log shows: [CONFIDENCE_DECAY] team1 vs team2 conf=80→64 (ev=None for 15s)
- Log shows: [AUTO_UNMATCH] team1 vs team2 eid=12345 → rejected, retrying
- After unmatch: market re-enters UNMATCHED pool and matcher retries

CONSTRAINTS:
- Track confidence on TrackedMarket (add field to __slots__)
- Only decay when market is MATCHED and ev is None
- Never decay when ev is computable (match is correct)
- Show me the diff before applying
```

---

# WHEN THINGS GO WRONG

## AI broke something
```
1. Revert: cp file_backup.py file.py
2. New session — don't try to fix the fix
3. Paste MASTER_CONTEXT.md + explain what the AI did wrong
4. Add to MASTER_CONTEXT.md "KNOWN BUGS" section
```

## AI is confused about data flow
```
Upload: MASTER_CONTEXT.md + the specific files it's confused about
Say: "Read the DATA FLOW section. Tell me what compute_ev reads
and where that data comes from. Then tell me your plan."
```

## AI wants to rewrite everything
```
Say: "No. Read rule #1 in MASTER_CONTEXT.md. I need the MINIMUM
change to fix this ONE problem. Show me a 5-line diff, not a
200-line rewrite."
```

## AI keeps losing context mid-session
```
This means the conversation is too long. Start a new session.
Copy the LAST working state into MASTER_CONTEXT.md.
New session = fresh context + master doc.
```

---

# MAINTAINING MASTER_CONTEXT.md

After every successful change:
1. Update "KNOWN BUGS" (remove fixed ones)
2. Update "FILE MAP" if new files were created
3. Update "CONFIGURATION" if values changed
4. Add to "PROVEN & NEVER TOUCH" if a fix was verified
5. Keep a one-line changelog at the bottom

This document is YOUR memory. Every AI session is stateless.
The only persistent memory is this file + git history.

---

# CHANGELOG

- S28: Initial creation from full codebase review with Opus
