import asyncio
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

# Lista Canali Spia ampliata (Generali + Casa + Spesa/Igiene)
CANALI_SPIA = [
    "sparky_offerte", 
    "AstroHouse_Casa_Cucina", 
    "ultimaofferta", 
    "offerte5",
    "OfferteSpesaAmazon",      # Cibo e igiene
    "offerte_supermercato",    # Casa e prodotti quotidiani
    "SpesaScontata"            # Cura della persona e casa
]

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

# Nome del tuo file font
FONT_PATH = BASE_DIR / "Montserrat-Italic-VariableFont_wght.ttf"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

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

# --- SCRAPER AMAZON UNIVERSALE (Funziona anche per Cibo/Igiene/Casa) ---
def estrai_asin(testo):
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    return match.group(1) if match else None

def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(res.text, "html.parser")
        
        # 1. Titolo
        titolo_elem = soup.find("span", {"id": "productTitle"})
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"

        # 2. Prezzo Attuale (Tentativi multipli per cibo, spesa e casa)
        prezzo_attuale = None
        
        # Tentativo A: Blocco prezzo standard
        p_elem = soup.find("span", {"class": "a-price", "data-a-size": "xl"}) or soup.find("span", {"class": "a-price", "data-a-size": "l"})
        if p_elem:
            off_elem = p_elem.find("span", class_="a-offscreen")
            if off_elem:
                prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        # Tentativo B: Blocco prezzo Apex (usato spesso per prodotti da supermercato)
        if not prezzo_attuale:
            p_apex = soup.find("span", class_="apexPriceToPay") or soup.find("div", {"id": "corePrice_feature_div"})
            if p_apex:
                off_elem = p_apex.find("span", class_="a-offscreen")
                if off_elem:
                    prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        # Se non si trova il prezzo, scarta
        if not prezzo_attuale: 
            return None

        # Conversione sicura per calcoli
        p_att_clean = prezzo_attuale.replace(' ', '').replace('\xa0', '')
        if ',' in p_att_clean and '.' in p_att_clean:
            p_att_clean = p_att_clean.replace('.', '').replace(',', '.')
        else:
            p_att_clean = p_att_clean.replace(',', '.')
            
        p_att_num = float(p_att_clean)

        # 3. Prezzo Precedente e Sconto
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
                if 0 < sconto_calc <= 80: # Sanity check anti-errore
                    prezzo_precedente = val_strike.replace(".", ",")
                    sconto = sconto_calc

        # 4. Immagine Prodotto
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

# --- GENERAZIONE GRAFICA 1080x1080 ---
def crea_immagine(prodotto):
    # 1. Carica e forza la risoluzione a 1080x1080
    template = Image.open(TEMPLATE_PATH).convert("RGBA").resize((1080, 1080), Image.Resampling.LANCZOS)
    
    # 2. Incolla l'immagine del prodotto nel riquadro bianco a sinistra
    box_x, box_y = 23, 238
    box_w, box_h = 542, 778
    
    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            img_prod.thumbnail((box_w - 40, box_h - 40), Image.Resampling.LANCZOS)
            
            offset_x = box_x + (box_w - img_prod.width) // 2
            offset_y = box_y + (box_h - img_prod.height) // 2
            
            template.paste(img_prod, (offset_x, offset_y), img_prod)
        except Exception as e:
            print(f"[ERRORE INCOLLA IMMAGINE]: {e}")

    draw = ImageDraw.Draw(template)
    
    # 3. Caricamento Font Montserrat
    try:
        font_titolo = ImageFont.truetype(str(FONT_PATH), 26)
        font_prezzo_grande = ImageFont.truetype(str(FONT_PATH), 72)
        font_prezzo_vecchio = ImageFont.truetype(str(FONT_PATH), 38)
        font_sconto = ImageFont.truetype(str(FONT_PATH), 65)
    except Exception as e:
        print(f"[WARNING] Impossibile caricare {FONT_PATH.name}: {e}. Uso il font di default.")
        font_titolo = font_prezzo_grande = font_prezzo_vecchio = font_sconto = ImageFont.load_default()

    # --- A. TITOLO PRODOTTO (Etichetta Verde in alto a destra) ---
    titolo_breve = prodotto["titolo"][:42] + "..." if len(prodotto["titolo"]) > 42 else prodotto["titolo"]
    draw.text((760, 320), titolo_breve, fill="white", font=font_titolo, anchor="mm")

    # --- B. PREZZO ATTUALE (Box Arancione) ---
    testo_prezzo = f"{prodotto['prezzo_attuale']} €"
    draw.text((765, 555), testo_prezzo, fill="white", font=font_prezzo_grande, anchor="mm")

    # --- C. PREZZO BARRATO (Riquadro Chiaro) ---
    if prodotto["prezzo_precedente"] and prodotto["prezzo_precedente"] != prodotto["prezzo_attuale"]:
        testo_vecchio = f"{prodotto['prezzo_precedente']} €"
        draw.text((765, 720), testo_vecchio, fill="#555555", font=font_prezzo_vecchio, anchor="mm")
        
        # Disegna la linea rossa sopra il prezzo vecchio
        bbox = draw.textbbox((765, 720), testo_vecchio, font=font_prezzo_vecchio, anchor="mm")
        draw.line([(bbox[0] - 5, 720), (bbox[2] + 5, 720)], fill="#D32F2F", width=4)

    # --- D. PERCENTUALE SCONTO (Banner arancione in basso a destra) ---
    if prodotto["sconto"] > 0:
        testo_sconto = f"-{prodotto['sconto']}%"
        draw.text((810, 855), testo_sconto, fill="white", font=font_sconto, anchor="mm")

    # 4. Salva il file finale
    template.convert("RGB").save(OUTPUT_PATH, "PNG")
    return OUTPUT_PATH

# --- BOT TELEGRAM ---
async def main():
    init_db()
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()

    @client.on(events.NewMessage(chats=CANALI_SPIA))
    async def handler(event):
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

    print("Bot Il Tarlo del Risparmio avviato con successo...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    asyncio.run(main())
    
