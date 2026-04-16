# ═══════════════════════════════════════════════════════════════════════════════
# API BIBLE — Single Source of Truth
# ═══════════════════════════════════════════════════════════════════════════════
# Source:   Playwright captures + Chrome DevTools + live testing, 2026-04-02
# Rule:     ONE function per endpoint. Function named after endpoint.
# Rule:     Before ANY call → check session alive. Dead session → STOP ALL.
# Rule:     Every future Claude Code session reads this file FIRST.
# ═══════════════════════════════════════════════════════════════════════════════


# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
# PART 1: ETOPFUN
# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
# Base URL: https://www.etopfun.com
# Auth: Steam SSO → JSESSIONID + DJSP_UUID cookies
# Response envelope: { "code": 0, "datas": {...}, "message": "", "errors": null }
# code=0 = success
# APPID: 570 = Dota2/CS2, 730 = CS:GO (legacy)
# Rate limit: NONE observed on any endpoint


# ── ETOP-1: match_list — ALL RUNNING MATCHES ──────────────────────────────────
# THE main listing. Discovery + monitoring + fire zone odds in ONE call.
# Browser polls this repeatedly. No rate limit. rows=50 returns more than UI (10).
#
# GET /api/match/list.do?status=run&game=all&rows=50&page=1&lang=en
#
# Response datas.list[] = parent matches:
#   .id              → parent match ID
#   .bo              → best-of (1/3/5)
#   .league.name     → league string
#   .vs1.name/.odds/.image  → team 1
#   .vs2.name/.odds/.image  → team 2
#   .offerMatch      → HDP offer details
#   .remainTime      → ms until close (goes NEGATIVE after close)
#   .sublist[] = sub-markets:
#     .id            → sub-match ID (used for betting)
#     .type          → 2=GW/HDP, 3=MapGW, 5=OU, 6=Rounds, 8=10K/HDP,
#                      9=Duration, 11=LoL MapGW, 12=TotalRoundsAlt, 13=TK_OU
#     .map           → map number (0=full match)
#     .vs1.odds      → team 1 odds
#     .vs2.odds      → team 2 odds
#     .vs1.support   → team 1 pool share (e.g. 0.292)
#     .vs2.support   → team 2 pool share (e.g. 0.708)
#     .remainTime    → ms until close
#     .canPress      → true = betting open (THE truth gate for firing)
#     .canChange     → true = can switch side after bet
#     .offerScore    → handicap value
#     .offerTeam     → 0=none, 1=vs1 gives, 2=vs2 gives
#     .totalScore    → kills/rounds target (OU/10K)
#     .totalTime     → duration target
#     .cancelCode    → cancel availability
#     .state         → 0 = active
#
# Function: match_list(status="run", game="all") → (parents[], lookup{})
# Returns BOTH: full parents for discovery, flat {mid: {o1,o2,remain,can_press}} for monitoring
# ONE call. ONE parse. TWO outputs. 167ms average.
#
# PROVEN: Returns IDENTICAL odds/remain/support to match_subs and match_show.
# Tested side-by-side, same match, all 3 endpoints — odds match exactly.
# remainTime differs only by real-time clock tick between calls.


# ── ETOP-2: match_subs — REDUNDANT (DO NOT USE IN UNIFIED LOOP) ─────────────
# Returns fresh odds for a specific parent's sub-markets.
# PROVEN REDUNDANT: match_list() returns identical odds/remain/support.
#
# GET /api/match/{parentId}/list.do?id={subMatchId}&lang=en
#
# Response datas:
#   .running[]   → active subs (same fields as match_list sublist)
#   .ended[]     → finished subs
#   .vs1, .vs2   → parent team info (name, tag, image)
#   .curMatch    → current sub ID
#
# Function: match_subs(parent_id, sub_id) → dict
# 70ms average — faster than match_list but only ONE parent.
#
# STATUS: Still in code but should be removed. match_list provides same data
# for ALL matches in one 167ms call. Only edge case: if you need parent team
# names/tags and don't have them from discovery.


