import os
import re
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://www.deportick.com/event/argbenin26"
EVENT_ID = "argbenin26"  # parte de la direccion que identifica el evento

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

# Frases (sin tildes) que indican una fila virtual / sala de espera
QUEUE_PHRASES = [
    "fila virtual",
    "sala de espera",
    "estas en la fila",
    "estas en la sala",
    "posicion en la fila",
    "personas delante",
    "waiting room",
    "virtual queue",
    "you are in line",
    "you are now in line",
]

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "afa-benin-marco-1609")
NTFY_SERVER = "https://ntfy.sh"

STATE_FILE = "state.txt"
QUEUE_STATE_FILE = "queue_state.txt"


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


def notify_queue(reason):

    message = (
        "🚦 FILA VIRTUAL HABILITADA\n\n"
        "Argentina 🇦🇷 vs Benín 🇧🇯\n\n"
        f"Detectado: {reason}\n\n{URL}"
    )

    response = requests.post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        headers={
            "Title": "Fila virtual en Deportick",
            "Priority": "urgent",
            "Tags": "rotating_light,vertical_traffic_light",
            "Click": URL,
        },
        data=message.encode("utf-8"),
        timeout=20,
    )

    response.raise_for_status()


def load_state(path=STATE_FILE):

    try:
        with open(path, encoding="utf-8") as file:
            return file.read().strip()

    except FileNotFoundError:
        return ""


def save_state(state, path=STATE_FILE):

    with open(path, "w", encoding="utf-8") as file:
        file.write(state)


def detect_queue(final_url, text):
    """Devuelve (activa, motivo). Dos señales:
    1) la pagina te llevo a otro sitio o a otra direccion (tipico de una sala de espera)
    2) aparece texto de fila virtual y NO se ven los sectores del evento
    """
    parsed = urlparse(final_url or "")
    host = parsed.netloc.lower()

    if host:  # si no hay host, la pagina no cargo: no es una fila
        if not host.endswith("deportick.com"):
            return True, f"redirigido a otro sitio ({host})"
        if EVENT_ID not in parsed.path.lower():
            return True, f"redirigido a {parsed.path or '/'}"

    t = normalize(text or "")
    frases = [p for p in QUEUE_PHRASES if p in t]
    sectores_visibles = any(
        normalize(alias) in t
        for aliases in SECTORS.values()
        for alias in aliases
    )

    if frases and not sectores_visibles:
        return True, "texto en pantalla: " + ", ".join(frases)

    return False, ""


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

        # ---- Fila virtual ----
        try:
            body_text = page.inner_text("body", timeout=5000)
        except Exception:
            body_text = ""

        print("Direccion final:", page.url)
        print("Caracteres de texto en pantalla:", len(body_text))
        print("Inicio del texto:", normalize(body_text)[:200])

        queue_active, queue_reason = detect_queue(page.url, body_text)
        previous_queue = load_state(QUEUE_STATE_FILE)

        if queue_active:
            print("🚦 Fila virtual detectada:", queue_reason)
            if previous_queue != "queue":
                notify_queue(queue_reason)
                print("🚨 Notificación de fila virtual enviada a ntfy")
            else:
                print("La fila virtual sigue activa. No repito la notificación.")
            save_state("queue", QUEUE_STATE_FILE)
        else:
            print("Sin fila virtual.")
            save_state("", QUEUE_STATE_FILE)

        # ---- Sectores ----
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
