"""
matching/alias_db.py — Team Alias Database (game_type keyed)

Key = (etop_name_lower, game_type)
  game_type = cs2, dota2, lol, valorant, basketball, soccer, ...

This prevents cross-game contamination:
  ("bayern", "lol") → "FC Bayern Munich Esports"
  ("bayern", "soccer") → "FC Bayern München"
  ("navi", "cs2") → "Natus Vincere"
  ("navi", "dota2") → "Natus Vincere"

Lifecycle:
  seed      → hardcoded, always available
  auto      → learned from high-confidence match (DISABLED)
  approved  → human approved via dashboard
  permanent → 3+ successful fires

S31: Upgraded from sport ("esports") to game_type ("cs2", "lol", etc.)
     Merged seeds from aliases.py BUILTIN_ALIASES + old alias_db seeds.
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional
from core.logger import log_info, log_warn


# ── Normalize caller's game_type to standard key ─────────────────────────
# Callers may pass cat_type ("csgo"), hint ("esports"), or game ("dota").
# This normalizes all variants to our standard keys.
GAME_TYPE_NORMALIZE = {
    # CS2
    'csgo': 'cs2', 'cs2': 'cs2', 'cs': 'cs2',
    # Dota
    'dota2': 'dota2', 'dota': 'dota2',
    # LoL
    'lol': 'lol',
    # Valorant
    'valorant': 'valorant',
    # Basketball
    'basketball': 'basketball', 'sports_basketball': 'basketball',
    # Soccer
    'soccer': 'soccer', 'football': 'soccer',
    'sports_soccer': 'soccer', 'sports_football': 'soccer',
    # Overwatch / others
    'overwatch': 'overwatch',
    'kog': 'kog',
    'r6': 'r6',
    'pubg': 'pubg',
    'starcraft': 'starcraft',
}


def _norm_game(game_type: str) -> str:
    """Normalize game_type to standard key. Unknown → return as-is lowercase."""
    if not game_type:
        return ''
    return GAME_TYPE_NORMALIZE.get(game_type.lower().strip(), game_type.lower().strip())


@dataclass
class AliasEntry:
    """One learned alias."""
    etop_name: str
    ps_name: str
    game_type: str          # cs2, dota2, lol, valorant, basketball, soccer
    source: str             # 'seed', 'auto', 'approved'
    score: float
    status: str = 'auto'    # auto → approved → permanent
    uses: int = 0
    fires: int = 0
    created_at: float = 0
    last_used_at: float = 0


class AliasDB:
    """Persistent alias database keyed by (name, game_type)."""

    def __init__(self, path: str = None):
        if path is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base, 'data', 'aliases_learned.json')
        self._path = path
        self._aliases = {}       # (etop_lower, game_type) → AliasEntry
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
            migrated = 0
            for key_str, entry_dict in data.get('aliases', {}).items():
                parts = key_str.split('|', 1)
                if len(parts) != 2:
                    continue
                etop_lower, raw_type = parts

                # Backward compat: old files have "esports" as sport
                # Load them but mark for migration
                if raw_type == 'esports':
                    # Can't auto-migrate without knowing the game — load as-is
                    # These will be overwritten by seeds on next load_all_seeds()
                    game_type = 'esports_legacy'
                    migrated += 1
                else:
                    game_type = _norm_game(raw_type)

                # Handle old format: "sport" field vs new "game_type" field
                if 'sport' in entry_dict and 'game_type' not in entry_dict:
                    entry_dict['game_type'] = _norm_game(entry_dict.pop('sport'))
                elif 'game_type' not in entry_dict:
                    entry_dict['game_type'] = game_type

                self._aliases[(etop_lower, game_type)] = AliasEntry(**entry_dict)
                count += 1

            log_info(f"[ALIAS_DB] Loaded {count} aliases from disk"
                     f"{f' ({migrated} legacy esports entries)' if migrated else ''}")
        except Exception as e:
            log_warn("alias_db", f"Failed to load: {e}")

    def _save(self):
        try:
            data = {
                'aliases': {},
                'saved_at': time.time(),
            }
            for (etop_lower, game_type), entry in self._aliases.items():
                key_str = f"{etop_lower}|{game_type}"
                data['aliases'][key_str] = asdict(entry)
            tmp = self._path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            log_warn("alias_db", f"Failed to save: {e}")

    # ── Seed loading ─────────────────────────────────────────────────

    def load_seeds(self, seed_dict: dict, game_type: str):
        """Load seed aliases for a specific game_type."""
        gt = _norm_game(game_type)
        added = 0
        for etop_lower, ps_name in seed_dict.items():
            key = (etop_lower.strip().lower(), gt)
            if key not in self._aliases:
                self._aliases[key] = AliasEntry(
                    etop_name=etop_lower, ps_name=ps_name, game_type=gt,
                    source='seed', score=100, status='permanent',
                    created_at=time.time(),
                )
                added += 1
        if added:
            self._save()
            log_info(f"[ALIAS_DB] Seeded {added} {gt} aliases")

    def load_all_seeds(self):
        """Load comprehensive seed aliases organized by game_type.

        Merged from aliases.py BUILTIN_ALIASES + old alias_db seeds.
        Multi-game orgs (NAVI, G2, Liquid, etc.) get separate entries per game.
        """

        # ── CS2 ──────────────────────────────────────────────
        cs2 = {
            'navi': 'Natus Vincere',
            "na'vi": 'Natus Vincere',
            'mouz': 'MOUZ',
            'faze': 'FaZe Clan',
            'nip': 'Ninjas In Pyjamas',
            'col': 'Complexity Gaming',
            'vp': 'Virtus.pro',
            'parivision': 'PVISION',
            'rekonix': 'REKONIX',
            'spirit': 'Team Spirit',
            'g2': 'G2 Esports',
            'c9': 'Cloud9',
            'tl': 'Team Liquid',
            'eg': 'Evil Geniuses',
            'fnc': 'Fnatic',
            'vit': 'Vitality',
            'bds': 'Team BDS',
            'sk': 'SK Gaming',
            'sk gaming': 'SK Gaming',
        }

        # ── Dota 2 ──────────────────────────────────────────
        dota2 = {
            'navi': 'Natus Vincere',
            "na'vi": 'Natus Vincere',
            'spirit': 'Team Spirit',
            'betboom': 'BB Team',
            'betboom team': 'BB Team',
            'tl': 'Team Liquid',
            'eg': 'Evil Geniuses',
            'vp': 'Virtus.pro',
            'lgd': 'PSG.LGD',
            'fpx': 'FunPlus Phoenix',
            'g2': 'G2 Esports',
            'c9': 'Cloud9',
            'yes': 'Yellow Submarine',
        }

        # ── LoL ──────────────────────────────────────────────
        lol = {
            # LCK
            't1': 'T1',
            'fearx': 'BNK FearX',
            'brion esports': 'HANJIN BRION',
            'hanwha life esports': 'Hanwha Life',
            'kwangdong freecs': 'Kwangdong Freecs',
            # LPL
            'ig': 'Invictus Gaming',
            'we': 'Team WE',
            'omg': 'Oh My God',
            'jdg': 'JD Gaming',
            'jdgaming': 'JD Gaming',
            'lgd': 'LGD Gaming',
            'edg': 'EDward Gaming',
            'tes': 'Top Esports',
            'fpx': 'FunPlus Phoenix',
            'rng': 'Royal Never Give Up',
            'blg': 'Bilibili Gaming',
            'wbg': 'Weibo Gaming',
            'wb': 'Weibo Gaming',
            'lng': 'LNG Esports',
            'ra': 'Rare Atom',
            'al': "Anyone's Legend",
            # LEC
            'g2': 'G2 Esports',
            'fnc': 'Fnatic',
            'vit': 'Vitality',
            'mad': 'MAD Lions',
            'msf': 'Misfits Gaming',
            'xl': 'Excel Esports',
            'bds': 'Team BDS',
            'koi': 'Movistar KOI',
            'sk': 'SK Gaming',
            # LCS
            'c9': 'Cloud9',
            'tl': 'Team Liquid',
            'eg': 'Evil Geniuses',
            'nrg': 'NRG Esports',
            '100t': '100 Thieves',
        }

        # ── Valorant ─────────────────────────────────────────
        valorant = {
            'navi': 'Natus Vincere',
            "na'vi": 'Natus Vincere',
            'fnc': 'Fnatic',
            'c9': 'Cloud9',
            'tl': 'Team Liquid',
            'eg': 'Evil Geniuses',
            'g2': 'G2 Esports',
        }

        # ── Basketball ───────────────────────────────────────
        basketball = {
            'spurs': 'San Antonio Spurs',
            'rockets': 'Houston Rockets',
            'beijing beikong': 'Beijing Royal Fighters',
            'shang hai sharks': 'Shanghai Sharks',
            'wonju dongbu': 'Wonju Dongbu Promy',
            'daegu kogas': 'Daegu Kogas Pegasus',
            'korea gas': 'Daegu Kogas Pegasus',
        }

        # ── Soccer ───────────────────────────────────────────
        soccer = {
            # Club names
            'dortmund': 'Borussia Dortmund',
            'tottenham': 'Tottenham Hotspur',
            'hotspur': 'Tottenham Hotspur',
            'inter milan': 'Internazionale',
            'inter': 'Internazionale',
            'psg': 'Paris Saint-Germain',
            'paris st. germain': 'Paris Saint-Germain',
            'sporting': 'Sporting CP',
            'sporting clube de portugal': 'Sporting CP',
            'new york city football club': 'New York City',
            'club internacional de futbol miami': 'Inter Miami',
            'inter miami': 'Inter Miami',
            'porto': 'Porto',
            'braga': 'Braga',
            'toluca': 'Deportivo Toluca',
            'chivas': 'Chivas Guadalajara',
            'st. louis city': 'St. Louis City SC',
            # Country names
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

        self.load_seeds(cs2, 'cs2')
        self.load_seeds(dota2, 'dota2')
        self.load_seeds(lol, 'lol')
        self.load_seeds(valorant, 'valorant')
        self.load_seeds(basketball, 'basketball')
        self.load_seeds(soccer, 'soccer')

    # ── Lookup ───────────────────────────────────────────────────────

    def lookup(self, etop_name: str, game_type: str) -> Optional[AliasEntry]:
        """Look up alias by (name, game_type).

        game_type is normalized: "csgo" → "cs2", "sports_basketball" → "basketball", etc.
        """
        gt = _norm_game(game_type)
        key = (etop_name.strip().lower(), gt)
        entry = self._aliases.get(key)
        if entry:
            entry.uses += 1
            entry.last_used_at = time.time()
            log_info(f"[ALIAS_HIT] '{etop_name}' → '{entry.ps_name}' "
                    f"[{entry.status}] uses={entry.uses} game={gt}")
            if sum(e.uses for e in self._aliases.values()) % 10 == 0:
                self._save()
            return entry
        return None

    def get_history_bonus(self, etop_name: str, game_type: str) -> int:
        gt = _norm_game(game_type)
        key = (etop_name.strip().lower(), gt)
        entry = self._aliases.get(key)
        if not entry:
            return 0
        if entry.status == 'permanent':
            return 10
        if entry.status == 'approved':
            return 5 + min(entry.fires, 5)
        return 0

    # ── Learning (auto DISABLED — causes cascading wrong matches) ────

    def auto_learn(self, etop_name, ps_name, game_type, score):
        return  # DISABLED

    def suggest(self, etop_name, ps_name, game_type, score):
        """Queue a suggestion for dashboard review."""
        gt = _norm_game(game_type)
        key = (etop_name.strip().lower(), gt)
        if key in self._aliases:
            return
        for s in self._suggestions:
            if s.get('etop', '').lower() == key[0] and s.get('game_type') == gt:
                return
        self._suggestions.append({
            'etop': etop_name, 'ps': ps_name,
            'game_type': gt, 'score': score,
        })
        self._save()
        log_info(f"[ALIAS_SUGGEST] '{etop_name}' → '{ps_name}' "
                f"score={score:.0f} game={gt} → PENDING DASHBOARD APPROVAL")

    def approve(self, etop_name: str, game_type: str) -> bool:
        gt = _norm_game(game_type)
        key = (etop_name.strip().lower(), gt)
        for i, s in enumerate(self._suggestions):
            if (s.get('etop', '').lower() == key[0]
                    and s.get('game_type') == gt):
                self._aliases[key] = AliasEntry(
                    etop_name=s['etop'], ps_name=s['ps'],
                    game_type=gt, source='approved',
                    score=s.get('score', 0), status='approved',
                    created_at=time.time(),
                )
                self._suggestions.pop(i)
                self._save()
                log_info(f"[ALIAS_APPROVED] '{s['etop']}' → '{s['ps']}' game={gt}")
                return True
        return False

    def reject(self, etop_name: str, game_type: str) -> bool:
        gt = _norm_game(game_type)
        key = (etop_name.strip().lower(), gt)
        for i, s in enumerate(self._suggestions):
            if (s.get('etop', '').lower() == key[0]
                    and s.get('game_type') == gt):
                self._suggestions.pop(i)
                self._save()
                log_info(f"[ALIAS_REJECTED] '{s['etop']}' → '{s['ps']}' game={gt}")
                return True
        return False

    def record_fire(self, etop_name: str, game_type: str):
        gt = _norm_game(game_type)
        key = (etop_name.strip().lower(), gt)
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

    # ── Dashboard / stats ────────────────────────────────────────────

    def get_suggestions(self) -> list:
        return list(self._suggestions)

    def get_stats(self) -> dict:
        by_game = {}
        by_status = {}
        for (_, gt), entry in self._aliases.items():
            by_game[gt] = by_game.get(gt, 0) + 1
            by_status[entry.status] = by_status.get(entry.status, 0) + 1
        return {
            'total': len(self._aliases),
            'by_game_type': by_game,
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