# ── ETOP-3: match_show — MATCH DETAIL + USER BETS (CANCEL ONLY) ─────────────
# Full detail page. ONLY needed for cancel flow (pressTotalList).
# NOT needed for odds — match_list has them.
#
# GET /api/match/{subMatchId}/show.do?rel=match_details&loaded=false&data=loading&lang=en
#
# Response datas:
#   .sublist[]           → all sub-markets (same data as match_list)
#   .remainTime          → ms
#   .user:
#     .goldingot         → balance (e.g. 49.87)
#     .limit             → bet limit (e.g. 40)
#     .canceltimes       → cancel count used
#     .canUseSystem      → true
#     .pressTotalList[]  → user's bets on this match:
#       .id              → press ID (needed for cancel/regret)
#       .canRegretFund   → true = can cancel
#     .maxOrderItemVal   → max single item value
#
# Function: match_show(sub_id) → dict
# 56ms average. No caching — always hits server fresh.
# USE ONLY FOR: getting pressTotalList when cancelling a bet.


# ── ETOP-4: match_pool — BET POOL ────────────────────────────────────────────
# All bets placed on a sub-market.
#
# GET /api/match/bc/{subMatchId}/list.do?lang=en
#
# Function: match_pool(sub_id) → list


# ── ETOP-5: press — PLACE BET ────────────────────────────────────────────────
# Place items on a side. SEQUENTIAL ONLY — parallel = "trygain.later".
#
# POST /match/{appid}/v2/press.do
#   {appid} = 570 (Dota2/CS2) — NOT the match ID!
#
# POST fields (multipart form):
#   id   = {subMatchId}
#   ids  = {itemId1},{itemId2},...   (comma-separated bag item IDs)
#   vsid = 1 (team1/left/over) or 2 (team2/right/under)
#   lang = en
#
# Response: code=0 = success
# CRITICAL: 75ms minimum gap between calls. Max 40 items per match.
# Items can be different values ($2, $5, $10, $20, $50, $100, $200).
# Server doesn't check "did you open the match page first" — just needs
# valid session + valid sub_id + valid item IDs + valid vsid.
#
# Function: press(sub_id, item_ids, vsid, appid=570) → (success, message)


# ── ETOP-6: changepress — CHANGE SIDE AFTER BET ──────────────────────────────
# Switch bet to other team. Only if canChange=true.
#
# POST /match/changepress.do   (NO appid in URL)
#
# POST fields: id={subMatchId}, vsid=1|2, lang=en
#
# Function: changepress(sub_id, vsid) → (success, message)


# ── ETOP-7: regret — CANCEL BET ──────────────────────────────────────────────
# Cancel a placed bet. Only if canRegretFund=true.
#
# POST /match/{subMatchId}/regretFund.do
#
# POST fields: pressid={pressId}, lang=en
# Get pressId from: match_show() → user.pressTotalList[].id
#
# Function: regret(sub_id, press_id) → (success, message)


# ── ETOP-8: bag_listbc — BET-COMPATIBLE ITEMS (INVENTORY) ───────────────────
# Items available for betting. 120 per page.
# Browser loads this when you open a match page.
# Bot loads this at PREFIRE (remain 30-300s) to prepare slices.
#
# GET /api/user/bag/{appid}/listbc.do?page=1&rows=120&desc=1&lang=en
#
# Response datas:
#   .counts   → total available items
#   .list[]   → items: .id, .value, .shortName, .status.lock (0=avail), .state (0=avail)
#
# Function: bag_listbc(appid=570, page=1) → (items[], total_count)


# ── ETOP-9: userconn_check — SESSION KEEPALIVE ───────────────────────────────
# Ping to keep etop session alive. Call every 5 min.
#
# POST /api/userconn/check.do
# POST fields: uuid={DJSP_UUID cookie value}, lang=en
#
# Response datas: .status, .success, .userId
#
# Function: userconn_check(uuid) → bool


# ── ETOP-10: user_show — USER PROFILE ────────────────────────────────────────
# GET /api/user/show.do?lang=en
# Response: .goldingot, .nickname, .steamId, .level, .trade_url
# Function: user_show() → dict


