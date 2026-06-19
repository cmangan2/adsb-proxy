from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

HEADERS = {"User-Agent": "MangoWindHub/1.0 skydiving-plane-tracker"}

@app.route("/plane")
def plane():
    tail = request.args.get("tail", "").upper().strip()
    if not tail:
        return jsonify({"error": "tail required"}), 400
    try:
        r = requests.get(f"https://api.adsb.lol/v2/registration/{tail}", timeout=8, headers=HEADERS)
        if not r.ok:
            return jsonify({"error": f"ADS-B error {r.status_code}"}), 502
        data = r.json()
        ac_list = data.get("ac", [])
        ac = next((a for a in ac_list if a.get("lat") is not None), None)
        if not ac:
            return jsonify({"found": False, "tail": tail})
        return jsonify({
            "found": True, "tail": tail,
            "icao": ac.get("hex", ""),
            "lat":  ac.get("lat"),
            "lon":  ac.get("lon"),
            "alt":  ac.get("alt_baro") or ac.get("alt_geom") or 0,
            "spd":  ac.get("gs") or 0,
            "hdg":  ac.get("track") or 0,
            "vert": ac.get("baro_rate") or ac.get("geom_rate") or 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/plane/trace")
def plane_trace():
    icao = request.args.get("icao", "").lower().strip()
    if not icao:
        return jsonify({"error": "icao required"}), 400
    try:
        r = requests.get(f"https://api.adsb.lol/v2/icao/{icao}/trace", timeout=12, headers=HEADERS)
        if not r.ok:
            return jsonify({"error": f"Trace error {r.status_code}"}), 502
        data = r.json()
        raw_trace = data.get("trace", [])
        points = []
        for pt in raw_trace:
            if not isinstance(pt, list) or len(pt) < 3: continue
            lat, lon = pt[1] if len(pt) > 1 else None, pt[2] if len(pt) > 2 else None
            if lat is None or lon is None: continue
            points.append({
                "ts":   pt[0] if len(pt) > 0 else 0,
                "lat":  lat, "lon": lon,
                "alt":  pt[3] if len(pt) > 3 else 0,
                "spd":  pt[4] if len(pt) > 4 else 0,
                "hdg":  pt[5] if len(pt) > 5 else 0,
                "vert": pt[6] if len(pt) > 6 else 0,
            })
        return jsonify({"icao": icao, "points": points})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "https://mango-winds.onrender.com"
    response.headers["Access-Control-Allow-Methods"] = "GET"
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
