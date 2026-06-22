from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs

HEADERS = {"User-Agent": "MangoWindHub/1.0"}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        tail  = (params.get("tail",  [None])[0] or "").upper().strip()
        icao  = (params.get("icao",  [None])[0] or "").lower().strip()
        trace = params.get("trace",  [None])[0]

        try:
            if icao and trace:
                r = requests.get(f"https://api.adsb.lol/v2/icao/{icao}/trace",
                                 timeout=10, headers=HEADERS)
                data = r.json() if r.ok else {}
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
                body = json.dumps({"icao": icao, "points": points}).encode()

            elif tail:
                r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}",
                                 timeout=8, headers=HEADERS)
                data = r.json() if r.ok else {}
                ac = next((a for a in data.get("ac", []) if a.get("lat") is not None), None)
                if not ac:
                    body = json.dumps({"found": False, "tail": tail}).encode()
                else:
                    body = json.dumps({
                        "found": True, "tail": tail,
                        "icao": ac.get("hex", ""),
                        "lat":  ac.get("lat"), "lon": ac.get("lon"),
                        "alt":  ac.get("alt_baro") or ac.get("alt_geom") or 0,
                        "spd":  ac.get("gs") or 0,
                        "hdg":  ac.get("track") or 0,
                        "vert": ac.get("baro_rate") or ac.get("geom_rate") or 0,
                    }).encode()
            else:
                body = json.dumps({"error": "tail or icao required"}).encode()

        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