# ── ETOP-11: system_get — SYSTEM CONFIG ──────────────────────────────────────
# GET /api/system/get.do?lang=en
# Response: site configuration data (1.1KB)
# Function: system_get() → dict


# ── ETOP NOT USED ─────────────────────────────────────────────────────────────
# /api/match/schedule.do         — upcoming matches
# /api/match/get_mindex.do       — game categories
# /api/match/v2/guess.do         — prediction game
# /api/match/{id}/tuhao.do       — big spender list
# /api/news/list.do              — news
# /api/notice/list.do            — notifications
# /api/rank/win/list.do          — leaderboard
# /api/user/bag/{appid}/list.do  — all items (not just bet-compatible)
# /api/ingotitems/v2/list.do     — gold ingot shop
# /api/user/gifts.do             — gift history
# /api/user/offer/active/list.do — trade offers
# /api/match/list_user_press_history.do — bet history
# /loginsteam.do                 — Steam SSO redirect
# /logout.do                     — logout


# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
# PART 2: PS3838
# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
# Base URL: https://www.ps3838.com
# Auth: pinnacle95 / Abcd1234
# Session: curl_cffi Chrome120 impersonation + _ulp cookie
# All requests: locale=en_US, _={timestamp_ms}, withCredentials=true


# ── PS3838 REQUIRED HEADERS ──────────────────────────────────────────────────
# Every request must include these (extracted from Chrome DevTools capture):
#
# Standard:
#   Accept:              application/json, text/plain, */*
#   Accept-Encoding:     gzip, deflate, br, zstd
#   Accept-Language:     en-GB,en-US;q=0.9,en;q=0.8
#   User-Agent:          Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...Chrome/146.0.0.0
#   Sec-Ch-Ua:           "Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"
#   Sec-Ch-Ua-Mobile:    ?0
#   Sec-Ch-Ua-Platform:  "macOS"
#   Sec-Fetch-Dest:      empty
#   Sec-Fetch-Mode:      cors
#   Sec-Fetch-Site:      same-origin
#   Referer:             https://www.ps3838.com/en/sports/soccer
#   Origin:              https://www.ps3838.com  (POST only)
#   Priority:            u=1, i
#
# Custom (derived from cookies — CRITICAL for all-odds-selections):
#   X-Browser-Session-Id:  = BrowserSessionId cookie value
#   X-Custid:              = custid cookie value
#   X-Lcu:                 = lcu cookie value
#   X-Slid:                = SLID cookie value
#   X-U:                   = u cookie value
#   X-App-Data:            = all cookies serialized (see format below)
#   X-Requested-With:      XMLHttpRequest  (POST requests only)
#
# X-App-Data format:
#   dpJCA={dpJCA};pctag={pctag};directusToken={directusToken};
#   BrowserSessionId={BrowserSessionId};PCTR={PCTR};
#   _og={_og};_ulp={_ulp};custid={custid};
#   _userDefaultView=COMPACT;__prefs={__prefs};lang=en_US
#
# WHY THIS MATTERS:
#   Bot got 405 on all-odds-selections because cookies were only in Cookie
#   header, not as X-* headers. Server validates BOTH.


# ── PS-1: authenticate — LOGIN ────────────────────────────────────────────────
# POST /member-auth/v2/authenticate
# POST body (form): loginId=pinnacle95&password=Abcd1234&captcha=&captchaToken=
# Response: { code, tokens }
# Sets _ulp cookie. ONE TIME ONLY. Never re-login while bot runs.
# Function: authenticate(user, password) → tokens


# ── PS-2: wstoken — GET WEBSOCKET TOKEN ───────────────────────────────────────
# GET /member-auth/v2/wstoken
# Response: { token: "AAAAAARxCr0..." }
# Token expires ~65min (403 is NORMAL — not session death)
# Function: wstoken() → token_string


# ── PS-3: keep_alive — SESSION KEEPALIVE ──────────────────────────────────────
# GET /member-auth/v2/keep-alive
# Response: empty {}
# Call every 10s. CRITICAL for session immortality.
# Function: keep_alive() → None


