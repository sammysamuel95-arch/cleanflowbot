# SESSION STARTUP PROTOCOL
# Read this FIRST. Every new session. No exceptions.

## BEFORE TOUCHING ANY CODE

1. Read `docs/MASTER_CONTEXT.md` — full context, all sessions
2. Read `docs/MODULAR_ARCHITECTURE.md` — module structure + rules
3. Read `docs/S33_PRIORITIES.md` — what to do this session
4. Confirm with user: "I've read master context, I see <N> bugs, <M> pending items, priorities are <list>. Is this still accurate?"

## RULES THAT PREVENT THE MOST DAMAGE

### Rule 1: NEVER make the same mistake twice
- If user points out an error, note it in your head
- If you're about to do the same thing again, STOP and ask
- Previous sessions wasted 2 days on silent skip bugs — don't repeat

### Rule 2: Check for duplication BEFORE fixing
Before any fix:
```
grep -n "<the pattern you're about to fix>" modules/fire_engine.py
grep -n "<the pattern>" modules/valuator.py
```
If the pattern exists in 2+ places, fix ALL of them or NONE.

### Rule 3: Log every `continue`
Before editing any file that has `continue` statements:
```
grep -n "continue" <file>.py
```
Every continue either:
- Has a log line immediately above (log_info, log_warn, log_prefire)
- OR is a state filter (m.state != X) and is fine to be silent
Never add `if X: log` then `continue`. Always log, or never log, but consistent.

### Rule 4: Trust evidence over conclusions
- If user shows a screenshot, that's ground truth
- If your analysis conflicts with the screenshot, YOUR analysis is wrong
- Never say "market closed because PS adjusted" without proof in logs

### Rule 5: One change, verify, next change
- Ship ONE fix per instruction file
- User applies it, tests it, confirms working
- Then ship the next
- Do NOT bundle 5 fixes — if one breaks, all roll back

### Rule 6: Use the diagnostic tools
- `tools/test_fire_spacing.py` — test fire timing without live matches
- `tools/test_kills_ev.py` — test kills eid resolution offline
- `tools/bus_diagnostic.py` — live DataBus health
Create new tools/test_*.py when debugging recurring issues.

### Rule 7: Instruction file format
Every instruction file starts with:
```
Do not run diagnostic commands, do not investigate, do not use sleep/background tasks. Just execute exactly what I tell you.
```
One change per file. Verify steps at the end. Downloadable from /mnt/user-data/outputs/.

## WHAT USER EXPECTS

- Respect for his time (2 days debugging = user frustration)
- No guessing — if you don't know, say "I don't know, need to see X"
- Trust his domain knowledge (he's been value betting 5+ years)
- Honesty about mistakes — acknowledge, don't deflect
- Bahasa Indonesia OK, English OK, match his energy
- He decides when to stop. Never suggest rest/sleep/breaks.

## RED FLAGS — STOP AND THINK

If you're about to:
- Say "that's just how PS adjusts odds" → check logs first
- Bundle 5 fixes in one instruction → split them
- Rewrite a whole file → stop, make surgical edit
- Skip reading context "because it's obvious" → read it
- Guess at a root cause → get the data first

## WHAT MAKES OPUS USEFUL HERE

- Architecture thinking (the refactor plan, module boundaries)
- Pattern recognition across codebase
- Writing precise Claude Code instructions
- Saying "I don't know" when data is missing
- Catching user's own errors politely (see: 429 analysis in S32)
