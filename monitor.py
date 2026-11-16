import os
import re
import json
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
THEATRE_PATTERN = re.compile(r"\b(pvr|inox|cinepolis|miraj|carnival|multiplex|cinemas?)\b", re.I)
# ----------------------------------------


def get_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    return webdriver.Chrome(options=opts)


def extract_theatres(driver, url):
    driver.get(url)
    body = lambda d: d.find_element(By.TAG_NAME, "body").text
    WebDriverWait(driver, 20).until(lambda d: len(body(d)) > 200)
    text = body(driver)
    print(f"  page text length: {len(text)}")
    return {l.strip() for l in text.split("\n")
            if len(l.strip()) < 100 and THEATRE_PATTERN.search(l)}


def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("No DISCORD_WEBHOOK_URL set; skipping notification.")
        return
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps({"content": message[:1900]}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print("  [Discord] Sent.")
    except Exception as e:
        print(f"  [Discord Error] {e}")


def run_check():
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)

    driver = get_driver()
    try:
        for name, url in MOVIES.items():
            print(f"Checking {name}...")
            try:
                current = extract_theatres(driver, url)
            except Exception as e:
                print(f"  [Scrape Error] {e}")
                continue

            known = set(state.get(name, []))
            new = current - known
            if not new:
                print("  No new theatres found.")
                continue

            send_discord(f"🚨 **{name}: new theatres/showtimes!**\n\n"
                         + "\n".join(f"• {t}" for t in sorted(new))
                         + f"\n\n👉 Book here: {url}")
            state[name] = sorted(known | new)
    finally:
        driver.quit()

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    run_check()