# ── PS-4: account_balance — BALANCE + KEEPALIVE ──────────────────────────────
# POST /member-service/v2/account-balance
# Content-Type: application/x-www-form-urlencoded (Content-Length: 0)
# Response: { totalMessage, success, currency, currencyUnit }
# Call every 10s alongside keep_alive. Browser hits this 22x in 2 min.
# Function: account_balance() → dict


# ── PS-5: system_status — SYSTEM STATUS ───────────────────────────────────────
# GET /member-service/v2/system/status
# Response: system flags
# Browser hits this 28x (MOST polled endpoint)
# Function: system_status() → dict


# ── PS-6: compact_events — ALL EVENTS + ODDS (REST FALLBACK) ─────────────────
# REST endpoint for all events in a sport. USE ONLY when WS has no data.
# WS is the primary data source. This is backup for team search only.
#
# GET /sports-service/sv/compact/events
#
# Required params:
#   sp   = 29 (soccer) | 4 (basketball) | 12 (esports)
#   mk   = 1 (main) | 3 (maps, esports)
#
# Browser params (full set):
#   btg=1, cl=100, g=QQ==, l=100, more=false, o=1, ot=1, pa=0,
#   pimo=0,1,8,39,2,3,6,7,4,5, pn=-1, pv=1, sp=29, tm=0, v=0
#
# Bot minimal params: btg=1, sp=29, mk=1, pimo=0,1,2
# KNOWN GAP: Browser sends 10 pimo types, bot sends 3 — missing data possible.
#
# Response: { u, l, n, e, e1, pt, ps, d, hle, ce, be, ces }
#   l = live events: [[sp, ?, [leagues...]]]
#   n = non-live events: same structure
#
# Rate limit: 429 after ~10 calls in 30s. IP-based, recovers after ~60s.
# Dead session: returns 113 bytes nulls.
# me= param DOES NOT WORK (proven session 22).
#
# CURL_CFFI NOTE: use urlencode for params, resp.status_code, resp.json() sync.
# Do NOT use aiohttp patterns (async context manager, await resp.json()).
#
# Function: compact_events(sp, mk) → raw_response


