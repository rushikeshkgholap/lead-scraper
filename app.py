import os
import shutil
import sys
import types

# --- compatibility shim: newer Python (3.12+) removed 'distutils',
# but undetected_chromedriver still imports from it. This fakes the
# piece it needs (LooseVersion) using the 'packaging' library instead. ---
if 'distutils' not in sys.modules:
    try:
        import distutils  # noqa: F401
    except ImportError:
        from packaging.version import Version as _Version

        class LooseVersion(_Version):
            def __init__(self, vstring):
                cleaned = str(vstring).split('-')[0]
                super().__init__(cleaned)

        distutils_module = types.ModuleType('distutils')
        version_module = types.ModuleType('distutils.version')
        version_module.LooseVersion = LooseVersion
        distutils_module.version = version_module
        sys.modules['distutils'] = distutils_module
        sys.modules['distutils.version'] = version_module

import streamlit as st
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import time
import csv
import re
import math
import random
from geopy.geocoders import Nominatim

st.set_page_config(page_title="Lead Generator", layout="centered")

# ---------------- CORE FUNCTIONS (same logic as notebook) ----------------

geolocator = Nominatim(user_agent="lead_gen_app_v1")


def get_lat_lng(area_name):
    location = geolocator.geocode(area_name, country_codes='in', exactly_one=True)
    if location:
        return location.latitude, location.longitude, location.address
    return None, None, None


def generate_grid(center_lat, center_lng, radius_km, spacing_km=1.5):
    points = []
    lat_step = spacing_km / 111.0
    lng_step = spacing_km / (111.0 * math.cos(math.radians(center_lat)))

    steps = int(radius_km / spacing_km) + 1
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            lat = center_lat + i * lat_step
            lng = center_lng + j * lng_step
            dist = math.sqrt((i * spacing_km) ** 2 + (j * spacing_km) ** 2)
            if dist <= radius_km:
                points.append((lat, lng))
    return points


def open_maps_search_at_point(driver, keyword, lat, lng, zoom=15):
    query = keyword.replace(' ', '+')
    url = f"https://www.google.com/maps/search/{query}/@{lat},{lng},{zoom}z"
    driver.get(url)
    time.sleep(4)


def scroll_results_panel(driver, max_scrolls=25, pause=2):
    try:
        panel = driver.find_element(By.CSS_SELECTOR, 'div[role="feed"]')
    except Exception:
        return

    last_height = 0
    same_count = 0

    for _ in range(max_scrolls):
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", panel)
        time.sleep(pause)
        new_height = driver.execute_script("return arguments[0].scrollHeight", panel)

        if new_height == last_height:
            same_count += 1
            if same_count >= 3:
                break
        else:
            same_count = 0
        last_height = new_height


def extract_place_id(url):
    match = re.search(r'!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)', url)
    return match.group(1) if match else url


def extract_cards_data(driver):
    cards = driver.find_elements(By.CSS_SELECTOR, 'div.Nv2PK')
    data_list = []

    for card in cards:
        try:
            name = card.find_element(By.CSS_SELECTOR, 'div.qBF1Pd').text
        except Exception:
            name = ""

        try:
            url = card.find_element(By.CSS_SELECTOR, 'a.hfpxzc').get_attribute('href')
        except Exception:
            url = ""

        try:
            info_divs = card.find_elements(By.CSS_SELECTOR, 'div.W4Efsd')
            address = " | ".join([d.text for d in info_divs if d.text])
        except Exception:
            address = ""

        try:
            card.find_element(By.CSS_SELECTOR, 'a[data-value="Website"]')
            has_website = "Y"
        except Exception:
            has_website = "N"

        if name and url:
            data_list.append({
                "name": name,
                "address": address,
                "url": url,
                "has_website": has_website
            })

    return data_list


def collect_all_leads(driver, keyword, center_lat, center_lng, radius_km, spacing_km, progress_cb=None):
    grid_points = generate_grid(center_lat, center_lng, radius_km, spacing_km)
    seen_ids = set()
    all_cards = []

    for idx, (lat, lng) in enumerate(grid_points):
        open_maps_search_at_point(driver, keyword, lat, lng)
        scroll_results_panel(driver)
        cards_data = extract_cards_data(driver)

        for c in cards_data:
            pid = extract_place_id(c["url"])
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_cards.append(c)

        if progress_cb:
            progress_cb(idx + 1, len(grid_points), len(all_cards))

        time.sleep(random.uniform(1.5, 3))

    return all_cards


