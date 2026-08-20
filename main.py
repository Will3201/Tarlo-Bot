import asyncio
import os
import re
import sqlite3
import threading
import urllib.request
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import cairosvg
import requests
from bs4 import BeautifulSoup
from flask import Flask
from PIL import Image
from telegram import Bot
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# --- CONFIGURAZIONE ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU")
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID", "@TarloDelRisparmio")
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelrispa-21")
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "31134748"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "ba4265cff56d0687c6c5171b47f76e02")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING", "")

PORT = int(os.getenv("PORT", 10000))

CANALI_SPIA = [
    "sparky_offerte", 
    "AstroHouse_Casa_Cucina", 
    "ultimaofferta", 
    "offerte5",
    "offerte_supermercato", 
    "SpesaScontata"
    "provawill32"
]

BASE_DIR = Path(__file__).resolve().parent
SVG_TEMPLATE_PATH = BASE_DIR / "template.svg"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# --- WEB SERVER PER RENDER ---
@app.route("/")
def home():
    return "Bot Online", 200

# --- DATABASE ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                asin TEXT PRIMARY KEY,
                inviato_il DATETIME
            )
        """)

def gia_inviato(asin):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT inviato_il FROM prodotti WHERE asin = ?", (asin,))
        row = cursor.fetchone()
        if not row: return False
        inviato_dt = datetime.fromisoformat(row[0])
        return datetime.now() - inviato_dt < timedelta(hours=24)

def segna_inviato(asin):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO prodotti (asin, inviato_il) VALUES (?, ?)", 
                     (asin, datetime.now().isoformat()))

# --- ESTRAZIONE ASIN ---
def estrai_asin(testo):
    if not testo:
        return None
        
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    if match:
        return match.group(1)
        
    urls = re.findall(r'https?://[^\s]+', testo)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    for url in urls:
        try:
            res = requests.head(url, allow_redirects=True, timeout=5, headers=headers)
            match_redirect = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
            if match_redirect:
                return match_redirect.group(1)
        except Exception as e:
            print(f"Errore risoluzione link corto {url}: {e}")
            
    return None

# --- SCRAPER AMAZON ---
def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    try:
        res = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(res.text, "html.parser")
        
        titolo_elem = soup.find("span", {"id": "productTitle"})
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"

        prezzo_attuale = None
        p_elem = soup.find("span", {"class": "a-price", "data-a-size": "xl"}) or soup.find("span", {"class": "a-price", "data-a-size": "l"})
        if p_elem:
            off_elem = p_elem.find("span", class_="a-offscreen")
            if off_elem:
                prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        if not prezzo_attuale:
            p_apex = soup.find("span", class_="apexPriceToPay") or soup.find("div", {"id": "corePrice_feature_div"})
            if p_apex:
                off_elem = p_apex.find("span", class_="a-offscreen")
                if off_elem:
                    prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        if not prezzo_attuale: 
            return None

        p_att_clean = re.sub(r'[^\d,]', '', prezzo_attuale).replace(',', '.')
        p_att_num = float(p_att_clean)

        sconto = 0
        prezzo_precedente = None
        
        strike_elem = (
            soup.find("span", class_="a-text-strike") or 
            soup.find("span", class_="a-text-price") or
            soup.find("span", {"id": "listPrice"})
        )
        
        if strike_elem:
            off_strike = strike_elem.find("span", class_="a-offscreen")
            val_strike = off_strike.get_text() if off_strike else strike_elem.get_text()
            val_strike_clean = re.sub(r'[^\d,]', '', val_strike).replace(',', '.')
            try:
                p_prec_num = float(val_strike_clean)
                if p_prec_num > p_att_num:
                    sconto_calc = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                    if 0 < sconto_calc <= 85:
                        prezzo_precedente = f"{p_prec_num:.2f}".replace(".", ",")
                        sconto = sconto_calc
            except ValueError:
                pass

        img_elem = (
            soup.find("img", {"id": "landingImage"}) or 
            soup.find("img", {"id": "imgBlkFront"}) or 
            soup.find("img", {"class": "a-dynamic-image"})
        )
        img_url = img_elem["src"] if img_elem else ""

        return {
            "asin": asin, 
            "titolo": titolo, 
            "prezzo_attuale": prezzo_attuale,
            "prezzo_precedente": prezzo_precedente,
            "sconto": sconto, 
            "immagine_url": img_url
        }
    except Exception as e:
        print(f"Errore scraping ASIN {asin}: {e}")
        return None

# --- GENERAZIONE GRAFICA TRAMITE CANVA SVG ---
def crea_immagine(prodotto):
    # 1. Leggi il modello SVG generato da Canva
    with open(SVG_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        svg_code = f.read()

    # Prepara le stringhe per la sostituzione
    titolo_breve = prodotto["titolo"][:42]
    prezzo_att = f"{prodotto['prezzo_attuale']} €"
    prezzo_vec = f"{prodotto['prezzo_precedente']} €" if prodotto.get("prezzo_precedente") else ""
    sconto_txt = f"-{prodotto['sconto']}%" if prodotto.get("sconto") and prodotto["sconto"] > 0 else ""

    # 2. Sostituisci i segnaposto di Canva con i dati reali
    svg_modificato = (
        svg_code.replace("TITOLO_PRODOTTO", titolo_breve)
                .replace("PREZZO_ATTUALE", prezzo_att)
                .replace("PREZZO_VECCHIO", prezzo_vec)
                .replace("PERCENTUALE_SCONTO", sconto_txt)
    )

    # 3. Converti l'SVG in un'immagine PNG usando CairoSVG
    cairosvg.svg2png(bytestring=svg_modificato.encode('utf-8'), write_to=str(OUTPUT_PATH))
    
    # 4. Incolla l'immagine del prodotto a sinistra usando Pillow
    if prodotto.get("immagine_url"):
        try:
            base_img = Image.open(OUTPUT_PATH).convert("RGBA")
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            
            box_x, box_y = 25, 240
            box_w, box_h = 510, 770
            
            img_prod.thumbnail((box_w - 40, box_h - 40), Image.Resampling.LANCZOS)
            offset_x = box_x + (box_w - img_prod.width) // 2
            offset_y = box_y + (box_h - img_prod.height) // 2
            
            base_img.paste(img_prod, (offset_x, offset_y), img_prod)
            base_img.convert("RGB").save(OUTPUT_PATH, "PNG")
        except Exception as e:
            print(f"[ERRORE INCOLLA IMMAGINE PRODOTTO]: {e}")

    return OUTPUT_PATH

# --- BOT TELEGRAM ---
async def main():
    init_db()
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()

    @client.on(events.NewMessage())
    async def handler(event):
        try:
            chat = await event.get_chat()
            chat_username = getattr(chat, 'username', '') or ''
            
            if chat_username.lower() in [c.lower() for c in CANALI_SPIA]:
                asin = estrai_asin(event.message.text)
                if asin and not gia_inviato(asin):
                    p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
                    if p:
                        segna_inviato(asin)
                        foto = crea_immagine(p)
                        
                        url_affiliato = f"https://www.amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}"
                        
                        didascalia = "🪵 **Il Tarlo ha colpito ancora!**\n\n"
                        didascalia += f"📦 **{p['titolo']}**\n"
                        
                        if p['sconto'] > 0 and p['prezzo_precedente']:
                            didascalia += f"📉 **Sconto:** -{p['sconto']}%\n"
                            didascalia += f"💰 ~~{p['prezzo_precedente']} €~~ ➔ **{p['prezzo_attuale']} €**\n\n"
                        else:
                            didascalia += f"💰 **Prezzo speciale:** {p['prezzo_attuale']} €\n\n"
                            
                        didascalia += f"👉 **[ACQUISTA SUBITO IN OFFERTA]({url_affiliato})**\n\n"
                        didascalia += "#IlTarloDelRisparmio"

                        await bot.send_photo(
                            chat_id=CANALE_CHAT_ID, 
                            photo=open(foto, "rb"), 
                            caption=didascalia, 
                            parse_mode="Markdown"
                        )
        except Exception as e:
            print(f"Errore gestione messaggio: {e}")

    print("Bot Il Tarlo del Risparmio avviato...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