# ── PS-7: all_odds_selections — ODDS TRUTH SOURCE ────────────────────────────
# THE truth source for PS odds. Simulates clicking an odds button in browser.
# Returns current real price. GHOST ODDS PROTECTION: UNAVAILABLE if line dropped.
#
# POST /member-betslip/v2/all-odds-selections
#
# ┌─────────────────────────────────────────────────────────────────────────────
# │ REQUIRED HEADERS (missing any = 400/405):
# │   Content-Type: application/json
# │   v-hucode: {from localStorage — STATIC per browser, grab once via Playwright}
# │   Cookie: full cookie string
# │   (X-* headers from build_headers are also sent but v-hucode is the key one)
# └─────────────────────────────────────────────────────────────────────────────
#
# POST body:
# {
#   "oddsSelections": [{
#     "oddsFormat": 1,
#     "oddsId": "{eid}|{period}|{mk}|{side}|0|{line}",
#     "oddsSelectionsType": "NORMAL",
#     "selectionId": "{line_id}|{eid}|{period}|{mk}|{side}|0|{line}|{side}"
#   }]
# }
#
# ┌─────────────────────────────────────────────────────────────────────────────
# │ ID FORMAT:
# │   eid      = PS event ID (e.g. 1627278034)
# │   period   = map/period number (0=full match, 1=map1, 2=map2, 3=map3)
# │   mk       = 1 for ML, 2 for HDP/OU
# │   side     = 0=home/over, 1=away/under
# │   line     = 0 (ML) or handicap/total value (e.g. -1.5, 2.5)
# │   line_id  = from WS data (see LINE_ID POSITIONS below)
# └─────────────────────────────────────────────────────────────────────────────
#
# ┌─────────────────────────────────────────────────────────────────────────────
# │ RESPONSE: Array of selection results. Each item:
# │
# │ status="OK"
# │   Line exists. Odds are real. SAFE TO FIRE.
# │   .odds         → current decimal odds (e.g. "1.434")
# │   .maxStake     → max bet in EUR
# │   .homeTeam/.awayTeam → team names
# │   .handicap     → line value (HDP/OU)
# │
# │ status="ODDS_CHANGE"
# │   Line exists but odds moved since our line_id snapshot.
# │   Still returns current .odds — RECOMPUTE EV, fire if still positive.
# │
# │ status="UNAVAILABLE"
# │   Line DROPPED. Ghost odds. DO NOT FIRE. .odds=null, .maxStake=null
# │
# │ HTTP 400 → invalid body / missing v-hucode
# │ HTTP 429 → rate limited (see budget below)
# └─────────────────────────────────────────────────────────────────────────────
#
# ┌─────────────────────────────────────────────────────────────────────────────
# │ BATCH: Multiple selections in ONE call (TESTED EXTENSIVELY)
# │
# │ Max selections per call: 10
# │   11+ → HTTP 200 but error: EXCEED_LIMIT_OF_MAX_STRAIGHT_SELECTIONS
# │
# │ What works in one batch:
# │   ✅ ML home + away (same eid, side 0 vs 1)
# │   ✅ HDP home + away (same eid, same line, side 0 vs 1)
# │   ✅ OU over + under (same eid, same total, side 0 vs 1)
# │   ✅ Mixed ML(2) + HDP(2) + OU(2) = 6 selections in 1 call
# │   ✅ 10 different eids in 1 call
# │
# │ Each response item is independent — one UNAVAILABLE doesn't affect others.
# │ Batch counts as 1 call for rate limiting (not per-selection).
# │
# │ Production: 12 markets × 2 sides = 24 selections = 3 calls (10+10+4)
# └─────────────────────────────────────────────────────────────────────────────
#
# ┌─────────────────────────────────────────────────────────────────────────────
# │ RATE LIMITS (TESTED — Sessions 23+24, multiple test suites)
# │
# │ MODEL: Sliding window ~20-25 calls per ~15 seconds
# │
# │ Sequential tests:
# │   2s gap (30/min):   30/30 OK ✅
# │   1s gap (60/min):   60/60 OK ✅
# │   0.5s gap (120/min): 120/120 OK ✅
# │   Burst 15 (0s gap): 15/15 OK ✅
# │   Burst 25 (0s gap): 25/25 OK ✅
# │   Burst 50 (0s gap): 429 at call #38 ⚠️
# │
# │ Parallel tests (all different markets, unique eids):
# │   3 concurrent:  3/3 OK ✅
# │   5 concurrent:  5/5 OK ✅
# │   10 concurrent: 10/10 OK ✅
# │   20 concurrent: 20/20 OK ✅ (uses full window budget)
# │   25 concurrent: 25/25 OK ✅ (after 30s cooldown)
# │   30 concurrent: 10/30 OK ⚠️ (window partially consumed)
# │   50 concurrent: 20/50 OK ⚠️
# │
# │ Fire zone simulation (20 parallel × 5 rounds, 3s gap):
# │   Round 1: 20/20 OK
# │   Rounds 2-5: ALL 429 (window spent, 3s not enough to recover)
# │
# │ Sustained high (10 parallel every 2s for 30s):
# │   First 429 at 7.4s (~30-40 total calls into window)
# │
# │ Recovery: ~30s full cooldown after hitting 429
# │ 429 response: 25-95ms (CDN edge reject, instant)
# │ OK response: 250-650ms (actual server processing)
# │
# │ SAFE OPERATING LIMIT:
# │   Max ~20 calls per 15s window
# │   Bot use: 3-7 calls/cycle, 3s apart = ~6-14 calls/15s → SAFE
# │   With batching: 12 markets = 3 calls per cycle → very safe
# └─────────────────────────────────────────────────────────────────────────────
#
# v-hucode:
#   Value: 32-char hex (e.g. 950f80013a300a24c8032e374a27995f)
#   Source: localStorage['v-hucode'] in browser
#   Lifetime: STATIC per browser profile. Same after logout+re-login.
#   Regenerated if browser profile wiped.
#   Generated by Cloudflare bot management JS (dp-iframe challenge).
#   Grab once: page.evaluate("localStorage.getItem('v-hucode')")
#   Store in cookie.json. Reuse forever unless profile wiped.
#   Missing/wrong → HTTP 400 on this endpoint.
#
# CURL_CFFI NOTE: use resp.status_code, resp.json() sync, no async context manager.
# Do NOT use aiohttp patterns. auth._session is curl_cffi, not aiohttp.
#
# Function: all_odds_selections(selections) → list[dict] | None
#   selections = [{eid, period, mk, side, line, line_id}, ...]


