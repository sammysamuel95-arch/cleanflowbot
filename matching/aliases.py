"""
v2perfectbot — matching/aliases.py
Self-learning team name alias database.

Extracted from bot_working.py lines 907-994, 1021-1029.
Loads hardcoded aliases + learned aliases from aliases.json.
Auto-saves new aliases on successful pair matches.
"""

import json
import re
import time
from thefuzz import fuzz

from config import ALIASES_FILE
from core.logger import log_info, log_error


# ── Hardcoded aliases (never deleted, always available) ────────────────────────

BUILTIN_ALIASES = {
    "fearx":             "bnk fearx",
    "tyloo":             "tyloo",
    "navi":              "natus vincere",
    "na'vi":             "natus vincere",
    "spirit":            "team spirit",
    "parivision":        "PVISION",
    "dortmund":          "Borussia Dortmund",
    "mouz":              "MOUZ",
    "spurs":             "San Antonio Spurs",
    "rockets":           "Houston Rockets",
    "st. louis city":    "St. Louis City SC",
    "chivas":            "Chivas Guadalajara",
    "psg":               "Paris Saint-Germain",
    "paris st. germain": "Paris Saint-Germain",
    "betboom":           "BB Team",
    "betboom team":      "BB Team",
    "rekonix":           "REKONIX",
    "wonju dongbu":      "Wonju Dongbu Promy",
    "daegu kogas":       "Daegu Kogas Pegasus",
    "korea gas":         "Daegu Kogas Pegasus",
    "hotspur":           "Tottenham Hotspur",
    "tottenham":         "Tottenham Hotspur",
    "inter milan":       "Internazionale",
    "inter":             "Internazionale",
    "sporting clube de portugal": "Sporting CP",
    "sporting":          "Sporting CP",
    "new york city football club": "New York City",
    "club internacional de futbol miami": "Inter Miami",
    "inter miami":       "Inter Miami",
    "porto":             "Porto",
    "braga":             "Braga",
    "toluca":            "Deportivo Toluca",
    "beijing beikong":   "Beijing Royal Fighters",
    "shang hai sharks":  "Shanghai Sharks",
    "sk":                "SK Gaming",
    "sk gaming":         "SK Gaming",
    "g2":                "G2 Esports",
    "ig":                "Invictus Gaming",
    "we":                "Team WE",
    "omg":               "Oh My God",
    "jdg":               "JD Gaming",
    "jdgaming":          "JD Gaming",
    "lgd":               "LGD Gaming",
    "edg":               "EDward Gaming",
    "tes":               "Top Esports",
    "fpx":               "FunPlus Phoenix",
    "rng":               "Royal Never Give Up",
    "blg":               "Bilibili Gaming",
    "wbg":               "Weibo Gaming",
    "wb":                "Weibo Gaming",
    "lng":               "LNG Esports",
    "ra":                "Rare Atom",
    "al":                "Anyone's Legend",
    "t1":                "T1",
    "c9":                "Cloud9",
    "nrg":               "NRG Esports",
    "eg":                "Evil Geniuses",
    "100t":              "100 Thieves",
    "tl":                "Team Liquid",
    "fnc":               "Fnatic",
    "vit":               "Vitality",
    "mad":               "MAD Lions",
    "msf":               "Misfits Gaming",
    "xl":                "Excel Esports",
    "bds":               "Team BDS",
    "koi":               "Movistar KOI",
}


# ── Name cleaning ──────────────────────────────────────────────────────────────

STRIP_SUFFIXES = re.compile(r'\b(esports?|clan|team)\b', re.IGNORECASE)

_PREFIXES = ['fc ', 'bc ', 'as ', 'ac ', 'cd ', 'rc ', 'sc ', 'fk ', 'sk ', 'nk ']

def clean_name(name: str) -> str:
    """Strip common prefixes (FC, BC, etc.)."""
    name = name.lower().strip()
    for p in _PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
    return name


# ── Alias database ─────────────────────────────────────────────────────────────

class AliasDB:
    """Self-learning alias database.

    Merges builtin + learned aliases. Tracks usage counts.
    Auto-saves to aliases.json on updates.
    """

    def __init__(self, aliases_file: str = None):
        self._file = aliases_file or ALIASES_FILE
        self._aliases = {}      # key(lower) → PS name
        self._usage = {}        # key(lower) → usage count
        self._load()

    def _load(self):
        """Load builtin + learned aliases."""
        self._aliases = dict(BUILTIN_ALIASES)
        try:
            with open(self._file) as f:
                learned = json.load(f)
            for k, v in learned.items():
                if isinstance(v, str):
                    self._aliases[k.lower()] = v
                    self._usage[k.lower()] = 0
                elif isinstance(v, dict):
                    ps_name = v.get("name") or v.get("canonical")
                    if ps_name:
                        self._aliases[k.lower()] = ps_name
                        self._usage[k.lower()] = v.get("usage_count", v.get("count", 0))
        except FileNotFoundError:
            pass
        except Exception as e:
            log_error("aliases", f"Failed to load {self._file}: {e}")

    def get(self, name: str) -> str:
        """Get alias for name, or return original if no alias."""
        return self._aliases.get(name.lower().strip(), name)

    def get_usage(self, name: str) -> int:
        """Get usage count for an alias."""
        return self._usage.get(name.lower().strip(), 0)

    def has_alias(self, name: str) -> bool:
        """Check if name has an alias entry."""
        return name.lower().strip() in self._aliases

    def save(self, etop_name: str, ps_name: str):
        """Save or update an alias. Auto-increments usage count."""
        key = etop_name.lower().strip()
        try:
            try:
                with open(self._file) as f:
                    data = json.load(f)
            except Exception:
                data = {}

            existing = data.get(key)
            if isinstance(existing, dict):
                if existing.get("name") != ps_name:
                    existing["name"] = ps_name
                    existing["usage_count"] = 1
                    log_info(f"[alias updated] {key!r} → {ps_name!r}")
                else:
                    existing["usage_count"] = existing.get("usage_count", 0) + 1
                data[key] = existing
            else:
                is_new = existing != ps_name
                data[key] = {"name": ps_name, "usage_count": 1}
                if is_new:
                    log_info(f"[alias saved] {key!r} → {ps_name!r}")

            with open(self._file, 'w') as f:
                json.dump(data, f, indent=2)

            # Update in-memory
            self._aliases[key] = ps_name
            uc = data[key].get("usage_count", 1) if isinstance(data[key], dict) else 1
            self._usage[key] = uc
        except Exception as e:
            log_error("aliases", f"Failed to save alias: {e}")

    def normalize(self, name: str) -> str:
        """Normalize name: resolve alias → clean_name → strip suffixes."""
        n = self._aliases.get(name.lower().strip(), name)
        n = clean_name(n)
        n = STRIP_SUFFIXES.sub('', n).strip()
        return n

    @property
    def count(self) -> int:
        return len(self._aliases)
