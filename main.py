import os
import json
import html
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DATA_FILE = "notified_events.json"
TARGET_URL = "https://www.unive.it/data/agenda/8/"
ITALIAN_MONTHS = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"
)
ITALIAN_WEEKDAYS = (
    "lunedì", "martedì", "mercoledì", "giovedì",
    "venerdì", "sabato", "domenica"
)
MONTH_NUMBERS = {
    name: month
    for month, names in enumerate((
        ("jan", "january", "gen", "gennaio"),
        ("feb", "february", "febbraio"),
        ("mar", "march", "marzo"),
        ("apr", "april", "aprile"),
        ("may", "maggio"),
        ("jun", "june", "giu", "giugno"),
        ("jul", "july", "lug", "luglio"),
        ("aug", "august", "ago", "agosto"),
        ("sep", "september", "set", "settembre"),
        ("oct", "october", "ott", "ottobre"),
        ("nov", "november", "novembre"),
        ("dec", "december", "dic", "dicembre"),
    ), start=1)
    for name in names
}

def load_notified_events():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_notified_events(events_set):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(list(events_set), f, ensure_ascii=False, indent=2)


def parse_event_date(date_text):
    match = re.fullmatch(r"(\d{1,2})\s+([A-Za-zÀ-ÿ.]+)\s+(\d{4})", date_text.strip())
    if not match:
        raise ValueError(f"Formato data non riconosciuto: {date_text!r}")

    day, month_name, year = match.groups()
    month = MONTH_NUMBERS.get(month_name.lower().rstrip("."))
    if month is None:
        raise ValueError(f"Mese non riconosciuto nella data: {date_text!r}")
    return date(int(year), month, int(day))


def format_event_date(event_date):
    weekday = ITALIAN_WEEKDAYS[event_date.weekday()]
    month = ITALIAN_MONTHS[event_date.month - 1]
    return f"{weekday} {event_date.day} {month} {event_date.year}"


def build_google_calendar_url(event):
    event_date = event.get("date")
    if event_date is None:
        return None

    event_time = event.get("time")
    if event_time is None:
        dates = (
            f"{event_date:%Y%m%d}/"
            f"{event_date + timedelta(days=1):%Y%m%d}"
        )
    else:
        start = datetime.combine(event_date, event_time)
        end = start + timedelta(hours=1)
        dates = f"{start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}"

    details = f"Dettagli: {event['url']}"
    if event_time is not None:
        details += "\nDurata preimpostata: 1 ora (modificabile)."

    params = {
        "action": "TEMPLATE",
        "text": event["title"],
        "dates": dates,
        "ctz": "Europe/Rome",
        "details": details,
    }
    if event.get("location"):
        params["location"] = event["location"]

    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def send_telegram_message(text):
    global TELEGRAM_CHAT_ID

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN o TELEGRAM_CHAT_ID non impostati.")
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    def post_message(chat_id):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            response = requests.post(url, json=payload, timeout=20)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Errore di rete durante l'invio a Telegram ({type(exc).__name__})."
            ) from None

        try:
            result = response.json()
        except ValueError:
            raise RuntimeError(
                f"Telegram ha restituito HTTP {response.status_code} e una risposta non valida."
            ) from None
        return response, result

    response, result = post_message(TELEGRAM_CHAT_ID)
    if isinstance(result, dict) and result.get("ok") is not True:
        parameters = result.get("parameters")
        migrated_chat_id = (
            parameters.get("migrate_to_chat_id")
            if isinstance(parameters, dict)
            else None
        )
        if migrated_chat_id is not None:
            TELEGRAM_CHAT_ID = str(migrated_chat_id)
            print(
                "Telegram ha migrato il gruppo. Invio di nuovo al nuovo ID; "
                "aggiorna TELEGRAM_CHAT_ID nei GitHub Actions secrets."
            )
            response, result = post_message(TELEGRAM_CHAT_ID)

    if not response.ok or not isinstance(result, dict) or result.get("ok") is not True:
        description = result.get("description") if isinstance(result, dict) else None
        raise RuntimeError(
            f"Telegram ha rifiutato il messaggio (HTTP {response.status_code}): "
            f"{description or 'risposta non valida'}."
        )

    return True

