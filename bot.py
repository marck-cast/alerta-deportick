import os
import re
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://www.deportick.com/event/argbenin26"

SECTORS = {
    "Centenario Alta": ["centenario alta"],
    "Sívori Alta": ["sívori alta", "sivori alta"],
}

UNAVAILABLE_WORDS = [
    "agotado",
    "sin stock",
    "no disponible",
    "indisponible",
    "sold out",
    "unavailable",
    "disabled",
]

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "afa-benin-marco-1609")
NTFY_SERVER = "https://ntfy.sh"

STATE_FILE = "state.txt"


def normalize(text):
    table = str.maketrans("áéíóúü", "aeiouu")
    text = text.lower().translate(table)
    return re.sub(r"\s+", " ", text).strip()


def notify_ntfy(sectors):

    message = (
        "🚨 ENTRADAS DISPONIBLES\n\n"
        "Argentina 🇦🇷 vs Benín 🇧🇯\n\n"
        + "\n".join(f"✅ {sector}" for sector in sectors)
        + f"\n\n{URL}"
    )

    response = requests.post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        headers={
            "Title": "Entradas Argentina - Benin",
            "Priority": "urgent",
            "Tags": "rotating_light,ticket",
            "Click": URL,
        },
        data=message.encode("utf-8"),
        timeout=20,
    )

    response.raise_for_status()


def load_state():

    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            return file.read().strip()

    except FileNotFoundError:
        return ""


def save_state(state):

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        file.write(state)


def sector_available(page, aliases):

    for alias in aliases:

        locator = page.get_by_text(
            re.compile(re.escape(alias), re.I)
        )

        count = min(locator.count(), 20)

        for i in range(count):

            try:

                element = locator.nth(i)

                if not element.is_visible():
                    continue

                candidate = element

                for _ in range(4):

                    text = normalize(
                        candidate.inner_text(timeout=1000)
                    )

                    css_class = normalize(
                        candidate.get_attribute("class") or ""
                    )

                    aria_disabled = normalize(
                        candidate.get_attribute("aria-disabled") or ""
                    )

                    unavailable = any(
                        word in text
                        for word in UNAVAILABLE_WORDS
                    )

                    unavailable = (
                        unavailable
                        or "disabled" in css_class
                        or "unavailable" in css_class
                        or "sold" in css_class
                        or aria_disabled == "true"
                    )

                    if not unavailable:
                        return True

                    parent = candidate.locator("..")

                    if parent.count() == 0:
                        break

                    candidate = parent

            except Exception:
                continue

    return False


def main():

    print("Revisando Deportick...")

    with sync_playwright() as playwright:

        browser = playwright.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1440,
                "height": 1000
            }
        )

        try:

            page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=45000,
            )

            page.wait_for_timeout(7000)

        except PlaywrightTimeoutError:

            print(
                "La página tardó demasiado. "
                "Analizando lo que llegó a cargar."
            )

        found = []

        for sector, aliases in SECTORS.items():

            available = sector_available(
                page,
                aliases
            )

            print(
                sector,
                "=>",
                "DISPONIBLE" if available else "no disponible"
            )

            if available:
                found.append(sector)

        current = ",".join(found)
        previous = load_state()

        if current and current != previous:

            notify_ntfy(found)

            print(
                "🚨 Notificación enviada a ntfy"
            )

        elif current == previous and current:

            print(
                "Las entradas siguen disponibles. "
                "No repito la notificación."
            )

        elif not current:

            print(
                "No se detectaron entradas."
            )

        save_state(current)

        browser.close()


if __name__ == "__main__":
    main()
