from http.server import BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor
import requests
import json
import time
from urllib.parse import urlparse, parse_qs

HEADERS = {"User-Agent": "MangoWindHub/1.0 skydiving-plane-tracker"}
FR24_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.flightradar24.com",
    "Referer": "https://www.flightradar24.com/",
    "fr24-device-id": "web-1234567890"
}

LIVE_TIMEOUT  = 5
TRACE_TIMEOUT = 9
MAX_TRAIL_PTS = 1500   # cap for the compact "trail" returned with a tail lookup


# ---------------------------------------------------------------------------
# N-number -> ICAO24 hex (US registrations map to a fixed block, no lookup needed)
# ---------------------------------------------------------------------------
_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # 24 letters, no I or O
_DIGITS  = "0123456789"
_SUFFIX_SIZE  = 1 + len(_CHARSET) * (1 + len(_CHARSET))      # 601
_BUCKET4_SIZE = 1 + len(_CHARSET) + len(_DIGITS)             # 35
_BUCKET3_SIZE = len(_DIGITS) * _BUCKET4_SIZE + _SUFFIX_SIZE  # 951
_BUCKET2_SIZE = len(_DIGITS) * _BUCKET3_SIZE + _SUFFIX_SIZE  # 10111
_BUCKET1_SIZE = len(_DIGITS) * _BUCKET2_SIZE + _SUFFIX_SIZE  # 101711


def _suffix_offset(s):
    if not s:
        return 0
    count = _CHARSET.index(s[0]) * (len(_CHARSET) + 1) + 1
    if len(s) == 2:
        count += _CHARSET.index(s[1]) + 1
    return count


def n_to_icao(tail):
    """Convert a US N-number to its ICAO24 hex (lowercase). Returns None if not convertible."""
    try:
        t = (tail or "").upper().strip()
        if not t.startswith("N") or len(t) < 2 or len(t) > 6:
            return None
        n = t[1:]
        if n[0] not in "123456789":
            return None
        out = 0xA00001 + (int(n[0]) - 1) * _BUCKET1_SIZE
        digit_bucket = {1: _BUCKET2_SIZE, 2: _BUCKET3_SIZE, 3: _BUCKET4_SIZE}
        for i in range(1, len(n)):
            ch = n[i]
            if i == 4:
                # last allowed position: a single letter or a digit
                if ch in _CHARSET:
                    out += 1 + _CHARSET.index(ch)
                elif ch in _DIGITS:
                    out += 1 + len(_CHARSET) + int(ch)
                else:
                    return None
            elif ch in _CHARSET:
                # one or two trailing letters end the number
                if len(n) - i > 2:
                    return None
                out += _suffix_offset(n[i:])
                break
            elif ch in _DIGITS:
                out += _SUFFIX_SIZE + int(ch) * digit_bucket[i]
            else:
                return None
        if out > 0xADF7C7:
            return None
        return format(out, "06x")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Live position lookup (adsb.lol, adsb.fi, then FR24 as a last resort)
# ---------------------------------------------------------------------------
def _live_adsblol(tail):
    r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}", timeout=LIVE_TIMEOUT, headers=HEADERS)
    if r.ok:
        ac = next((a for a in r.json().get("ac", []) if a.get("lat") is not None), None)
        if ac:
            return normalize_ac(ac, tail, "adsb.lol")
    return None


def _live_adsbfi(tail):
    r = requests.get(f"https://opendata.adsb.fi/api/v2/reg/{tail}", timeout=LIVE_TIMEOUT, headers=HEADERS)
    if r.ok:
        data = r.json()
        ac = next((a for a in (data.get("aircraft") or data.get("ac") or []) if a.get("lat") is not None), None)
        if ac:
            return normalize_ac(ac, tail, "adsb.fi")
    return None


def _live_fr24_feed(tail):
    r = requests.get(
        f"https://data-live.flightradar24.com/zones/fcgi/feed.js?reg={tail}&faa=1&satellite=1&mlat=1&flarm=1&adsb=1&gnd=1&air=1&vehicles=1&estimated=1&maxage=14400&gliders=1&stats=1",
        timeout=LIVE_TIMEOUT, headers=FR24_HEADERS)
    if r.ok:
        for key, val in r.json().items():
            if key in ("full_count", "version", "stats"):
                continue
            if not isinstance(val, list) or len(val) < 9:
                continue
            lat, lon = val[1], val[2]
            if lat and lon:
                alt = val[4] or 0
                return {
                    "found": True, "tail": tail, "source": "fr24-live",
                    "icao": (val[0] or "").lower(), "fr24id": str(key),
                    "lat": lat, "lon": lon, "alt": alt,
                    "spd": val[5] or 0, "hdg": val[3] or 0,
                    "vert": 0, "on_ground": alt < 100,
                }
    return None


