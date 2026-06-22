import requests
import json
from urllib.parse import urlparse, parse_qs

HEADERS = {"User-Agent": "MangoWindHub/1.0 skydiving-plane-tracker"}

def handler(request):
    params = parse_qs(urlparse(request.url).query)
    tail  = (params.get("tail",  [None])[0] or "").upper().strip()
    icao  = (params.get("icao",  [None])[0] or "").lower().strip()
    trace = params.get("trace",  [None])[0]

    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
    }

    try:
        if icao and trace:
            r = requests.get(f"https://api.adsb.lol/v2/icao/{icao}/trace",
                             timeout=10, headers=HEADERS)
            if not r.ok:
                return Response(json.dumps({"error": f"ADS-B {r.status_code}"}), 502, headers)
            data = r.json()
            points = []
            for pt in data.get("trace", []):
                if not isinstance(pt, list) or len(pt) < 3: continue
                lat, lon = pt[1], pt[2]
                if lat is None or lon is None: continue
                points.append({"ts": pt[0], "lat": lat, "lon": lon,
                    "alt": pt[3] if len(pt)>3 else 0, "spd": pt[4] if len(pt)>4 else 0,
                    "hdg": pt[5] if len(pt)>5 else 0, "vert": pt[6] if len(pt)>6 else 0})
            return Response(json.dumps({"icao": icao, "points": points}), 200, headers)

        elif tail:
            r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}",
                             timeout=8, headers=HEADERS)
            if not r.ok:
                return Response(json.dumps({"error": f"ADS-B {r.status_code}"}), 502, headers)
            data = r.json()
            ac = next((a for a in data.get("ac", []) if a.get("lat") is not None), None)
            if not ac:
                return Response(json.dumps({"found": False, "tail": tail}), 200, headers)
            return Response(json.dumps({
                "found": True, "tail": tail,
                "icao": ac.get("hex", ""),
                "lat":  ac.get("lat"),  "lon": ac.get("lon"),
                "alt":  ac.get("alt_baro") or ac.get("alt_geom") or 0,
                "spd":  ac.get("gs") or 0,
                "hdg":  ac.get("track") or 0,
                "vert": ac.get("baro_rate") or ac.get("geom_rate") or 0,
            }), 200, headers)

        else:
            return Response(json.dumps({"error": "tail or icao required"}), 400, headers)

    except Exception as e:
        return Response(json.dumps({"error": str(e)}), 500, headers)