# ── PS-8: live_events — LIVE EVENT LIST ───────────────────────────────────────
# GET /member-service/v2/live-center/live-events?all=true
# Response: { events: [...] }
# Browser hits 14x. Could be useful for discovery.
# Function: live_events() → list


# ── PS-9: odds_periods — AVAILABLE PERIOD TYPES ──────────────────────────────
# GET /sports-service/sv/odds/periods
# Response: list of period type definitions
# Could explain what pimo values (0,1,8,39,2,3,6,7,4,5) mean.
# Function: odds_periods() → list


# ── PS-WS: WebSocket — LIVE ODDS STREAM (PRIMARY DATA SOURCE) ────────────────
# URL: wss://www.ps3838.com/sports-websocket/ws?token={wstoken}
#
# Token from: wstoken() — expires ~65min, WS stays alive after expiry
# Session must be alive (_ulp valid)
#
# Client → Server:
#   SUBSCRIBE    → subscribe to sport (sp=29, mk=1)
#   UNSUBSCRIBE  → unsubscribe
#   PING         → keepalive
#
# Server → Client:
#   CONNECTED, PONG
#   FULL_ODDS      → complete snapshot (on subscribe/reconnect)
#   UPDATE_ODDS    → incremental update (every 1-5s)
#   FULL_LEFT_MENU, UPDATE_LEFT_MENU, UPDATE_GROUPS_OUTRIGHT
#
# CRITICAL: PONG must have destination="ALL" (not "ODDS")
# CRITICAL: Subscribe with browser params: btg=1, cl=100
#
# Candidate array in UPDATE_ODDS:
#   [period, mk, side, line, decOdds, priceStr,
#    selId_new, selId_old, ??, direction, "O"/"U", ??, eventId]
#
# ┌─────────────────────────────────────────────────────────────────────────────
# │ WS PERIOD DATA — LINE_ID POSITIONS
# │ match[8] = dict of {period_key: [hdp, ou, ml]}
# │
# │ pd[0] = HDP rows (can be multiple alt lines):
# │   [home_line, away_line, line_str, home_odds_str, away_odds_str,
# │    side, active, line_id, ?, maxStake, ?]
# │   line_id at position [7] — 11 items per row
# │
# │ pd[1] = OU rows (can be multiple alt lines):
# │   [total_str, total_num, over_odds_str, under_odds_str,
# │    line_id, ?, maxStake, ?]
# │   line_id at position [4] — 8 items per row
# │
# │ pd[2] = ML row:
# │   [AWAY_odds, HOME_odds, ?, line_id, ...]
# │   ⚠️  CAUTION: index 0 = AWAY, index 1 = HOME (reversed from what you'd expect)
# │   REST parser (line 303-304) had correct order all along.
# │   WS parser (line 224-225) was wrong until S24 fix — swapped indices.
# │   Confirmed by DIAG_ML: Thunder home ml[0]=4.030(away) ml[1]=1.278(home)
# │   line_id at position [3]
# │
# │ HDP/OU can have MULTIPLE rows (alt lines) — each row has its own line_id.
# └─────────────────────────────────────────────────────────────────────────────
#
# ┌─────────────────────────────────────────────────────────────────────────────
# │ WS RESUBSCRIBE (page refresh simulation) — TESTED
# │
# │   5 unsub/resub cycles:   5/5 got FULL_ODDS ✅
# │   15 rapid resubs in 60s: 15/15 got FULL_ODDS ✅
# │   3 parallel WS connections: all 3 receive data ✅
# │   resub esports: 2402 lines refreshed in one call ✅
# │
# │   Safe pattern: resub 1 sport per 20s (4 subs rotating)
# │   Each sport gets fresh FULL_ODDS every ~60s
# │   No rate limit on resubscribe
# └─────────────────────────────────────────────────────────────────────────────
#
# FULL_ODDS arrives at least every 3 minutes (WS rotation).
# 12K+ entries after initial connect.
# FREE and UNLIMITED as long as session alive.