def _live_fr24_search(tail):
    r = requests.get(
        f"https://www.flightradar24.com/v1/search/web/find?query={tail}&limit=10",
        timeout=LIVE_TIMEOUT, headers=FR24_HEADERS)
    if r.ok:
        for item in r.json().get("results", []):
            if item.get("type") == "live":
                d = item.get("detail", {})
                if d.get("lat") and d.get("lon"):
                    return {
                        "found": True, "tail": tail, "source": "fr24-search",
                        "icao": (d.get("icao", "") or "").lower(),
                        "fr24id": str(d.get("flight", "")),
                        "lat": d["lat"], "lon": d["lon"],
                        "alt": d.get("alt", 0), "spd": d.get("speed", 0),
                        "hdg": d.get("heading", 0), "vert": d.get("vspeed", 0),
                        "on_ground": d.get("on_ground", False),
                    }
    return None


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def fetch_live(tail):
    """Run all live sources in parallel; return the first hit in priority order."""
    fns = [_live_adsblol, _live_adsbfi, _live_fr24_feed, _live_fr24_search]
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        futures = [ex.submit(_safe, fn, tail) for fn in fns]
        results = [f.result() for f in futures]
    for res in results:
        if res:
            return res
    return {"found": False, "tail": tail}


def normalize_ac(ac, tail, source):
    on_ground = ac.get("alt_baro") == "ground" or (
        (ac.get("gs") or 0) < 30 and (ac.get("alt_baro") or 9999) < 500
    )
    alt = ac.get("alt_baro")
    if alt == "ground" or not isinstance(alt, (int, float)):
        alt = 0 if alt == "ground" else (ac.get("alt_geom") or ac.get("altitude") or 0)
    return {
        "found": True, "tail": tail, "source": source,
        "icao": ac.get("hex", "") or ac.get("icao24", ""),
        "fr24id": "",
        "lat": ac.get("lat"), "lon": ac.get("lon"),
        "alt": alt,
        "spd": ac.get("gs") or ac.get("speed") or 0,
        "hdg": ac.get("track") or ac.get("heading") or 0,
        "vert": ac.get("baro_rate") or ac.get("geom_rate") or ac.get("vert_rate") or 0,
        "on_ground": on_ground,
    }


# ---------------------------------------------------------------------------
# Today's trace (UTC day) from adsb.lol's tar1090 data
#   https://adsb.lol/data/traces/{last 2 hex chars}/trace_full_{hex}.json
# Point format: [seconds_offset, lat, lon, alt_ft|"ground", gs, track, flags, vrate, ...]
# ---------------------------------------------------------------------------
def _num(v, default=0.0):
    if v is None or v == "":
        return default
    if v == "ground":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def fetch_trace(icao):
    icao = (icao or "").lower().strip()
    if len(icao) != 6:
        return {"icao": icao, "source": "none", "points": []}
    sub = icao[-2:]
    for name in (f"trace_full_{icao}.json", f"trace_recent_{icao}.json"):
        try:
            r = requests.get(f"https://adsb.lol/data/traces/{sub}/{name}",
                             timeout=TRACE_TIMEOUT, headers=HEADERS)
            if not r.ok:
                continue
            data = r.json()
            ref_ts = data.get("timestamp", 0) or 0
            points = []
            for pt in data.get("trace", []):
                if not isinstance(pt, list) or len(pt) < 3:
                    continue
                lat, lon = pt[1], pt[2]
                if lat is None or lon is None:
                    continue
                points.append({
                    "ts":   ref_ts + pt[0],
                    "lat":  lat,
                    "lon":  lon,
                    "alt":  _num(pt[3] if len(pt) > 3 else None),
                    "spd":  _num(pt[4] if len(pt) > 4 else None),
                    "hdg":  _num(pt[5] if len(pt) > 5 else None),
                    "vert": _num(pt[7] if len(pt) > 7 else None),
                })
            if points:
                return {"icao": icao, "source": "adsb.lol", "reg": data.get("r", ""),
                        "type": data.get("t", ""), "points": points}
        except Exception:
            continue
    return {"icao": icao, "source": "none", "points": []}


FAST_KT       = 45     # ground speed at/above this counts as flying (Caravan stalls ~60 kt)
SLOW_END_S    = 180    # this long below FAST_KT ends a flight (landing + rollout/taxi)
GAP_END_S     = 600    # a silence this long in the data also separates flights
KEEP_SLOW_S   = 60     # keep this much rollout after a landing, drop the rest of the taxi
MIN_FLIGHT_PTS = 8
MIN_FLIGHT_S   = 120   # ignore blips (fast taxi, rejected takeoff)
MIN_ALT_SPAN   = 400   # a real flight climbs/descends at least this many feet
GAP_SOFT_S     = 90    # after a gap this long, a candidate that isn't a real flight is dropped


def _is_flight(pts):
    if len(pts) < MIN_FLIGHT_PTS or pts[-1]["ts"] - pts[0]["ts"] < MIN_FLIGHT_S:
        return False
    alts = [p["alt"] for p in pts]
    return max(alts) - min(alts) >= MIN_ALT_SPAN


