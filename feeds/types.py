"""
v2perfectbot — feeds/types.py
Dataclasses for all data flowing through the pipeline.
Every module uses these types. No raw dicts past the extraction layer.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PSLine:
    """A single PS3838 odds line from WS or REST.

    Covers all market types: ML, HDP, OU, team_total.
    fair_* fields are no-vig fair odds (always > 1.0 when valid).
    """
    market: str          # "ml", "hdp", "ou", "team_total"
    home: str            # PS home team name
    away: str            # PS away team name
    sp: int              # Sport ID (4=basketball, 12=esports, 29=soccer)
    sp_name: str         # "basketball", "esports", "soccer"
    league: str          # Raw league name from PS
    map_num: int         # 0=full game/series, 1/2/3=maps/halves
    event_id: Optional[int] = None
    src: str = "ws"      # "ws" or "rest"

    # ML / HDP fields
    home_odds: float = 0.0
    away_odds: float = 0.0
    fair_home: float = 0.0
    fair_away: float = 0.0
    home_hdp: float = 0.0   # HDP only: home handicap (negative = home gives)

    # OU / team_total fields
    total: float = 0.0
    over_odds: float = 0.0
    under_odds: float = 0.0
    fair_over: float = 0.0
    fair_under: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dict for backward compat with existing code."""
        d = {
            "market": self.market, "home": self.home, "away": self.away,
            "sp": self.sp, "sp_name": self.sp_name, "league": self.league,
            "map_num": self.map_num, "event_id": self.event_id, "src": self.src,
        }
        if self.market in ("ml", "hdp"):
            d.update({
                "home_odds": self.home_odds, "away_odds": self.away_odds,
                "fair_home": self.fair_home, "fair_away": self.fair_away,
            })
            if self.market == "hdp":
                d["home_hdp"] = self.home_hdp
        if self.market in ("ou", "team_total"):
            d.update({
                "total": self.total,
                "over_odds": self.over_odds, "under_odds": self.under_odds,
                "fair_over": self.fair_over, "fair_under": self.fair_under,
            })
        return d

    @staticmethod
    def from_dict(d: dict) -> "PSLine":
        """Create PSLine from legacy dict (for migration)."""
        return PSLine(
            market=d.get("market", ""),
            home=d.get("home", ""),
            away=d.get("away", ""),
            sp=d.get("sp", 0),
            sp_name=d.get("sp_name", ""),
            league=d.get("league", ""),
            map_num=d.get("map_num", 0),
            event_id=d.get("event_id"),
            src=d.get("src", "ws"),
            home_odds=d.get("home_odds", 0.0),
            away_odds=d.get("away_odds", 0.0),
            fair_home=d.get("fair_home", 0.0),
            fair_away=d.get("fair_away", 0.0),
            home_hdp=d.get("home_hdp", 0.0),
            total=d.get("total", 0.0),
            over_odds=d.get("over_odds", 0.0),
            under_odds=d.get("under_odds", 0.0),
            fair_over=d.get("fair_over", 0.0),
            fair_under=d.get("fair_under", 0.0),
        )


@dataclass
class EtopMarket:
    """A single etopfun sub-match market.

    Extracted from list.do API response sublist entries.
    """
    team1: str              # vs1 name
    team2: str              # vs2 name
    market: str             # "ml", "hdp", "ou", "f10k"
    bracket: str            # Display label e.g. "Game Winner, Map1"
    league: str             # League name from parent
    map_num: int = 0        # 0=series, 1/2/3=maps

    # Odds (parimutuel pool ratios)
    odds1: float = 0.0      # vs1 odds
    odds2: float = 0.0      # vs2 odds

    # HDP specific
    hdp_line: Optional[float] = None   # e.g. -0.5
    hdp_side: Optional[str] = None     # "team1" or "team2" or None
    ou_side: Optional[str] = None      # "over" or "under" (OU markets)

    # Timing
    remain_secs: float = 0.0    # Seconds until live
    can_press: bool = False     # Betting enabled?

    # IDs
    match_id: str = ""          # Sub-match ID (for API calls)
    parent_id: str = ""         # Parent match ID
    gw_id: str = ""             # Game Winner offer match ID
    url: str = ""               # URL with sub-match id

    # Classification
    etop_type: int = 0          # Raw type from API (2,3,8,9,13)

    def to_dict(self) -> dict:
        """Convert to dict for backward compat."""
        return {
            "team1": self.team1, "team2": self.team2,
            "market": self.market, "bracket": self.bracket,
            "league": self.league, "map": self.map_num,
            "odds1": self.odds1, "odds2": self.odds2,
            "hdp_line": self.hdp_line, "hdp_side": self.hdp_side,
            "ou_side": self.ou_side,
            "url": self.url,
        }


@dataclass
class MatchedPair:
    """Result of pair matching + line finding.

    This is what gets added to the monitor watchlist.
    Contains everything needed to calculate EV and fire.
    """
    etop: EtopMarket
    ps: PSLine
    team1_is_home: bool        # etop team1 maps to PS home?
    fire_key: str              # Unique key: "team1|team2|bracket"
    confidence: int = 0        # Fuzzy match confidence (0-100)
    match_src: str = ""        # How pair was found: "alias", "fuzzy", "cache"


@dataclass
class FireResult:
    """Result of a single bet placement attempt."""
    success: bool
    message: str = ""
    items_fired: int = 0
    item_ids: list = field(default_factory=list)
    ev_at_fire: float = 0.0
    side: str = ""             # "left" or "right"


@dataclass
class InventoryItem:
    """A single inventory item available for betting."""
    id: int                    # Item ID (for API press.do)
    value: float               # Item value (should be ~$1)
    name: str = ""             # Short name for logging


@dataclass
class MarketSlice:
    """Per-market inventory reservation.

    Prevents market A's items from colliding with market B.
    """
    fire_key: str
    items: list = field(default_factory=list)       # List of InventoryItem
    consumed_ids: set = field(default_factory=set)   # IDs already fired

    @property
    def available(self) -> list:
        """Items not yet consumed."""
        return [it for it in self.items if it.id not in self.consumed_ids]

    @property
    def total_consumed(self) -> int:
        return len(self.consumed_ids)
