import os
import re
import json
import time
import urllib.parse
import urllib.request
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

# ---------------- CONFIG ----------------
MOVIES = {
    "Avengers: Doomsday": "https://www.district.in/movies/avengers-doomsday-movie-tickets-in-bengaluru-MV176735",
}
STATE_FILE = "known_theatres.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Distances are calculated from these points (lat, lon). Edit if they're off.
ORIGINS = {
    "RT Nagar": (13.0230, 77.5950),
    "PES RR": (12.9347, 77.5348),
}
THEATRE_PATTERN = re.compile(r"\b(pvr|inox|cinepolis|miraj|carnival|multiplex|cinemas?)\b", re.I)
# ----------------------------------------

UA = "theatre-watcher/1.0 (personal use)"
TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s?[AP]M$", re.I)
SKIP_RE = re.compile(r"km away|cancellation|^cc$", re.I)


# ---------- scraping ----------
def get_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    return webdriver.Chrome(options=opts)


def parse_theatres(text):
    """Returns {theatre name: {"times": [...], "labels": [...]}} for theatres that have showtimes."""
    theatres, current = {}, None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if TIME_RE.match(line):
            if current:
                t = line.upper()
                if t not in theatres[current]["times"]:
                    theatres[current]["times"].append(t)
            continue
        if SKIP_RE.search(line):
            continue
        if len(line) < 100 and THEATRE_PATTERN.search(line):
            current = line
            theatres.setdefault(current, {"times": [], "labels": []})
            continue
        # Screen names / formats (BIGPIX, MS INFINITY ...) show up as uppercase lines
        if current and len(line) >= 3 and line.isupper() and line not in theatres[current]["labels"]:
            theatres[current]["labels"].append(line)
    # Only count entries that actually have shows (filters out nav/footer text)
    return {n: d for n, d in theatres.items() if d["times"]}


def extract_theatres(driver, url):
    driver.get(url)
    body = lambda d: d.find_element(By.TAG_NAME, "body").text
    WebDriverWait(driver, 20).until(lambda d: len(body(d)) > 200)
    text = body(driver)
    print(f"  page text length: {len(text)}")
    return parse_theatres(text)


# ---------- distance ----------
def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def geocode(name):
    """Returns (lat, lon, approximate) or None. Falls back to area-level if the venue isn't found."""
    parts = [p.strip() for p in name.split(",")]
    stripped = re.sub(r"^(pvr|inox|cinepolis|miraj|carnival)\s*:?\s*", "", name, flags=re.I)
    tries = [(name, False)]
    if stripped != name:
        tries.append((stripped, False))
    if len(parts) > 2:
        tries.append((", ".join(parts[-2:]), True))
    for query, approx in tries:
        try:
            q = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1, "countrycodes": "in"})
            res = get_json(f"https://nominatim.openstreetmap.org/search?{q}")
            time.sleep(1.1)  # Nominatim's rate limit is 1 request/second
            if res:
                return float(res[0]["lat"]), float(res[0]["lon"]), approx
        except Exception as e:
            print(f"  [Geocode Error] {query}: {e}")
    return None


def drive_info(origin, dest):
    url = (f"https://router.project-osrm.org/route/v1/driving/"
           f"{origin[1]},{origin[0]};{dest[1]},{dest[0]}?overview=false")
    route = get_json(url)["routes"][0]
    return f"{route['distance'] / 1000:.1f} km (~{round(route['duration'] / 60)} min)"


def distance_line(name):
    try:
        geo = geocode(name)
        if not geo:
            return "📍 distance unavailable"
        lat, lon, approx = geo
        parts = [f"{label}: {drive_info(o, (lat, lon))}" for label, o in ORIGINS.items()]
        return "📍 " + " | ".join(parts) + ("  (approx, area-level)" if approx else "")
    except Exception as e:
        print(f"  [Distance Error] {name}: {e}")
        return "📍 distance unavailable"


# ---------- notify ----------
def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("No DISCORD_WEBHOOK_URL set; skipping notification.")
        return False
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps({"content": message}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print("  [Discord] Sent.")
        return True
    except Exception as e:
        print(f"  [Discord Error] {e}")
        return False


def send_chunked(header, blocks, footer):
    """Discord caps messages at 2000 chars, so split across several if needed."""
    messages, cur = [], header
    for b in blocks:
        if len(cur) + len(b) + 2 > 1800:
            messages.append(cur)
            cur = ""
        cur += "\n\n" + b
    messages.append(cur + "\n\n" + footer)
    return all([send_discord(m.strip()) for m in messages])


def theatre_block(name, info):
    lines = [f"• **{name}**", "🕒 " + ", ".join(info["times"])]
    if info["labels"]:
        lines.append("🎞️ " + ", ".join(info["labels"]))
    lines.append(distance_line(name))
    return "\n".join(lines)


# ---------- main ----------
def run_check():
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)

    driver = get_driver()
    try:
        for movie, url in MOVIES.items():
            print(f"Checking {movie}...")
            try:
                current = extract_theatres(driver, url)
            except Exception as e:
                print(f"  [Scrape Error] {e}")
                continue

            known = set(state.get(movie, []))
            new = sorted(set(current) - known)
            if not new:
                print("  No new theatres found.")
                continue

            blocks = [theatre_block(n, current[n]) for n in new]
            ok = send_chunked(f"🚨 **{movie}: new theatres listed!**", blocks, f"👉 Book here: {url}")
            if ok:  # only remember them once the alert actually went out
                state[movie] = sorted(known | set(new))
    finally:
        driver.quit()

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    run_check()