def build_leads_dataset(driver, all_cards, progress_cb=None):
    no_website_cards = [c for c in all_cards if c["has_website"] == "N"]
    results = []

    for idx, c in enumerate(no_website_cards):
        try:
            driver.get(c["url"])
            time.sleep(random.uniform(2, 3))

            phone = ""
            try:
                phone_el = driver.find_element(By.CSS_SELECTOR, 'button[data-item-id^="phone"]')
                phone = phone_el.get_attribute('aria-label').replace("Phone: ", "")
            except Exception:
                phone = ""

            results.append({
                "name": c["name"],
                "address": c["address"],
                "phone": phone,
                "has_website": "N",
                "maps_url": c["url"]
            })
        except Exception:
            continue

        if progress_cb:
            progress_cb(idx + 1, len(no_website_cards))

        time.sleep(random.uniform(1, 2))

    return results


def save_leads_csv(results, filename):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "address", "phone", "has_website", "maps_url"])
        writer.writeheader()
        writer.writerows(results)


# ---------------- STREAMLIT UI ----------------

st.title("Lead Generator")

with st.form("lead_form"):
    keyword = st.text_input("Keyword", placeholder="e.g. general physician, dentist, BDS")
    area_name = st.text_input("Area", placeholder="e.g. Kothrud, Pune")
    radius_km = st.slider("Radius (km)", min_value=1, max_value=5, value=2)
    submitted = st.form_submit_button("Start Scraping")

if submitted:
    if not keyword or not area_name:
        st.error("Place Keyword")
        st.stop()

    with st.spinner("Area Finding..."):
        lat, lng, matched_address = get_lat_lng(area_name)

    if lat is None:
        st.error("Area not Found (e.g. 'Kothrud, Pune, Maharashtra')")
        st.stop()

    st.success(f"Area Found: {matched_address}")
    st.info("Scraping Strat ")

    CHROMEDRIVER_PATH = "/tmp/chromedriver"
    if not os.path.exists(CHROMEDRIVER_PATH):
        shutil.copy("/usr/bin/chromedriver", CHROMEDRIVER_PATH)
        os.chmod(CHROMEDRIVER_PATH, 0o755)

    options = uc.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.binary_location = "/usr/bin/chromium"
    driver = uc.Chrome(options=options, driver_executable_path=CHROMEDRIVER_PATH)

    try:
        # Phase 1: grid search
        st.subheader("Phase 1: Area scaning...")
        grid_progress = st.progress(0)
        grid_status = st.empty()

        def grid_progress_cb(done, total, found_so_far):
            grid_progress.progress(done / total)
            grid_status.text(f"Grid point {done}/{total} — abhi tak {found_so_far} unique listings")

        all_cards = collect_all_leads(driver, keyword, lat, lng, radius_km, spacing_km=1.5, progress_cb=grid_progress_cb)

        st.success(f"Total unique listings : {len(all_cards)}")

        # Phase 2: detail fetch for no-website listings
        st.subheader("Phase 2: No-website detail ")
        detail_progress = st.progress(0)
        detail_status = st.empty()

        no_website_count = len([c for c in all_cards if c["has_website"] == "N"])

        def detail_progress_cb(done, total):
            if total > 0:
                detail_progress.progress(done / total)
            detail_status.text(f"{done}/{total} no-website leads process done")

        results = build_leads_dataset(driver, all_cards, progress_cb=detail_progress_cb)

        filename = f"{area_name.replace(' ', '_').replace(',', '')}_{keyword.replace(' ', '_')}_leads.csv"
        save_leads_csv(results, filename)

        st.success(f"✅ Done! {len(results)} leads Found")

        st.dataframe(results)

        with open(filename, "rb") as f:
            st.download_button("📥 CSV Download karo", f, file_name=filename, mime="text/csv")

    finally:
        driver.quit()
