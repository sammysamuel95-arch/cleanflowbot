"""
matching/alias_db.py — Self-Learning Alias Database

Stores team name mappings learned from multi-signal evidence matching.
Persists to data/aliases_learned.json (separate from manual aliases.json).

Lifecycle:
  auto     → learned automatically (score ≥85, both teams + opponent matched)
  approved → human approved via dashboard (suggestions with score 65-84)
  permanent → 3+ successful fires, never questioned again

Key = (etop_name_lower, sport) — sport is part of the key because
"T1" in LoL ≠ "T1" in Valorant.
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional
from core.logger import log_info, log_warn


@dataclass
class AliasEntry:
    """One learned alias."""
    etop_name: str
    ps_name: str
    sport: str
    source: str             # 'seed', 'auto', 'approved'
    score: float
    status: str = 'auto'    # auto → approved → permanent
    uses: int = 0
    fires: int = 0
    created_at: float = 0
    last_used_at: float = 0


class AliasDB:
    """Persistent alias database with self-learning lifecycle."""

    def __init__(self, path: str = None):
        if path is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base, 'data', 'aliases_learned.json')
        self._path = path
        self._aliases = {}       # (etop_lower, sport) → AliasEntry
        self._suggestions = []   # pending for dashboard
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            log_info(f"[ALIAS_DB] No file at {self._path} — starting fresh")
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
            count = 0
            for key_str, entry_dict in data.get('aliases', {}).items():
                parts = key_str.split('|', 1)
                if len(parts) != 2:
                    continue
                etop_lower, sport = parts
                self._aliases[(etop_lower, sport)] = AliasEntry(**entry_dict)
                count += 1
            log_info(f"[ALIAS_DB] Loaded {count} aliases from disk")
        except Exception as e:
            log_warn("alias_db", f"Failed to load: {e}")

    def _save(self):
        try:
            data = {
                'aliases': {},
                'suggestions': [
                    {'etop': s.etop_name, 'ps': s.ps_name,
                     'sport': s.sport, 'score': round(s.combined, 1)}
                    for s in self._suggestions
                ],
                'saved_at': time.time(),
            }
            for (etop_lower, sport), entry in self._aliases.items():
                key_str = f"{etop_lower}|{sport}"
                data['aliases'][key_str] = asdict(entry)
            tmp = self._path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            log_warn("alias_db", f"Failed to save: {e}")

    def load_seeds(self, seed_dict: dict, sport: str):
        added = 0
        for etop_lower, ps_name in seed_dict.items():
            key = (etop_lower.strip().lower(), sport)
            if key not in self._aliases:
                self._aliases[key] = AliasEntry(
                    etop_name=etop_lower, ps_name=ps_name, sport=sport,
                    source='seed', score=100, status='permanent',
                    created_at=time.time(),
                )
                added += 1
        if added:
            self._save()
            log_info(f"[ALIAS_DB] Seeded {added} {sport} aliases")

    def load_all_seeds(self):
        soccer = {
            'democratic rep congo': 'DR Congo',
            'dem. rep. congo': 'DR Congo',
            'republic of korea': 'South Korea',
            'korea republic': 'South Korea',
            'chinese taipei': 'Taiwan',
            "cote d'ivoire": 'Ivory Coast',
            'bosnia and herzegovina': 'Bosnia & Herzegovina',
            'trinidad and tobago': 'Trinidad & Tobago',
            'eswatini': 'Swaziland',
            'cabo verde': 'Cape Verde',
            'turkiye': 'Turkey',
        }
        esports = {
            'brion esports': 'HANJIN BRION',
            'hanwha life esports': 'Hanwha Life',
            'kwangdong freecs': 'Kwangdong Freecs',
            'faze': 'FaZe Clan',
            'navi': 'Natus Vincere',
            'betboom team': 'BetBoom Team',
            'nip': 'Ninjas In Pyjamas',
            'eg': 'Evil Geniuses',
            'col': 'Complexity Gaming',
            'c9': 'Cloud9',
            'tl': 'Team Liquid',
            'vp': 'Virtus.pro',
            'g2': 'G2 Esports',
            'fpx': 'FunPlus Phoenix',
            'jdg': 'JD Gaming',
            'tes': 'Top Esports',
            'edg': 'EDward Gaming',
            'lgd': 'PSG.LGD',
            'ig': 'Invictus Gaming',
            'rng': 'Royal Never Give Up',
        }
        self.load_seeds(soccer, 'soccer')
        self.load_seeds(esports, 'esports')

    def lookup(self, etop_name: str, sport: str) -> Optional[AliasEntry]:
        key = (etop_name.strip().lower(), sport)
        entry = self._aliases.get(key)
        if entry:
            entry.uses += 1
            entry.last_used_at = time.time()
            log_info(f"[ALIAS_HIT] '{etop_name}' → '{entry.ps_name}' "
                    f"[{entry.status}] uses={entry.uses} sport={sport}")
            if sum(e.uses for e in self._aliases.values()) % 10 == 0:
                self._save()
            return entry
        return None

    def get_history_bonus(self, etop_name: str, sport: str) -> int:
        key = (etop_name.strip().lower(), sport)
        entry = self._aliases.get(key)
        if not entry:
            return 0
        if entry.status == 'permanent':
            return 10
        if entry.status == 'approved':
            return 5 + min(entry.fires, 5)
        return 0

    def auto_learn(self, signals):
        return  # DISABLED — causes cascading wrong matches
        key = (signals.etop_name.strip().lower(), signals.sport)
        if key in self._aliases:
            return
        if signals.etop_name.strip().lower() == signals.ps_name.strip().lower():
            return
        self._aliases[key] = AliasEntry(
            etop_name=signals.etop_name, ps_name=signals.ps_name,
            sport=signals.sport, source='auto',
            score=signals.combined, status='auto',
            created_at=time.time(),
        )
        self._save()
        log_info(f"[ALIAS_LEARNED] '{signals.etop_name}' → '{signals.ps_name}' "
                f"score={signals.combined:.0f} sport={signals.sport}")

    def suggest(self, signals):
        key = (signals.etop_name.strip().lower(), signals.sport)
        if key in self._aliases:
            return
        for s in self._suggestions:
            if (s.etop_name.strip().lower() == key[0]
                    and s.sport == signals.sport):
                return
        self._suggestions.append(signals)
        self._save()
        log_info(f"[ALIAS_SUGGEST] '{signals.etop_name}' → '{signals.ps_name}' "
                f"score={signals.combined:.0f} sport={signals.sport} "
                f"→ PENDING DASHBOARD APPROVAL")

    def approve(self, etop_name: str, sport: str) -> bool:
        key = (etop_name.strip().lower(), sport)
        for i, s in enumerate(self._suggestions):
            if (s.etop_name.strip().lower() == key[0]
                    and s.sport == sport):
                self._aliases[key] = AliasEntry(
                    etop_name=s.etop_name, ps_name=s.ps_name,
                    sport=sport, source='approved',
                    score=s.combined, status='approved',
                    created_at=time.time(),
                )
                self._suggestions.pop(i)
                self._save()
                log_info(f"[ALIAS_APPROVED] '{s.etop_name}' → '{s.ps_name}' sport={sport}")
                return True
        return False

    def reject(self, etop_name: str, sport: str) -> bool:
        key = (etop_name.strip().lower(), sport)
        for i, s in enumerate(self._suggestions):
            if (s.etop_name.strip().lower() == key[0]
                    and s.sport == sport):
                self._suggestions.pop(i)
                self._save()
                log_info(f"[ALIAS_REJECTED] '{s.etop_name}' → '{s.ps_name}' sport={sport}")
                return True
        return False

    def record_fire(self, etop_name: str, sport: str):
        key = (etop_name.strip().lower(), sport)
        entry = self._aliases.get(key)
        if not entry:
            return
        entry.fires += 1
        if entry.fires >= 3 and entry.status != 'permanent':
            old = entry.status
            entry.status = 'permanent'
            self._save()
            log_info(f"[ALIAS_PROMOTED] '{etop_name}' → '{entry.ps_name}' "
                    f"{old} → permanent (fires={entry.fires})")
        elif entry.fires % 5 == 0:
            self._save()

    def get_suggestions(self) -> list:
        return [
            {'etop_name': s.etop_name, 'ps_name': s.ps_name,
             'sport': s.sport, 'score': round(s.combined, 1),
             'name_score': s.name_score, 'opponent_score': s.opponent_score}
            for s in self._suggestions
        ]

    def get_stats(self) -> dict:
        by_status = {}
        for entry in self._aliases.values():
            by_status[entry.status] = by_status.get(entry.status, 0) + 1
        return {
            'total': len(self._aliases),
            'by_status': by_status,
            'pending_suggestions': len(self._suggestions),
            'total_fires': sum(e.fires for e in self._aliases.values()),
        }

    def prune_stale(self, max_age_days: int = 30):
        cutoff = time.time() - (max_age_days * 86400)
        pruned = []
        for key, entry in list(self._aliases.items()):
            if (entry.source == 'auto' and entry.fires == 0
                    and entry.created_at < cutoff and entry.created_at > 0):
                pruned.append(key)
                del self._aliases[key]
        if pruned:
            self._save()
            log_info(f"[ALIAS_PRUNED] Removed {len(pruned)} stale auto-aliases")
