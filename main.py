import os
import json
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DATA_FILE = "notified_events.json"
TARGET_URL = "https://www.unive.it/data/agenda/8/"

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

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Errore: TELEGRAM_TOKEN o TELEGRAM_CHAT_ID non impostati.")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    response = requests.post(url, json=payload)
    return response.ok

def fetch_all_events():
    events = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(TARGET_URL, wait_until="networkidle")
        
        # Clicca sul pulsante "Carica altri" finché è presente nella pagina
        while True:
            # Cerca i possibili selettori/testi usati per il caricamento dinamico
            load_more_btn = page.locator("a:has-text('Carica altri'), button:has-text('Carica altri'), .carica-altri")
            if load_more_btn.count() > 0 and load_more_btn.first.is_visible():
                try:
                    load_more_btn.first.click()
                    page.wait_for_timeout(2000) # Attesa caricamento dei nuovi eventi
                except Exception as e:
                    print(f"Nessun altro evento da caricare o fine lista: {e}")
                    break
            else:
                break
                
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
                
            event_id = full_url.rstrip("/")
            
            if not any(e["id"] == event_id for e in events):
                events.append({
                    "id": event_id,
                    "title": title,
                    "url": full_url
                })
                
    return events

def main():
    notified_events = load_notified_events()
    current_events = fetch_all_events()
    
    print(f"Trovati {len(current_events)} eventi totali sulla pagina.")
    new_found = False
    
    for event in current_events:
        if event["id"] not in notified_events:
            msg = (
                f"<b>Nuovo evento Ca' Foscari!</b>\n\n"
                f"📌 <b>{event['title']}</b>\n"
                f"🔗 <a href='{event['url']}'>Apri dettaglio evento</a>"
            )
            print(f"Invio notifica Telegram per: {event['title']}")
            if send_telegram_message(msg):
                notified_events.add(event["id"])
                new_found = True
    
    if new_found:
        save_notified_events(notified_events)
        print("File degli eventi notificati aggiornato.")
    else:
        print("Nessun nuovo evento da notificare.")

if __name__ == "__main__":
    main()