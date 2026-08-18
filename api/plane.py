from http.server import BaseHTTPRequestHandler
import requests
import json
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

def fetch_live(tail):
    # 1. Try adsb.lol
    try:
        r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}", timeout=6, headers=HEADERS)
        if r.ok:
            data = r.json()
            ac = next((a for a in data.get("ac", []) if a.get("lat") is not None), None)
            if ac:
                return normalize_ac(ac, tail, "adsb.lol")
    except: pass

    # 2. Try adsb.fi
    try:
        r = requests.get(f"https://opendata.adsb.fi/api/v2/reg/{tail}", timeout=6, headers=HEADERS)
        if r.ok:
            data = r.json()
            ac = next((a for a in data.get("aircraft", []) if a.get("lat") is not None), None)
            if ac:
                return normalize_ac(ac, tail, "adsb.fi")
    except: pass

    # 3. Try FR24 live feed API (newer endpoint)
    try:
        r = requests.get(
            f"https://data-live.flightradar24.com/zones/fcgi/feed.js?reg={tail}&faa=1&satellite=1&mlat=1&flarm=1&adsb=1&gnd=1&air=1&vehicles=1&estimated=1&maxage=14400&gliders=1&stats=1",
            timeout=8, headers=FR24_HEADERS)
        if r.ok:
            data = r.json()
            # FR24 feed returns dict of flight_id: [icao, lat, lon, hdg, alt, spd, ...]
            for key, val in data.items():
                if key in ("full_count", "version", "stats"): continue
                if not isinstance(val, list) or len(val) < 9: continue
                lat, lon = val[1], val[2]
                if lat and lon:
                    icao = val[0] if val[0] else ""
                    hdg  = val[3] or 0
                    alt  = val[4] or 0
                    spd  = val[5] or 0
                    return {
                        "found": True, "tail": tail, "source": "fr24-live",
                        "icao": icao.lower(),
                        "fr24id": str(key),
                        "lat": lat, "lon": lon,
                        "alt": alt, "spd": spd, "hdg": hdg,
                        "vert": 0, "on_ground": alt < 100,
                    }
    except: pass

    # 4. Try FR24 search API
    try:
        r = requests.get(
            f"https://www.flightradar24.com/v1/search/web/find?query={tail}&limit=10",
            timeout=6, headers=FR24_HEADERS)
        if r.ok:
            data = r.json()
            results = data.get("results", [])
            for item in results:
                if item.get("type") == "live":
                    detail = item.get("detail", {})
                    lat = detail.get("lat")
                    lon = detail.get("lon")
                    if lat and lon:
                        return {
                            "found": True, "tail": tail, "source": "fr24-search",
                            "icao": detail.get("icao", "").lower(),
                            "fr24id": str(detail.get("flight", "")),
                            "lat": lat, "lon": lon,
                            "alt": detail.get("alt", 0),
                            "spd": detail.get("speed", 0),
                            "hdg": detail.get("heading", 0),
                            "vert": detail.get("vspeed", 0),
                            "on_ground": detail.get("on_ground", False),
                        }
    except: pass

    # 5. Try FR24 classic API
    try:
        r = requests.get(
            f"https://api.flightradar24.com/common/v1/flight/list.json?fetchBy=reg&query={tail}&limit=1",
            timeout=6, headers=FR24_HEADERS)
        if r.ok:
            data = r.json()
            flights = data.get("result", {}).get("response", {}).get("data", [])
            if flights:
                f = flights[0]
                lat = f.get("lat") or f.get("latitude")
                lon = f.get("lon") or f.get("longitude")
                if lat and lon:
                    return {
                        "found": True, "tail": tail, "source": "fr24-classic",
                        "icao": f.get("hex") or f.get("icao24") or "",
                        "fr24id": str(f.get("id") or f.get("flight_id") or ""),
                        "lat": lat, "lon": lon,
                        "alt": f.get("alt") or f.get("altitude") or 0,
                        "spd": f.get("speed") or f.get("gs") or 0,
                        "hdg": f.get("heading") or f.get("track") or 0,
                        "vert": f.get("vspeed") or f.get("vert") or 0,
                        "on_ground": f.get("on_ground", False) or f.get("gnd", False),
                    }
    except: pass

    return {"found": False, "tail": tail}