def fetch_all_events():
    events = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        
        # Il pulsante carica gli eventi successivi inviando il form e ricaricando la pagina.
        while True:
            cookie_modal = page.locator("#cookieModal")
            if cookie_modal.is_visible():
                cookie_modal.get_by_role("button", name="Close").click()
                cookie_modal.wait_for(state="hidden")

            load_more_btn = page.locator("#altri")
            if load_more_btn.count() == 0 or not load_more_btn.is_visible():
                break

            with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                load_more_btn.click()
                
        content = page.content()
        browser.close()
        
    soup = BeautifulSoup(content, "html.parser")
    
    # Estrazione dei link agli eventi
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/data/agenda/8/" in href and href != "/data/agenda/8/" and href != TARGET_URL:
            full_url = href if href.startswith("http") else f"https://www.unive.it{href}"
            title = a_tag.get_text(strip=True)
            
            if not title or len(title) < 3:
                continue

            event_date = None
            event_time = None
            location = ""
            event_card = a_tag.find_parent("div", class_="evento")
            if event_card is None:
                print(f"Impossibile trovare la scheda dell'evento {full_url}.")
            else:
                date_element = event_card.select_one(".card-header strong")
                time_element = event_card.select_one(".card-header .float-end")
                location_element = event_card.select_one(".card-text")

                if date_element is None:
                    print(f"Data non trovata nella scheda dell'evento {full_url}.")
                else:
                    try:
                        event_date = parse_event_date(
                            date_element.get_text(" ", strip=True)
                        )
                    except ValueError as exc:
                        print(f"Impossibile leggere la data dell'evento {full_url}: {exc}")

                if time_element is not None:
                    time_match = re.search(
                        r"\b(\d{1,2}):(\d{2})\b",
                        time_element.get_text(" ", strip=True)
                    )
                    if time_match:
                        try:
                            event_time = time(
                                int(time_match.group(1)),
                                int(time_match.group(2))
                            )
                        except ValueError as exc:
                            print(
                                f"Impossibile leggere l'orario dell'evento "
                                f"{full_url}: {exc}"
                            )

                if location_element is not None:
                    location = location_element.get_text(" ", strip=True)

            event_id = full_url.rstrip("/")
            
            if not any(e["id"] == event_id for e in events):
                events.append({
                    "id": event_id,
                    "title": title,
                    "url": full_url,
                    "date": event_date,
                    "time": event_time,
                    "location": location
                })
                
    return events

def main():
    notified_events = load_notified_events()
    current_events = fetch_all_events()
    
    print(f"Trovati {len(current_events)} eventi totali sulla pagina.")
    new_found = False
    
    for event in current_events:
        if event["id"] not in notified_events:
            event_date = event.get("date")
            event_time = event.get("time")
            when = (
                format_event_date(event_date)
                if event_date is not None
                else "Data non disponibile"
            )
            if event_time is not None:
                when += f", alle {event_time:%H:%M}"

            calendar_url = build_google_calendar_url(event)
            calendar_link = (
                f"\n📅 <a href='{html.escape(calendar_url, quote=True)}'>"
                "Aggiungi a Google Calendar</a>"
                if calendar_url
                else ""
            )
            msg = (
                f"<b>Nuovo evento nell'Agenda!</b>\n\n"
                f"📌 <b>{html.escape(event['title'])}</b>\n"
                f"🗓️ <b>Quando:</b> {html.escape(when)}\n"
                f"🔗 <a href='{html.escape(event['url'], quote=True)}'>Apri dettaglio evento</a>"
                f"{calendar_link}"
            )
            print(f"Invio notifica Telegram per: {event['title']}")
            if send_telegram_message(msg):
                notified_events.add(event["id"])
                new_found = True
                save_notified_events(notified_events)
                print(f"Notifica Telegram inviata per: {event['title']}")
    
    if not new_found:
        print("Nessun nuovo evento da notificare.")
    else:
        print("File degli eventi notificati aggiornato.")

if __name__ == "__main__":
    main()