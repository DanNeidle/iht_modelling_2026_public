# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Download WAS round 8 after assigning it to a UK Data Service project manually.

Uses browser-based Shibboleth login and credentials from projects/.env. A manual download into docs/was/ also works."""

from __future__ import annotations

import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import PROJECT_ROOT, WAS_DIR

ENV_PATH = PROJECT_ROOT.parent / ".env"
STUDY_NUMBER = "7215"

LOGIN_URL = (
    "https://vosp.data-archive.ac.uk/Shibboleth.sso/Login"
    "?entityID=https%3A%2F%2Fshib.data-archive.ac.uk%2Fshibboleth-idp"
    "&target=https%3A%2F%2Fvosp.data-archive.ac.uk%2Fidp%2Fproxy.jsp"
    "?target=https%3A%2F%2Fsp.ukdataservice.ac.uk%2Fsecure%2Fproxy.asp"
    "?target=https%3A%2F%2Fbeta.ukdataservice.ac.uk%2FUmbraco%2FSurface"
    "%2FLogin%2FAuthenticateUser?redirect=/MyAccount"
)

DATA_URL = "https://beta.ukdataservice.ac.uk/myaccount/data"


def read_credentials() -> tuple[str, str]:
    values = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")

    try:
        return values["UK_DATASERVICE_USER"], values["UK_DATASERVICE_PASS"]
    except KeyError as exc:
        raise SystemExit(
            f"{exc} missing from {ENV_PATH}. Expected UK_DATASERVICE_USER and "
            "UK_DATASERVICE_PASS."
        ) from exc


def make_driver(download_dir: Path):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1500,2400")
    options.add_experimental_option("prefs", {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
    })
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(120)
    return driver


def dismiss_overlays(driver) -> None:
    """The cookie banner sits on top of the page and swallows clicks."""
    driver.execute_script(
        "document.querySelectorAll('.cookie-message,.modal,.modal-backdrop')"
        ".forEach(e => e.remove());"
    )


def main() -> None:
    from selenium.webdriver.common.by import By

    username, password = read_credentials()
    WAS_DIR.mkdir(parents=True, exist_ok=True)
    download_dir = WAS_DIR / "_download"
    download_dir.mkdir(exist_ok=True)

    driver = make_driver(download_dir)
    try:
        driver.get(LOGIN_URL)
        time.sleep(4)
        driver.find_element(By.ID, "username").send_keys(username)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(
            By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"
        ).click()
        time.sleep(8)

        if "MyAccount" not in driver.current_url:
            raise SystemExit(f"login did not land on My Account: {driver.current_url}")
        print("  logged in")

        driver.get(DATA_URL)
        time.sleep(7)
        dismiss_overlays(driver)

        body = driver.find_element(By.TAG_NAME, "body").text
        if "Awaiting assignment" in body and STUDY_NUMBER in body:
            raise SystemExit(
                f"Study {STUDY_NUMBER} is still awaiting assignment to a "
                "project. Assign it in your UK Data Service account first."
            )

        # Find the study's download link under Projects.
        links = [
            (a.text.strip(), a.get_attribute("href"))
            for a in driver.find_elements(By.CSS_SELECTOR, "a")
            if a.get_attribute("href")
        ]
        candidates = [
            href for text, href in links
            if STUDY_NUMBER in (href or "") or "download" in (href or "").lower()
        ]

        if not candidates:
            print("  could not find a download link. Links on the page were:")
            for text, href in links[:40]:
                if text:
                    print(f"    {text[:40]:<40} {href}")
            raise SystemExit(
                "Download the round 8 tab-delimited or SPSS file by hand into "
                f"{WAS_DIR} and run inspect_was.py."
            )

        for href in candidates[:3]:
            print(f"  trying {href}")
            driver.get(href)
            time.sleep(15)

        # Wait for Chrome to finish writing.
        for _ in range(60):
            if not list(download_dir.glob("*.crdownload")):
                break
            time.sleep(5)

    finally:
        driver.quit()

    archives = list(download_dir.glob("*.zip"))
    if not archives:
        raise SystemExit(
            f"No zip arrived in {download_dir}. Download by hand into {WAS_DIR}."
        )

    for archive in archives:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(WAS_DIR)
        print(f"  extracted {archive.name}")

    print(f"\n  Files now under {WAS_DIR}:")
    for path in sorted(WAS_DIR.rglob("*"))[:30]:
        if path.is_file():
            print(f"    {path.relative_to(WAS_DIR)}  "
                  f"({path.stat().st_size / 1e6:.1f} MB)")

    print("\n  Next: python3 code/acquire/inspect_was.py")


if __name__ == "__main__":
    main()