def split_flights(points):
    """Split a day's trace into separate flights (takeoff to landing). Returns a list of
    point lists in time order. A flight ends after SLOW_END_S below FAST_KT, or after a
    GAP_END_S silence; the next fast point starts a new one."""
    flights, cur = [], []
    slow_since = None
    prev_ts = None

    def close():
        if _is_flight(cur):
            flights.append(list(cur))

    for p in points:
        ts = p["ts"]
        if prev_ts is not None and cur:
            gap = ts - prev_ts
            if gap > GAP_END_S:
                close(); cur.clear(); slow_since = None
            elif gap > GAP_SOFT_S and not _is_flight(cur):
                cur.clear(); slow_since = None   # fast-taxi blip, not a flight
        prev_ts = ts
        if p["spd"] >= FAST_KT:
            if slow_since is not None and ts - slow_since >= SLOW_END_S and cur:
                close(); cur.clear()
            slow_since = None
            cur.append(p)
        else:
            if slow_since is None:
                slow_since = ts
            if cur and ts - slow_since <= KEEP_SLOW_S:
                cur.append(p)
    close()
    return flights


def _downsample(points, cap):
    if len(points) <= cap:
        return points
    step = len(points) / float(cap)
    out = [points[int(i * step)] for i in range(cap)]
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def fetch_tail(tail, want_trail=False, all_flights=False, meta_only=False):
    """Live position (if airborne). With want_trail=True, also today's trail from adsb.lol:
    only the most recent flight unless all_flights=True.

    The trail is opt-in on purpose: the live-tracking poll hits this every few seconds and
    must stay tiny and fast. Only the hourly poller / "Fetch Plane Data" ask for the trail."""
    icao = n_to_icao(tail)

    if not want_trail:
        live = fetch_live(tail)
        result = dict(live)
        result["tail"] = tail
        hexcode = (live.get("icao") or "").lower() or icao or ""
        result["icao24"] = icao or hexcode
        if hexcode and not result.get("icao"):
            result["icao"] = hexcode
        return result

    if icao:
        # US tail: hex is known up front, so live lookup and trace run in parallel
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_live = ex.submit(fetch_live, tail)
            f_trace = ex.submit(fetch_trace, icao)
            live, trace = f_live.result(), f_trace.result()
    else:
        live = fetch_live(tail)
        icao = (live.get("icao") or "").lower() if live.get("found") else ""
        trace = fetch_trace(icao) if icao else {"icao": icao, "source": "none", "points": []}

    result = dict(live)
    result["tail"] = tail
    if icao:
        result.setdefault("icao", icao)
        if not result.get("icao"):
            result["icao"] = icao
    result["icao24"] = icao or result.get("icao", "")

    pts = trace.get("points", [])
    flights = split_flights(pts)
    if all_flights:
        chosen = pts
        result["trail_scope"] = "full_day"
    else:
        chosen = flights[-1] if flights else []
        result["trail_scope"] = "latest_flight"
    if meta_only:
        # debugging view: which flights were detected, without any points
        def profile(f):
            # altitude every 3 min, so climbs/descents/landings are visible without the points
            out, nxt = [], f[0]["ts"]
            for p in f:
                if p["ts"] >= nxt:
                    out.append([round((p["ts"] - f[0]["ts"]) / 60.0, 1), int(p["alt"]), int(p["spd"])])
                    nxt = p["ts"] + 180
            return out
        result["flights"] = [{"start": f[0]["ts"], "end": f[-1]["ts"], "points": len(f),
                              "max_alt": max(p["alt"] for p in f), "min_alt": min(p["alt"] for p in f),
                              "profile_min_alt_kt": profile(f)} for f in flights]
    else:
        result["trail"] = [{"lat": p["lat"], "lon": p["lon"], "alt": p["alt"], "ts": p["ts"]}
                           for p in _downsample(chosen, MAX_TRAIL_PTS)]
    result["trail_source"] = trace.get("source", "none")
    result["trail_points_total"] = len(pts)
    result["flights_total"] = len(flights)
    if chosen:
        result["flight_start"] = chosen[0]["ts"]
        result["flight_end"] = chosen[-1]["ts"]
        result["max_alt"] = max(p["alt"] for p in chosen)
    if pts:
        result["last_seen"] = pts[-1]["ts"]
    result["timestamp"] = chosen[-1]["ts"] if chosen else (pts[-1]["ts"] if pts else time.time())
    return result


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        tail  = (params.get("tail", [None])[0] or "").upper().strip()
        icao  = (params.get("icao", [None])[0] or "").lower().strip()
        trace = params.get("trace", [None])[0]
        trail_arg   = (params.get("trail",   [""])[0] or "").lower()
        want_trail  = trail_arg in ("1", "true", "yes", "meta")
        all_flights = (params.get("flights", [""])[0] or "").lower() == "all"
        try:
            if icao and trace:
                result = fetch_trace(icao)
            elif tail:
                result = fetch_tail(tail, want_trail=want_trail, all_flights=all_flights, meta_only=(trail_arg == "meta"))
            else:
                result = {"error": "tail or icao required"}
        except Exception as e:
            result = {"error": str(e)}

        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