# ── PS NOT USED ───────────────────────────────────────────────────────────────
# /dns-dispatcher/service/dispatch          — CDN routing
# /dp-iframe/ (HEAD)                        — presence heartbeat (19x)
# /member-betslip/v2/correlated-selection   — parlay check
# /member-service/v2/data.nocache-v2        — user preferences
# /member-service/v2/finger-print           — browser fingerprint
# /member-service/v2/my-bets                — bet history
# /member-service/v2/quick-stake/get-info   — quick bet settings
# /member-service/v2/sportsbook-favorite/*  — favorites
# /member-service/v2/tournament/buttons     — tournament nav
# /member-service/v2/announcement/list-limit — announcements
# /sports-service/sv/compact/sports-markets — sport structure
# /sports-service/sv/compact/favorite-league-item — fav leagues
#
# NOTE: /member-service/v2/load-template IS used — see directusToken below.


# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
# PART 3: SESSION RULES
# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀

# RULE 1: Session alive check BEFORE every external call.
#   - PS: last keep_alive succeeded? last account_balance succeeded?
#   - Etop: last userconn_check succeeded?
#   - If dead → STOP ALL CALLS (WS + REST + etop). Log it. Wait for human.
#   - NEVER spam a dead session. That's how accounts get banned.

# RULE 2: _ulp lives forever with activity.
#   - Set ONCE at login. Never changes. Never re-login while bot runs.
#   - keep_alive + account_balance every 10s → server extends session.
#   - wstoken 403 at ~65min is NORMAL. WS stays connected. Session alive.

# RULE 3: WS disconnect → max 3 retries then REST-only.
#   - Retry delays: 12s, 30s, 60s. Never 300 retries.
#   - REST fallback works. Bot can fire without WS (slightly older data).

# RULE 4: NEVER open Chrome on PS3838 while bot running.
#   - MULTIPLE_LOGIN detection → account ban.
#   - Kill bot first: pkill -9 -f main.py

# RULE 5: Etop session keepalive.
#   - POST userconn_check every 5 min with DJSP_UUID.
#   - Without this, JSESSIONID expires overnight.

# RULE 6: curl_cffi Chrome120 impersonation is mandatory for PS.
#   - Raw Python requests → TLS fingerprint mismatch → detected as bot.
#   - curl_cffi impersonates Chrome's exact TLS handshake.
#   - CRITICAL: curl_cffi ≠ aiohttp. Different API:
#     - No async context manager (no "async with session.get(...) as resp:")
#     - resp.status_code (not resp.status)
#     - resp.json() sync (not await resp.json())
#     - timeout=15 (not aiohttp.ClientTimeout(total=15))

# RULE 7: v-hucode is required for all-odds-selections.
#   - Static per browser (same value across logout/re-login).
#   - Stored in localStorage['v-hucode'].
#   - Grab once via Playwright: page.evaluate("localStorage.getItem('v-hucode')")
#   - Store in cookie.json alongside cookies.
#   - If missing/wrong → all-odds-selections returns 400.
#   - If browser profile wiped → new value generated, re-grab via Playwright.

# RULE 8: directusToken from load-template.
#   - Fetch once at startup: GET /member-service/v2/load-template
#   - Extract: response.globalAttribute.directusToken
#   - Used in x-app-data header for some endpoints.
#   - Refresh on bot restart.

