"""Data containers for the v2perfectbot data factory.

EtopMarket: one market from etopfun (source of truth for what markets exist).
No logic, no imports except stdlib.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EtopMarket:
    """One etop market, fully resolved at pairing time.

    Created by discover_markets_from_listing() after:
    1. classify_etop_sub() determines market type
    2. pair.py resolves PS team names
    3. event_map lookup gets PS event_id
    """
    # ── Etop identity (from listing + classify) ──
    team1: str                              # etop vs1 name
    team2: str                              # etop vs2 name
    o1: float                               # vs1 odds (Asian format)
    o2: float                               # vs2 odds (Asian format)
    market: str                             # ml, hdp, ou, team_total, f10k, duration, unknown_X
    line: float                             # absolute handicap/total value
    map_num: int                            # 0=series, 1-5=map
    label: str                              # human readable: "Keyd Stars -1.5", "Moneyline"
    giving_side: Optional[str]              # 'team1' or 'team2' (from classify offerTeam). None for ML/OU.
    mid: str                                # etop sub-match ID
    parent_id: str                          # etop parent ID
    remain: float                           # seconds remaining
    can_press: bool                         # can place bet
    raw_type: int = 0                       # original mtype for debugging
    league: str = ''
    url: str = ''
    game: str = ''                          # cs2, dota, lol, valorant, soccer, basketball, unknown

    # ── PS resolution (set ONCE by pair.py + event_map lookup) ──
    ps_name_team1: Optional[str] = None     # exact PS name for etop team1
    ps_name_team2: Optional[str] = None     # exact PS name for etop team2
    ps_event_id: Optional[int] = None       # PS event ID from event_map

    # ── Derived properties ──

    @property
    def giving_team_etop(self) -> Optional[str]:
        """Etop name of team that gives handicap."""
        if self.giving_side == 'team1':
            return self.team1
        if self.giving_side == 'team2':
            return self.team2
        return None

    @property
    def giving_team_ps(self) -> Optional[str]:
        """PS name of team that gives handicap. For store lookup."""
        if self.giving_side == 'team1':
            return self.ps_name_team1
        if self.giving_side == 'team2':
            return self.ps_name_team2
        return None

    @property
    def fire_key(self) -> str:
        """Unique key for monitor/fire dedup. Includes map_num to avoid collision."""
        return f"{self.team1}|{self.team2}|{self.label}|m{self.map_num}"

    def update_odds(self, o1: float, o2: float, remain: float, can_press: bool = True):
        """Fresh odds from get_listing or get_odds_live."""
        self.o1 = o1
        self.o2 = o2
        self.remain = remain
        self.can_press = can_press
