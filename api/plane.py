from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs

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

        resp_headers = {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        }

        try:
            if icao and trace:
                body = self._get_trace(icao)
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
                    return json.dumps({
                        "found": True, "tail": tail, "source": "adsb.lol",
                        "icao": ac.get("hex", ""),
                        "lat":  ac.get("lat"), "lon": ac.get("lon"),
                        "alt":  ac.get("alt_baro") or ac.get("alt_geom") or 0,
                        "spd":  ac.get("gs") or 0,
                        "hdg":  ac.get("track") or 0,
                        "vert": ac.get("baro_rate") or ac.get("geom_rate") or 0,
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
                    lat = f.get("lat") or (f.get("latitude"))
                    lon = f.get("lon") or (f.get("longitude"))
                    if lat and lon:
                        return json.dumps({
                            "found": True, "tail": tail, "source": "fr24",
                            "icao": f.get("hex") or f.get("icao24") or "",
                            "lat":  lat, "lon": lon,
                            "alt":  (f.get("altitude") or 0) * 3.28084 if f.get("altitude") else f.get("alt") or 0,
                            "spd":  f.get("speed") or f.get("gs") or 0,
                            "hdg":  f.get("heading") or f.get("track") or 0,
                            "vert": f.get("vspeed") or f.get("vert") or 0,
                        }).encode()
        except Exception as e:
            print(f"FR24 error: {e}")

        return json.dumps({"found": False, "tail": tail}).encode()

    def _get_trace(self, icao):
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
                return json.dumps({"icao": icao, "points": points}).encode()
        except Exception as e:
            print(f"Trace error: {e}")
        return json.dumps({"icao": icao, "points": []}).encode()

    def log_message(self, format, *args):
        pass
