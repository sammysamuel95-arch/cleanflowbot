# S33 PRIORITIES — Cleanup + Consolidation

## CONTEXT
S32 shipped many features but created duplication:
- Kills eid fallback: copy-pasted in 3 places (valuator + 2x fire_engine)
- FIRE_SKIP gate check: duplicated in candidate building + fire loop
- Silent continues: 8 found in fire_engine.py

S33 goal: consolidate WITHOUT breaking what works.

## PRIORITY 1: Match Kills OU at matcher level (not fallback)

### THE REAL PROBLEM
Matcher matches etop "Total Kills" to REGULAR PS event.
PS publishes kills OU under SEPARATE "(Kills)" event.
Current workaround: kills eid fallback in 3 places (valuator + 2x fire_engine).

### THE REAL FIX
At match time, if etop market is Total Kills OU on a per-map market, 
matcher should look up the "(Kills)" event DIRECTLY and store THAT eid.

### WHERE
modules/matcher.py — after structured_matcher returns AUTO_MATCH.

### LOGIC
```python
# After regular match is found:
if em.market in ('ou', 'team_total') and em.map_num > 0 and 'Kills' in m.label:
    # This is a kills OU market — PS uses separate "(Kills)" event
    evt = ps_store._event_teams.get(regular_eid)
    if evt:
        ps_h, ps_a = evt
        kills_eid = (ps_store.find_event_id(f"{ps_h} (Kills)", f"{ps_a} (Kills)")
                  or ps_store.find_event_id(f"{ps_a} (Kills)", f"{ps_h} (Kills)"))
        if kills_eid:
            em.ps_event_id = kills_eid
            m.ps_event_id = kills_eid
            log_info(f"[MATCH_KILLS] {m.label} regular={regular_eid} → kills={kills_eid}")
            # DO NOT assign regular_eid — kills eid is the correct one from now on
```

### WHAT GETS DELETED
After this fix, remove kills fallback from:
- modules/valuator.py (lines ~84-95, the kills_eid block)
- modules/fire_engine.py candidate building (lines ~121-135)
- modules/fire_engine.py fire loop (lines ~343-355)

compute_ev in core/ev.py already handles alt_eids internally. No resolve_ev() needed.

### VERIFICATION
Run bot during LoL/Dota match. Check:
```
grep "MATCH_KILLS" data/log/bot.log
```
Each Total Kills market should have ONE MATCH_KILLS log at match time.
Panel shows ps_fair populated immediately (not after a cycle delay).

### WHY THIS IS BETTER
- 1 lookup per match (once) vs 3 fallback lookups per fire cycle
- No fallback complexity in 3 files
- matcher has correct context: it KNOWS the market type at match time
- resolve_ev() function not needed anymore — simpler architecture
- If PS restructures, fix is in ONE place (matcher)

### WHAT STAYS
compute_ev's internal alt_eid loop — that handles mk=1 vs mk=3 splits,
which is different from kills eid. Don't touch.

## PRIORITY 2: check_gates() in modules/fire_engine/gates.py
(unchanged — still replace 2 gate check copies with 1 function)

## PRIORITY 3: Audit remaining silent continues
(unchanged)

## PRIORITY 4: MIN_EV removal or rename

cfg.MIN_EV caused the FIRE_SKIP bug. Two options:
- Remove entirely (phase_min_ev already handles this)
- Rename to MIN_LOG_THRESHOLD and make its purpose explicit

Decide with user first.

## DO NOT TOUCH

- DataBus (working)
- Session immortality (working)
- curl_cffi auth (working)
- Classify.py type mappings (verified correct)
- Structured matcher (33/33 live proven)
- PS store factory (proven across 7 reviews)
- core/ev.py compute_ev — its internal alt_eid fallback handles mk=1/mk=3 eid splits

## VERIFICATION AFTER EACH PRIORITY

1. Run bot for 30 minutes
2. Check for new error patterns in logs
3. Verify ≥1 kills OU market fires at P3
4. Git commit with descriptive message
5. Update MASTER_CONTEXT.md with CHANGELOG entry

If any step fails → git revert, investigate, don't stack more changes.