def normalize_ac(ac, tail, source):
    on_ground = ac.get("alt_baro") == "ground" or (
        (ac.get("gs") or 0) < 30 and (ac.get("alt_baro") or 9999) < 500
    )
    return {
        "found": True, "tail": tail, "source": source,
        "icao": ac.get("hex", "") or ac.get("icao24", ""),
        "fr24id": "",
        "lat": ac.get("lat"), "lon": ac.get("lon"),
        "alt": 0 if ac.get("alt_baro") == "ground" else (ac.get("alt_baro") or ac.get("alt_geom") or ac.get("altitude") or 0),
        "spd": ac.get("gs") or ac.get("speed") or 0,
        "hdg": ac.get("track") or ac.get("heading") or 0,
        "vert": ac.get("baro_rate") or ac.get("geom_rate") or ac.get("vert_rate") or 0,
        "on_ground": on_ground,
    }

def fetch_trace(icao, fr24id="", source=None, date=None):
    points = []

    # Try globe_history for full day trace
    if source == "history" and date:
        try:
            sub = icao[-2:]
            url = f"https://globe_history.adsb.lol/{date.replace('-','/')}/traces/{sub}/trace_full_{icao}.json"
            r = requests.get(url, timeout=15, headers=HEADERS)
            if r.ok:
                data = r.json()
                raw = data.get("trace", [])
                # Reference timestamp is in the JSON
                ref_ts = data.get("timestamp", 0)
                for pt in raw:
                    if not isinstance(pt, list) or len(pt) < 3: continue
                    # Format: [offset_seconds, lat, lon, alt, flags, spd, hdg, vert, ...]
                    ts_offset = pt[0]
                    lat, lon = pt[1], pt[2]
                    if lat is None or lon is None: continue
                    alt  = pt[3] if len(pt)>3 and pt[3] not in (None, "") else 0
                    spd  = pt[6] if len(pt)>6 and pt[6] not in (None, "") else 0
                    hdg  = pt[5] if len(pt)>5 and pt[5] not in (None, "") else 0
                    vert = pt[7] if len(pt)>7 and pt[7] not in (None, "") else 0
                    try:
                        alt  = float(alt)  if alt  else 0
                        spd  = float(spd)  if spd  else 0
                        hdg  = float(hdg)  if hdg  else 0
                        vert = float(vert) if vert else 0
                    except: alt=spd=hdg=vert=0
                    points.append({
                        "ts": ref_ts + ts_offset,
                        "lat": lat, "lon": lon,
                        "alt": alt, "spd": spd, "hdg": hdg, "vert": vert
                    })
                if points:
                    return {"icao": icao, "source": "globe_history", "points": points}
        except Exception as e:
            pass  # fall through to live trace

    # Try adsb.lol trace
    try:
        r = requests.get(f"https://api.adsb.lol/v2/icao/{icao}/trace", timeout=10, headers=HEADERS)
        if r.ok:
            data = r.json()
            raw = data.get("trace", [])
            for pt in raw:
                if not isinstance(pt, list) or len(pt) < 3: continue
                lat, lon = pt[1], pt[2]
                if lat is None or lon is None: continue
                points.append({"ts": pt[0], "lat": lat, "lon": lon,
                    "alt": pt[3] if len(pt)>3 else 0,
                    "spd": pt[4] if len(pt)>4 else 0,
                    "hdg": pt[5] if len(pt)>5 else 0,
                    "vert": pt[6] if len(pt)>6 else 0})
            if points:
                return {"icao": icao, "source": "adsb.lol", "points": points}
    except: pass

    # Try adsb.fi trace
    try:
        r2 = requests.get(f"https://opendata.adsb.fi/api/v2/trace/{icao}", timeout=8, headers=HEADERS)
        if r2.ok:
            raw = r2.json().get("trace", [])
            for pt in raw:
                if not isinstance(pt, list) or len(pt) < 3: continue
                lat, lon = pt[1], pt[2]
                if lat is None or lon is None: continue
                points.append({"ts": pt[0], "lat": lat, "lon": lon,
                    "alt": pt[3] if len(pt)>3 else 0,
                    "spd": pt[4] if len(pt)>4 else 0,
                    "hdg": pt[5] if len(pt)>5 else 0,
                    "vert": pt[6] if len(pt)>6 else 0})
            if points:
                return {"icao": icao, "source": "adsb.fi", "points": points}
    except: pass

    return {"icao": icao, "source": "none", "points": []}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        tail   = (params.get("tail",   [None])[0] or "").upper().strip()
        icao   = (params.get("icao",   [None])[0] or "").lower().strip()
        trace  = params.get("trace",   [None])[0]
        fr24id = (params.get("fr24id", [None])[0] or "").strip()

        source = (params.get("source", [None])[0] or "").strip()
        date   = (params.get("date",   [None])[0] or "").strip()
        try:
            if icao and trace:
                result = fetch_trace(icao, fr24id, source=source, date=date)
            elif tail:
                result = fetch_live(tail)
            else:
                result = {"error": "tail or icao required"}
        except Exception as e:
            result = {"error": str(e)}

        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args): pass
