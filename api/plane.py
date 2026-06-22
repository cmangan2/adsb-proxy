from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs
import time

ADSB_HEADERS = {"User-Agent": "MangoWindHub/1.0 skydiving-plane-tracker"}
FR24_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://www.flightradar24.com",
    "Referer": "https://www.flightradar24.com/"
}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        tail  = (params.get("tail",  [None])[0] or "").upper().strip()
        icao  = (params.get("icao",  [None])[0] or "").lower().strip()
        trace = params.get("trace",  [None])[0]
        fr24id = (params.get("fr24id", [None])[0] or "").strip()

        resp_headers = {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        }

        try:
            if icao and trace:
                body = self._get_trace(icao, fr24id)
            elif tail:
                body = self._get_plane(tail)
            else:
                body = json.dumps({"error": "tail or icao required"}).encode()
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()

        self.send_response(200)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _get_plane(self, tail):
        # Try adsb.lol first
        try:
            r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}",
                             timeout=8, headers=ADSB_HEADERS)
            if r.ok:
                data = r.json()
                ac = next((a for a in data.get("ac", []) if a.get("lat") is not None), None)
                if ac:
                    on_ground = ac.get("alt_baro") == "ground" or \
                                (ac.get("gs") or 0) < 30 and (ac.get("alt_baro") or 9999) < 500
                    return json.dumps({
                        "found": True, "tail": tail, "source": "adsb.lol",
                        "icao": ac.get("hex", ""),
                        "lat":  ac.get("lat"), "lon": ac.get("lon"),
                        "alt":  0 if ac.get("alt_baro") == "ground" else (ac.get("alt_baro") or ac.get("alt_geom") or 0),
                        "spd":  ac.get("gs") or 0,
                        "hdg":  ac.get("track") or 0,
                        "vert": ac.get("baro_rate") or ac.get("geom_rate") or 0,
                        "on_ground": on_ground,
                        "fr24id": ""
                    }).encode()
        except Exception as e:
            print(f"adsb.lol error: {e}")

        # Fallback: FR24 internal API
        try:
            r = requests.get(
                f"https://api.flightradar24.com/common/v1/flight/list.json?fetchBy=reg&query={tail}&limit=1",
                timeout=8, headers=FR24_HEADERS)
            if r.ok:
                data = r.json()
                flights = data.get("result", {}).get("response", {}).get("data", [])
                if flights:
                    f = flights[0]
                    lat = f.get("lat") or f.get("latitude")
                    lon = f.get("lon") or f.get("longitude")
                    on_ground = f.get("on_ground", False) or f.get("gnd", False)
                    fr24id = f.get("id") or f.get("flight_id") or ""
                    icao = f.get("hex") or f.get("icao24") or ""
                    if lat and lon:
                        return json.dumps({
                            "found": True, "tail": tail, "source": "fr24",
                            "icao": icao, "fr24id": str(fr24id),
                            "lat":  lat, "lon": lon,
                            "alt":  f.get("alt") or f.get("altitude") or 0,
                            "spd":  f.get("speed") or f.get("gs") or 0,
                            "hdg":  f.get("heading") or f.get("track") or 0,
                            "vert": f.get("vspeed") or f.get("vert") or 0,
                            "on_ground": on_ground,
                        }).encode()
        except Exception as e:
            print(f"FR24 live error: {e}")

        return json.dumps({"found": False, "tail": tail}).encode()

    def _get_trace(self, icao, fr24id=""):
        # Try adsb.lol trace first
        try:
            r = requests.get(f"https://api.adsb.lol/v2/icao/{icao}/trace",
                             timeout=10, headers=ADSB_HEADERS)
            if r.ok:
                data = r.json()
                points = []
                for pt in data.get("trace", []):
                    if not isinstance(pt, list) or len(pt) < 3: continue
                    lat, lon = pt[1], pt[2]
                    if lat is None or lon is None: continue
                    points.append({"ts": pt[0], "lat": lat, "lon": lon,
                        "alt": pt[3] if len(pt)>3 else 0,
                        "spd": pt[4] if len(pt)>4 else 0,
                        "hdg": pt[5] if len(pt)>5 else 0,
                        "vert": pt[6] if len(pt)>6 else 0})
                if points:
                    return json.dumps({"icao": icao, "source": "adsb.lol", "points": points}).encode()
        except Exception as e:
            print(f"adsb.lol trace error: {e}")

        # Fallback: FR24 internal track endpoint
        if fr24id:
            try:
                r = requests.get(
                    f"https://api.flightradar24.com/common/v1/flight/playback.json?flightId={fr24id}&timestamp=0",
                    timeout=10, headers=FR24_HEADERS)
                if r.ok:
                    data = r.json()
                    track = data.get("result", {}).get("response", {}).get("data", {}).get("flight", {}).get("track", [])
                    points = []
                    for pt in track:
                        lat = pt.get("latitude") or pt.get("lat")
                        lon = pt.get("longitude") or pt.get("lon")
                        if lat is None or lon is None: continue
                        points.append({
                            "ts":   pt.get("timestamp") or pt.get("ts") or 0,
                            "lat":  lat, "lon": lon,
                            "alt":  pt.get("altitude") or pt.get("alt") or 0,
                            "spd":  pt.get("speed") or pt.get("spd") or 0,
                            "hdg":  pt.get("heading") or pt.get("hdg") or 0,
                            "vert": pt.get("verticalSpeed") or pt.get("vert") or 0,
                        })
                    if points:
                        return json.dumps({"icao": icao, "source": "fr24", "points": points}).encode()
            except Exception as e:
                print(f"FR24 trace error: {e}")

        return json.dumps({"icao": icao, "points": []}).encode()

    def log_message(self, format, *args):
        pass
