import asyncio
import html
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask
from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Bot
from telethon import TelegramClient, events

# ============================================================
# CONFIGURAZIONE
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "TUO_TELEGRAM_TOKEN")
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID", "@TarloDelRisparmio")
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelrispa-21")
INTERVALLO_MINUTI = int(os.getenv("INTERVALLO_MINUTI", "30"))

# Credenziali Client Telegram (da my.telegram.org per il canale spia)
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "1234567"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "TUO_API_HASH")

# Canali Telegram da spiare (username o ID)
CANALI_SPIA = ["offertedeltag", "scontiamolo"] 

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# ============================================================
# GESTIONE DATABASE SQLITE
# ============================================================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                asin TEXT PRIMARY KEY,
                titolo TEXT,
                sconto INTEGER,
                prezzo_attuale TEXT,
                prezzo_precedente TEXT,
                immagine_url TEXT,
                inserito_il DATETIME,
                inviato_il DATETIME
            )
        """)
        conn.commit()

def aggiungi_prodotto_db(p):
    with sqlite3.connect(DB_PATH) as conn:
        conn.cursor().execute("""
            INSERT OR IGNORE INTO prodotti 
            (asin, titolo, sconto, prezzo_attuale, prezzo_precedente, immagine_url, inserito_il)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            p["asin"], p["titolo"], p.get("sconto", 0),
            p["prezzo_attuale"], p["prezzo_precedente"],
            p["immagine_url"], datetime.now()
        ))
        conn.commit()

def ottieni_prossimo_prodotto():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM prodotti WHERE inviato_il IS NULL ORDER BY inserito_il ASC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None

def segna_inviato(asin):
    with sqlite3.connect(DB_PATH) as conn:
        conn.cursor().execute("UPDATE prodotti SET inviato_il = ? WHERE asin = ?", (datetime.now(), asin))
        conn.commit()

# ============================================================
# SCRAPER AMAZON
# ============================================================
def estrai_asin(testo):
    """Trova un ASIN Amazon (es. B0BT7V2P2Q) all'interno di un testo o link."""
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    if not match:
        match = re.search(r'\b(B[0-9A-Z]{9})\b', testo)
    return match.group(1) if match else None

def scarica_dettagli_amazon(asin):
    """Effettua lo scraping della pagina prodotto di Amazon dato un ASIN."""
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code != 200:
        return None
        
    soup = BeautifulSoup(res.text, "html.parser")
    
    # Titolo
    titolo_elem = soup.find("span", {"id": "productTitle"})
    if not titolo_elem:
        return None
    titolo = titolo_elem.get_text().strip()

    # Prezzo attuale
    prezzo_elem = soup.find("span", {"class": "a-price-whole"})
    frazione_elem = soup.find("span", {"class": "a-price-fraction"})
    if not prezzo_elem:
        return None
    prezzo_attuale = f"{prezzo_elem.get_text().strip().replace(',', '')},{frazione_elem.get_text().strip() if frazione_elem else '00'}"

    # Prezzo precedente
    vecchio_elem = soup.find("span", {"class": "a-price a-text-price"})
    prezzo_precedente = prezzo_attuale
    if vecchio_elem:
        v_span = vecchio_elem.find("span", {"class": "a-offscreen"})
        if v_span:
            prezzo_precedente = v_span.get_text().replace("€", "").strip()

    # Immagine
    img_elem = soup.find("img", {"id": "landingImage"})
    img_url = img_elem["src"] if img_elem else ""

    # Sconto stimato
    try:
        p_att = float(prezzo_attuale.replace(",", "."))
        p_prec = float(prezzo_precedente.replace(",", "."))
        sconto = int(((p_prec - p_att) / p_prec) * 100) if p_prec > p_att else 0
    except ValueError:
        sconto = 0

    return {
        "asin": asin,
        "titolo": titolo,
        "prezzo_attuale": prezzo_attuale,
        "prezzo_precedente": prezzo_precedente,
        "sconto": sconto,
        "immagine_url": img_url
    }

# ============================================================
# CANALE SPIA (TELETHON USERBOT)
# ============================================================
async def avvia_canale_spia():
    client = TelegramClient("session_spia", TELEGRAM_API_ID, TELEGRAM_API_HASH)
    
    @client.on(events.NewMessage(chats=CANALI_SPIA))
    async def gestisci_nuovo_messaggio(event):
        testo = event.message.text or ""
        
        # Cerca link visibili o nascosti nei pulsanti/ipertesti
        urls = re.findall(r'https?://[^\s]+', testo)
        if event.message.buttons:
            for row in event.message.buttons:
                for btn in row:
                    if btn.url:
                        urls.append(btn.url)

        for url in urls:
            asin = estrai_asin(url)
            if asin:
                print(f"[SPIA] Intercettato ASIN: {asin}")
                # Esegue lo scraping in un thread separato per non bloccare l'event loop
                prodotto = await asyncio.to_thread(scarica_dettagli_amazon, asin)
                if prodotto:
                    aggiungi_prodotto_db(prodotto)
                    print(f"[SPIA] Aggiunto al DB: {prodotto['titolo']}")
                break

    await client.start()
    await client.run_until_disconnected()

# ============================================================
# GENERAZIONE IMMAGINE & FLASK SERVER
# ============================================================
@app.route("/")
def home():
    return "Tarlo del Risparmio - Bot Attivo"

@app.route("/health")
def health():
    return {"status": "ok"}

def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# [Qui vanno le tue funzioni di ausilio grafica: carica_font, spezza_testo, scarica_immagine, centra_testo, crea_immagine_offerta]
# ... (Mantenere le funzioni Pillow già implementate nel codice precedente) ...

async def invia_offerta(prodotto):
    link = f"https://www.amazon.it/dp/{prodotto['asin']}?tag={AMAZON_TAG}"
    foto = crea_immagine_offerta(prodotto) # Funzione Pillow

    didascalia = (
        "🐜 <b>Il Tarlo ha colpito ancora!</b>\n\n"
        f"📦 <b>{html.escape(str(prodotto['titolo']))}</b>\n"
        f"📉 Sconto: <b>-{prodotto['sconto']}%</b>\n"
        f"💰 <s>{html.escape(str(prodotto['prezzo_precedente']))} €</s> "
        f"➜ <b>{html.escape(str(prodotto['prezzo_attuale']))} €</b>\n\n"
        f'👉 <a href="{html.escape(link, quote=True)}">ACQUISTA SUBITO IN OFFERTA</a>\n\n'
        "#IlTarloDelRisparmio"
    )

    with open(foto, "rb") as file_foto:
        await bot.send_photo(
            chat_id=CANALE_CHAT_ID,
            photo=file_foto,
            caption=didascalia,
            parse_mode="HTML",
        )

# ============================================================
# MAIN LOOP
# ============================================================
async def ciclo_pubblicazione():
    while True:
        try:
            prodotto = ottieni_prossimo_prodotto()
            if prodotto:
                print(f"[PUBBLICAZIONE] Invio: {prodotto['titolo']}")
                await invia_offerta(prodotto)
                segna_inviato(prodotto["asin"])
                await asyncio.sleep(INTERVALLO_MINUTI * 60)
            else:
                print("[PUBBLICAZIONE] Nessun prodotto in coda. Attendo 2 minuti...")
                await asyncio.sleep(120)
        except Exception as e:
            print(f"[ERRORE LOOP]: {e}")
            await asyncio.sleep(60)

async def main():
    init_db()
    # Avvia in parallelo il Canale Spia e il Loop di Pubblicazione
    await asyncio.gather(
        avvia_canale_spia(),
        ciclo_pubblicazione()
    )

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
