"""
Force test: does compute_ev find kills OU line?
Compares valuator path vs fire_engine path.

Run alongside the bot (reads live ps_store).
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.factory import StandardStore
from core.ev import compute_ev
from core.models import EtopMarket

# Load event_map to find kills events
em_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'event_map.json')
with open(em_path) as f:
    event_map = json.load(f)

# Find all (Kills) events
kills_events = {}
regular_events = {}
for eid, info in event_map.items():
    home = info.get('home', '')
    away = info.get('away', '')
    if '(Kills)' in home:
        kills_events[eid] = info
    elif info.get('sp') == 12:  # esports
        regular_events[eid] = info

print(f"Total events: {len(event_map)}")
print(f"Esports events: {len(regular_events)}")
print(f"Kills events: {len(kills_events)}")
print()

# Show kills events
print("═══ Kills Events in PS Store ═══")
for eid, info in kills_events.items():
    print(f"  eid={eid} {info['home']} vs {info['away']} | {info.get('league','')}")

print()

# For each kills event, find the matching regular event
print("═══ Testing compute_ev ═══")

# We need the live ps_store. Import it from the running bot's data.
# The bot's ps_store is in memory, not on disk.
# But we can build a StandardStore from the WS data.

# Alternative: just test find_event_id
from core.event_store import TheOnlyStore

store = TheOnlyStore()

# Load events from event_map into store
for eid_str, info in event_map.items():
    eid = int(eid_str)
    store.register_event(eid, info['home'], info['away'],
                        info.get('sp', 0), info.get('league', ''))

print(f"Store loaded: {len(event_map)} events")
print()

# Test find_event_id for kills variants
print("═══ Testing find_event_id for Kills ═══")
test_teams = [
    ("KT Rolster", "Dplus"),
    ("Weibo Gaming", "IG"),
    ("ThunderTalk Gaming", "Ultra Prime"),
    ("Yellow Submarine", "Lynx"),
    ("Nemiga", "Inner Circle"),
]

for t1, t2 in test_teams:
    kills_name1 = t1 + " (Kills)"
    kills_name2 = t2 + " (Kills)"

    # Regular eid
    reg_eid = store.find_event_id(t1, t2)
    # Kills eid
    kills_eid = store.find_event_id(kills_name1, kills_name2)

    print(f"\n  {t1} vs {t2}:")
    print(f"    Regular eid: {reg_eid}")
    print(f"    Kills eid:   {kills_eid} (searched: '{kills_name1}' vs '{kills_name2}')")

    if kills_eid is None:
        # Try alternate search
        # Maybe PS stores it differently
        for eid_str, info in event_map.items():
            h = info.get('home', '').lower()
            a = info.get('away', '').lower()
            if ('kill' in h or 'kill' in a) and (t1.lower()[:5] in h or t1.lower()[:5] in a):
                print(f"    FOUND in event_map: eid={eid_str} '{info['home']}' vs '{info['away']}'")

print()

# Now test with the bot's live StandardStore (factory)
# This is the ps_store that compute_ev uses
print("═══ Testing with Live PS Data (factory.json) ═══")
factory_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'ps_store.json')
if os.path.exists(factory_path):
    print(f"  ps_store.json exists")
else:
    print(f"  ps_store.json NOT found")
    # Try to find what factory data is available
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    for f in sorted(os.listdir(data_dir)):
        if 'store' in f.lower() or 'factory' in f.lower() or 'ps' in f.lower():
            print(f"    Found: {f}")

# Check what the bot's container has for kills markets
print()
print("═══ Check dash_state for kills markets ═══")
dash_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'dash_state.json')
try:
    with open(dash_path) as f:
        dash = json.load(f)
    for mkt in dash.get('markets', []):
        label = mkt.get('ln', '')
        if 'kill' in label.lower() or 'Kill' in label:
            print(f"  {mkt.get('t1','?')} vs {mkt.get('t2','?')} [{label}]")
            print(f"    ev1={mkt.get('e1')} ev2={mkt.get('e2')}")
            print(f"    ps_fair={mkt.get('pf','?')}")
            print(f"    ps_age={mkt.get('pa','?')}")
            print(f"    state={mkt.get('st','?')}")
            print(f"    remain={mkt.get('s','?')}s")
            print(f"    mid={mkt.get('mid','?')}")
            print(f"    eid={mkt.get('eid','?')}")
except Exception as e:
    print(f"  dash_state read failed: {e}")
