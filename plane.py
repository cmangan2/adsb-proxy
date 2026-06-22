from http.server import BaseHTTPRequestHandler
import requests
import json
import urllib.parse

HEADERS = {"User-Agent": "MangoWindHub/1.0 skydiving-plane-tracker"}
ALLOWED_ORIGIN = "https://mango-winds.onrender.com"

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # Route: /api/plane?tail=N123AB
        # Route: /api/plane?icao=abc123&trace=1
        tail = params.get("tail", [None])[0]
        icao = params.get("icao", [None])[0]
        trace = params.get("trace", [None])[0]

        try:
            if icao and trace:
                r = requests.get(f"https://api.adsb.lol/v2/icao/{icao.lower()}/trace",
                                 timeout=10, headers=HEADERS)
                if not r.ok:
                    self._respond(502, {"error": f"ADS-B error {r.status_code}"})
                    return
                data = r.json()
                raw_trace = data.get("trace", [])
                points = []
                for pt in raw_trace:
                    if not isinstance(pt, list) or len(pt) < 3: continue
                    lat = pt[1] if len(pt) > 1 else None
                    lon = pt[2] if len(pt) > 2 else None
                    if lat is None or lon is None: continue
                    points.append({
                        "ts": pt[0] if len(pt) > 0 else 0,
                        "lat": lat, "lon": lon,
                        "alt":  pt[3] if len(pt) > 3 else 0,
                        "spd":  pt[4] if len(pt) > 4 else 0,
                        "hdg":  pt[5] if len(pt) > 5 else 0,
                        "vert": pt[6] if len(pt) > 6 else 0,
                    })
                self._respond(200, {"icao": icao, "points": points})

            elif tail:
                tail = tail.upper().strip()
                r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}",
                                 timeout=8, headers=HEADERS)
                if not r.ok:
                    self._respond(502, {"error": f"ADS-B error {r.status_code}"})
                    return
                data = r.json()
                ac_list = data.get("ac", [])
                ac = next((a for a in ac_list if a.get("lat") is not None), None)
                if not ac:
                    self._respond(200, {"found": False, "tail": tail})
                    return
                self._respond(200, {
                    "found": True, "tail": tail,
                    "icao": ac.get("hex", ""),
                    "lat":  ac.get("lat"),
                    "lon":  ac.get("lon"),
                    "alt":  ac.get("alt_baro") or ac.get("alt_geom") or 0,
                    "spd":  ac.get("gs") or 0,
                    "hdg":  ac.get("track") or 0,
                    "vert": ac.get("baro_rate") or ac.get("geom_rate") or 0,
                })
            else:
                self._respond(400, {"error": "tail or icao required"})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
