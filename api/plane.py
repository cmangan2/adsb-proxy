from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs

HEADERS = {"User-Agent": "MangoWindHub/1.0 skydiving-plane-tracker"}
FR24_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.flightradar24.com",
    "Referer": "https://www.flightradar24.com/"
}

def fetch_live(tail):
    """Try adsb.lol then adsb.fi for live position."""
    # Try adsb.lol
    try:
        r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}", timeout=6, headers=HEADERS)
        if r.ok:
            data = r.json()
            ac = next((a for a in data.get("ac", []) if a.get("lat") is not None), None)
            if ac:
                return normalize_ac(ac, tail, "adsb.lol")
    except: pass

    # Try adsb.fi
    try:
        r = requests.get(f"https://opendata.adsb.fi/api/v2/reg/{tail}", timeout=6, headers=HEADERS)
        if r.ok:
            data = r.json()
            ac = next((a for a in data.get("aircraft", []) if a.get("lat") is not None), None)
            if ac:
                return normalize_ac(ac, tail, "adsb.fi")
    except: pass

    # Try FR24 internal
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
                    on_ground = f.get("on_ground", False) or f.get("gnd", False)
                    return {
                        "found": True, "tail": tail, "source": "fr24",
                        "icao": f.get("hex") or f.get("icao24") or "",
                        "fr24id": str(f.get("id") or f.get("flight_id") or ""),
                        "lat": lat, "lon": lon,
                        "alt": f.get("alt") or f.get("altitude") or 0,
                        "spd": f.get("speed") or f.get("gs") or 0,
                        "hdg": f.get("heading") or f.get("track") or 0,
                        "vert": f.get("vspeed") or f.get("vert") or 0,
                        "on_ground": on_ground,
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

def fetch_trace(icao, fr24id=""):
    """Try adsb.lol trace, then adsb.fi, then FR24 playback."""
    points = []

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
                points.append({
                    "ts": pt[0], "lat": lat, "lon": lon,
                    "alt": pt[3] if len(pt)>3 else 0,
                    "spd": pt[4] if len(pt)>4 else 0,
                    "hdg": pt[5] if len(pt)>5 else 0,
                    "vert": pt[6] if len(pt)>6 else 0,
                })
            if points:
                return {"icao": icao, "source": "adsb.lol", "points": points}
    except: pass

    # Try adsb.fi — they store recent tracks
    try:
        r = requests.get(f"https://opendata.adsb.fi/api/v2/hex/{icao}", timeout=8, headers=HEADERS)
        if r.ok:
            data = r.json()
            ac_list = data.get("aircraft", [])
            if ac_list:
                ac = ac_list[0]
                # adsb.fi doesn't have trace but we can at least get last position
                # Try their track endpoint
                r2 = requests.get(f"https://opendata.adsb.fi/api/v2/trace/{icao}", timeout=8, headers=HEADERS)
                if r2.ok:
                    tdata = r2.json()
                    raw = tdata.get("trace", [])
                    for pt in raw:
                        if not isinstance(pt, list) or len(pt) < 3: continue
                        lat, lon = pt[1], pt[2]
                        if lat is None or lon is None: continue
                        points.append({
                            "ts": pt[0], "lat": lat, "lon": lon,
                            "alt": pt[3] if len(pt)>3 else 0,
                            "spd": pt[4] if len(pt)>4 else 0,
                            "hdg": pt[5] if len(pt)>5 else 0,
                            "vert": pt[6] if len(pt)>6 else 0,
                        })
                    if points:
                        return {"icao": icao, "source": "adsb.fi", "points": points}
    except: pass

    # Try FR24 playback
    if fr24id:
        try:
            r = requests.get(
                f"https://api.flightradar24.com/common/v1/flight/playback.json?flightId={fr24id}&timestamp=0",
                timeout=10, headers=FR24_HEADERS)
            if r.ok:
                data = r.json()
                track = data.get("result", {}).get("response", {}).get("data", {}).get("flight", {}).get("track", [])
                for pt in track:
                    lat = pt.get("latitude") or pt.get("lat")
                    lon = pt.get("longitude") or pt.get("lon")
                    if lat is None or lon is None: continue
                    points.append({
                        "ts": pt.get("timestamp") or pt.get("ts") or 0,
                        "lat": lat, "lon": lon,
                        "alt": pt.get("altitude") or pt.get("alt") or 0,
                        "spd": pt.get("speed") or pt.get("spd") or 0,
                        "hdg": pt.get("heading") or pt.get("hdg") or 0,
                        "vert": pt.get("verticalSpeed") or pt.get("vert") or 0,
                    })
                if points:
                    return {"icao": icao, "source": "fr24", "points": points}
        except: pass

    return {"icao": icao, "source": "none", "points": []}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        tail   = (params.get("tail",   [None])[0] or "").upper().strip()
        icao   = (params.get("icao",   [None])[0] or "").lower().strip()
        trace  = params.get("trace",   [None])[0]
        fr24id = (params.get("fr24id", [None])[0] or "").strip()

        try:
            if icao and trace:
                result = fetch_trace(icao, fr24id)
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
