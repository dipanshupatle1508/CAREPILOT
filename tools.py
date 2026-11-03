"""Tools the agents use: hospital discovery (OpenStreetMap/Nominatim), specialist mapping, Leaflet map, notifications."""
import math

import requests

import config

# Shown only if Nominatim is unreachable (e.g. hackathon wifi). Clearly labelled, never real hospital names.
SAMPLE_HOSPITALS = [
    {"name": "Sample Hospital A (offline demo data)", "distance_km": 2.1, "lat": None, "lon": None, "sample": True},
    {"name": "Sample Hospital B (offline demo data)", "distance_km": 3.4, "lat": None, "lon": None, "sample": True},
]


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geocode(place: str):
    try:
        r = requests.get(f"{config.NOMINATIM_URL}/search", params={"q": place, "format": "jsonv2", "limit": 1},
                         headers={"User-Agent": config.USER_AGENT}, timeout=8)
        data = r.json()
        return (float(data[0]["lat"]), float(data[0]["lon"])) if data else None
    except Exception:
        return None


def find_hospitals(lat: float, lon: float, radius_km: float = 8, limit: int = 5) -> list:
    d = radius_km / 111.0
    try:
        r = requests.get(
            f"{config.NOMINATIM_URL}/search",
            params={"q": "hospital", "format": "jsonv2", "limit": 30, "bounded": 1,
                    "viewbox": f"{lon - d},{lat + d},{lon + d},{lat - d}"},   # left,top,right,bottom
            headers={"User-Agent": config.USER_AGENT}, timeout=10)
        found = []
        for h in r.json():
            hl, ho = float(h["lat"]), float(h["lon"])
            found.append({"name": h["display_name"].split(",")[0], "lat": hl, "lon": ho,
                          "distance_km": round(_haversine(lat, lon, hl, ho), 1), "sample": False})
        found.sort(key=lambda x: x["distance_km"])
        return found[:limit] or SAMPLE_HOSPITALS
    except Exception:
        return SAMPLE_HOSPITALS


def find_specialists(lat: float, lon: float, specialty: str, radius_km: float = 10, limit: int = 3) -> list:
    """Nearby clinics / doctors from OpenStreetMap. OSM coverage of individual doctors is patchy, so [] is common."""
    token = specialty.split("/")[0].strip().lower()
    d = radius_km / 111.0
    try:
        r = requests.get(f"{config.NOMINATIM_URL}/search",
                         params={"q": token, "format": "jsonv2", "limit": 15, "bounded": 1,
                                 "viewbox": f"{lon - d},{lat + d},{lon + d},{lat - d}"},
                         headers={"User-Agent": config.USER_AGENT}, timeout=10)
        out = [{"name": x["display_name"].split(",")[0], "distance_km": round(_haversine(lat, lon, float(x["lat"]), float(x["lon"])), 1)}
               for x in r.json()]
        return sorted(out, key=lambda x: x["distance_km"])[:limit]
    except Exception:
        return []


def write_map_html(path: str, lat: float, lon: float, hospitals: list):
    """Leaflet map of the patient + nearby hospitals (open the file in a browser)."""
    markers = "".join(
        f"L.marker([{h['lat']},{h['lon']}]).addTo(m).bindPopup({h['name']!r} + ' ({h['distance_km']} km)');"
        for h in hospitals if h.get("lat") is not None)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Nearest hospitals</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script></head>
<body style="margin:0"><div id="map" style="height:100vh"></div><script>
var m = L.map('map').setView([{lat},{lon}], 13);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{attribution:'&copy; OpenStreetMap'}}).addTo(m);
L.circleMarker([{lat},{lon}], {{color:'red'}}).addTo(m).bindPopup('Patient');{markers}
</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def send_notification(audience: str, message: str):
    """Hook point: replace print with Twilio / WhatsApp / email / push for a real deployment."""
    print(f"   [NOTIFY -> {audience.upper()}] {message[:300]}")
