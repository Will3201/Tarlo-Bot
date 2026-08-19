import asyncio
import os
import re
import sqlite3
import textwrap
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask
from PIL import Image, ImageDraw, ImageFont
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

# Porta per Render
PORT = int(os.getenv("PORT", 10000))

# Lista Canali Spia
CANALI_SPIA = [
    "sparky_offerte", 
    "AstroHouse_Casa_Cucina", 
    "ultimaofferta", 
    "offerte5",
    "offerte_supermercato", 
    "SpesaScontata"
]

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

# Nome del tuo file font
FONT_PATH = BASE_DIR / "Montserrat-Italic-VariableFont_wght.ttf"

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

# --- SCRAPER AMAZON UNIVERSALE ---
def estrai_asin(testo):
    if not testo:
        return None
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    return match.group(1) if match else None

def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
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

        p_att_clean = prezzo_attuale.replace(' ', '').replace('\xa0', '')
        if ',' in p_att_clean and '.' in p_att_clean:
            p_att_clean = p_att_clean.replace('.', '').replace(',', '.')
        else:
            p_att_clean = p_att_clean.replace(',', '.')
            
        p_att_num = float(p_att_clean)

        sconto = 0
        prezzo_precedente = prezzo_attuale
        p_strike = soup.find("span", class_="a-text-strike")
        
        if p_strike:
            val_strike = p_strike.get_text().replace("€", "").strip()
            p_prec_clean = val_strike.replace(' ', '').replace('\xa0', '')
            if ',' in p_prec_clean and '.' in p_prec_clean:
                p_prec_clean = p_prec_clean.replace('.', '').replace(',', '.')
            else:
                p_prec_clean = p_prec_clean.replace(',', '.')
                
            p_prec_num = float(p_prec_clean)
            
            if p_prec_num > p_att_num:
                sconto_calc = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                if 0 < sconto_calc <= 80:
                    prezzo_precedente = val_strike.replace(".", ",")
                    sconto = sconto_calc

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

# --- GENERAZIONE GRAFICA ---
def crea_immagine(prodotto):
    template = Image.open(TEMPLATE_PATH).convert("RGBA").resize((1080, 1080), Image.Resampling.LANCZOS)
    
    box_x, box_y = 23, 238
    box_w, box_h = 542, 778
    
    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            img_prod.thumbnail((box_w - 60, box_h - 60), Image.Resampling.LANCZOS)
            
            offset_x = box_x + (box_w - img_prod.width) // 2
            offset_y = box_y + (box_h - img_prod.height) // 2
            
            template.paste(img_prod, (offset_x, offset_y), img_prod)
        except Exception as e:
            print(f"[ERRORE INCOLLA IMMAGINE]: {e}")

    draw = ImageDraw.Draw(template)
    
    # Caricamento Font in modo sicuro
    try:
        font_titolo = ImageFont.truetype(str(FONT_PATH), 28)
        font_prezzo_grande = ImageFont.truetype(str(FONT_PATH), 75)
        font_prezzo_vecchio = ImageFont.truetype(str(FONT_PATH), 38)
        font_sconto = ImageFont.truetype(str(FONT_PATH), 60)
        
        try:
            font_titolo.set_variation_by_name('Bold')
            font_prezzo_grande.set_variation_by_name('Bold')
            font_prezzo_vecchio.set_variation_by_name('Bold')
            font_sconto.set_variation_by_name('Bold')
        except Exception:
            pass
    except Exception as e:
        print(f"[WARNING] Impossibile caricare {FONT_PATH.name}: {e}")
        font_titolo = font_prezzo_grande = font_prezzo_vecchio = font_sconto = ImageFont.load_default()

    # A. Titolo Prodotto
    titolo_pulito = prodotto["titolo"][:60]
    righe = textwrap.wrap(titolo_pulito, width=20)
    y_titolo = 310 - (len(righe) * 15)
    for riga in righe[:3]:
        draw.text((720, y_titolo), riga, fill=(255, 255, 255, 255), font=font_titolo, anchor="mm")
        y_titolo += 34

    # B. Prezzo Attuale
    testo_prezzo = f"{prodotto['prezzo_attuale']} €"
    draw.text((765, 550), testo_prezzo, fill=(255, 255, 255, 255), font=font_prezzo_grande, anchor="mm")

    # C. Prezzo Barrato
    if prodotto["prezzo_precedente"] and prodotto["prezzo_precedente"] != prodotto["prezzo_attuale"]:
        testo_vecchio = f"{prodotto['prezzo_precedente']} €"
        center_x, center_y = 765, 718
        
        draw.text((center_x, center_y), testo_vecchio, fill=(100, 100, 100, 255), font=font_prezzo_vecchio, anchor="mm")
        bbox = draw.textbbox((center_x, center_y), testo_vecchio, font=font_prezzo_vecchio, anchor="mm")
        draw.line([(bbox[0] - 8, center_y), (bbox[2] + 8, center_y)], fill=(211, 47, 47, 255), width=4)

    # D. Percentuale Sconto
    if prodotto["sconto"] > 0:
        testo_sconto = f"-{prodotto['sconto']}%"
        draw.text((820, 858), testo_sconto, fill=(255, 255, 255, 255), font=font_sconto, anchor="mm")

    template.convert("RGB").save(OUTPUT_PATH, "PNG")
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
                        didascalia = (
                            f"🪵 **{p['titolo']}**\n\n"
                            f"💰 **Prezzo speciale:** {p['prezzo_attuale']} €\n"
                            f"👉 **Acquista ora:** https://amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}\n\n"
                            f"#IlTarloDelRisparmio"
                        )
                        await bot.send_photo(chat_id=CANALE_CHAT_ID, photo=open(foto, "rb"), caption=didascalia, parse_mode="Markdown")
        except Exception as e:
            print(f"Errore gestione messaggio: {e}")

    print("Bot Il Tarlo del Risparmio avviato...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    # Avvia Flask sulla porta assegnata da Render
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
    