# PS keepalive loop (every 10s):
#   1. POST account_balance (with build_headers)
#   2. GET  keep_alive (with build_headers)
#   3. GET  system_status (matches browser behavior)

# Etop keepalive loop (every 300s):
#   1. POST userconn_check(DJSP_UUID)

# Startup sequence:
#   1. Load cookies from cookie.json
#   2. Load v-hucode from cookie.json (or Playwright if missing)
#   3. Fetch directusToken from load-template
#   4. Init curl_cffi session
#   5. Start WS connection
#   6. Start keepalive loops


# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
# PART 4: ARCHITECTURE (proven by testing)
# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀

# TWO-LAYER ODDS MODEL:
#   Layer 1: WS = screening (~75-80% fresh after resub, degrades over time)
#   Layer 2: AOS = truth (100% current price, batched, rate-limit aware)
#   Layer 3: REST compact_events = backup (rate limited, team search only)
#
# FLOW:
#   1. WS periodic resub (1 sport/20s) → bulk odds snapshot
#   2. Screen EV from WS data → find candidates
#   3. ps_age > 300s → AOS ghost check (LINE_GONE if UNAVAILABLE)
#   4. Approaching fire zone → AOS batch verify (both sides for fair odds)
#   5. AOS OK + EV still positive → FIRE
#   6. AOS UNAVAILABLE → ghost odds, skip
#   7. AOS ODDS_CHANGE → recompute EV with new odds, fire if still positive

# ETOP API HIERARCHY:
#   match_list() = primary (all matches, every cycle, 167ms)
#   match_subs() = REDUNDANT (same data, remove from unified loop)
#   match_show() = cancel only (pressTotalList)
#   bag_listbc() = inventory load (PREFIRE only)
#   press.do     = place bet (sequential, 75ms gap)
#   regret.do    = cancel bet (needs pressId from match_show)

# FIRE ZONE DESIGN (agreed, not yet coded):
#   1. match_list() → fresh etop odds ALL markets
#   2. AOS batch → 3 calls for 12 markets (both sides, 10 per call)
#   3. no_vig(side_a, side_b) → fair odds → EV computation
#   4. Sort EV+ descending → fire sequentially (100ms gap)
#   5. Sleep 3s → repeat (post-fire odds shift, recompute, fire again if EV+)


# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
# PART 5: FUNCTION NAME MAPPING (code MUST use these names)
# ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀

# ETOP (7 endpoints, 11 functions including auth/user):
#   match_list()                         → GET  /api/match/list.do
#   match_subs(parent_id, sub_id)        → GET  /api/match/{pid}/list.do  [REDUNDANT]
#   match_show(sub_id)                   → GET  /api/match/{mid}/show.do  [CANCEL ONLY]
#   match_pool(sub_id)                   → GET  /api/match/bc/{mid}/list.do
#   press(sub_id, item_ids, vsid)        → POST /match/{appid}/v2/press.do
#   changepress(sub_id, vsid)            → POST /match/changepress.do
#   regret(sub_id, press_id)             → POST /match/{mid}/regretFund.do
#   bag_listbc(appid, page)              → GET  /api/user/bag/{appid}/listbc.do
#   userconn_check(uuid)                 → POST /api/userconn/check.do
#   user_show()                          → GET  /api/user/show.do
#   system_get()                         → GET  /api/system/get.do

# PS3838 (9 endpoints + WS):
#   authenticate(user, pass)             → POST /member-auth/v2/authenticate
#   wstoken()                            → GET  /member-auth/v2/wstoken
#   keep_alive()                         → GET  /member-auth/v2/keep-alive
#   account_balance()                    → POST /member-service/v2/account-balance
#   system_status()                      → GET  /member-service/v2/system/status
#   compact_events(sp, mk)              → GET  /sports-service/sv/compact/events
#   all_odds_selections(selections)      → POST /member-betslip/v2/all-odds-selections
#   live_events()                        → GET  /member-service/v2/live-center/live-events
#   odds_periods()                       → GET  /sports-service/sv/odds/periods
#   ws_connect(token)                    → WSS  /sports-websocket/ws?token=...
