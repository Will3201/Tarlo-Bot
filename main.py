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
from telethon.sessions import StringSession

# ============================================================
# CONFIGURAZIONE
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU")
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID", "@TarloDelRisparmio")
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelrispa-21")
INTERVALLO_MINUTI = int(os.getenv("INTERVALLO_MINUTI", "5"))

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "31134748"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "ba4265cff56d0687c6c5171b47f76e02")

# I tuoi canali spia originali
CANALI_SPIA = [
    "sparky_offerte",
    "AstroHouse_Casa_Cucina",
    "ultimaofferta",
    "offerte5"
]

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# ============================================================
# DATABASE SQLITE CON FILTRO ANTI-DUPLICATI (24 ORE)
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

def prodotto_inviato_recentemente(asin, ore=24):
    """Controlla se il prodotto è attualmente in coda o se è stato già inviato nelle ultime 'ore' (24h)."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT inviato_il FROM prodotti WHERE asin = ?", (asin,))
        row = cursor.fetchone()
        
        if not row:
            return False  # Mai visto prima
            
        inviato_il = row[0]
        if inviato_il is None:
            return True  # È già in coda di attesa per la pubblicazione
            
        # Controlla se è stato inviato nelle ultime 24 ore
        if isinstance(inviato_il, str):
            inviato_dt = datetime.fromisoformat(inviato_il)
        else:
            inviato_dt = inviato_il
            
        if datetime.now() - inviato_dt < timedelta(hours=ore):
            return True  # Inviato di recente (meno di 24 ore fa)
            
        return False  # Passate più di 24 ore, può essere ripubblicato

def aggiungi_prodotto_db(p):
    if prodotto_inviato_recentemente(p["asin"], ore=24):
        print(f"[DB] ASIN {p['asin']} già inviato nelle ultime 24h o già in coda, ignorato.")
        return False

    with sqlite3.connect(DB_PATH) as conn:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO prodotti 
            (asin, titolo, sconto, prezzo_attuale, prezzo_precedente, immagine_url, inserito_il, inviato_il)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            p["asin"], p["titolo"], p.get("sconto", 0),
            p["prezzo_attuale"], p["prezzo_precedente"],
            p["immagine_url"], datetime.now()
        ))
        conn.commit()
        return True

def ottieni_prossimo_prodotto():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM prodotti WHERE inviato_il IS NULL ORDER BY inserito_il ASC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None

def segna_inviato(asin):
    with sqlite3.connect(DB_PATH) as conn:
        conn.cursor().execute("UPDATE prodotti SET inviato_il = ? WHERE asin = ?", (datetime.now().isoformat(), asin))
        conn.commit()

# ============================================================
# SCRAPER AMAZON
# ============================================================
def estrai_asin(testo):
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    if not match:
        match = re.search(r'\b(B[0-9A-Z]{9})\b', testo)
    return match.group(1) if match else None

def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return None
            
        soup = BeautifulSoup(res.text, "html.parser")
        
        titolo_elem = soup.find("span", {"id": "productTitle"})
        if not titolo_elem:
            return None
        titolo = titolo_elem.get_text().strip()

        prezzo_elem = soup.find("span", {"class": "a-price-whole"})
        frazione_elem = soup.find("span", {"class": "a-price-fraction"})
        if not prezzo_elem:
            return None
        prezzo_attuale = f"{prezzo_elem.get_text().strip().replace(',', '')},{frazione_elem.get_text().strip() if frazione_elem else '00'}"

        vecchio_elem = soup.find("span", {"class": "a-price a-text-price"})
        prezzo_precedente = prezzo_attuale
        if vecchio_elem:
            v_span = vecchio_elem.find("span", {"class": "a-offscreen"})
            if v_span:
                prezzo_precedente = v_span.get_text().replace("€", "").strip()

        img_elem = soup.find("img", {"id": "landingImage"})
        img_url = img_elem["src"] if img_elem else ""

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
    except Exception as e:
        print(f"[ERRORE SCRAPING ASIN {asin}]: {e}")
        return None

# ============================================================
# CANALE SPIA
# ============================================================
async def avvia_canale_spia():
    session_string = os.getenv("TELEGRAM_SESSION_STRING", "").strip()
    
    if not session_string:
        print("[ERRORE SPIA] TELEGRAM_SESSION_STRING mancante o non configurata su Render!")
        return

    client = TelegramClient(StringSession(session_string), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    
    try:
        await client.connect()

        if not await client.is_user_authorized():
            print("[ERRORE SPIA] La TELEGRAM_SESSION_STRING non è valida o è scaduta!")
            await client.disconnect()
            return

        canali_validi = []
        for canale in CANALI_SPIA:
            try:
                entity = await client.get_entity(canale)
                canali_validi.append(entity)
                print(f"[SPIA] Canale agganciato: @{canale}")
            except Exception as e:
                print(f"[SPIA WARNING] Errore nell'aggancio a @{canale}: {e}")

        if not canali_validi:
            print("[ERRORE SPIA] Nessun canale spia valido trovato nella lista!")
            return

        print(f"[SPIA] Connesso con successo a {len(canali_validi)} canali Telegram!")

        @client.on(events.NewMessage(chats=canali_validi))
        async def gestisci_nuovo_messaggio(event):
            testo = event.message.text or ""
            urls = re.findall(r'https?://[^\s]+', testo)
            
            if event.message.buttons:
                for row in event.message.buttons:
                    for btn in row:
                        if btn.url:
                            urls.append(btn.url)

            for url in urls:
                asin = estrai_asin(url)
                if asin:
                    if prodotto_inviato_recentemente(asin, ore=24):
                        print(f"[SPIA] ASIN {asin} inviato nelle ultime 24h, salto.")
                        break

                    print(f"[SPIA] Intercettato NUOVO ASIN: {asin}")
                    prodotto = await asyncio.to_thread(scarica_dettagli_amazon, asin)
                    if prodotto:
                        if aggiungi_prodotto_db(prodotto):
                            print(f"[SPIA] Aggiunto al DB: {prodotto['titolo']}")
                    break

        await client.run_until_disconnected()
    except Exception as e:
        print(f"[ERRORE SPIA]: {e}")

# ============================================================
# FUNZIONI GRAFICHE PILLOW
# ============================================================
def carica_font(dimensione, grassetto=False):
    percorsi = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if grassetto else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if grassetto else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in percorsi:
        if Path(p).exists():
            return ImageFont.truetype(p, dimensione)
    try:
        return ImageFont.load_default(size=dimensione)
    except TypeError:
        return ImageFont.load_default()

def spezza_testo(draw, testo, font, larghezza_massima):
    parole = str(testo).split()
    righe, riga = [], ""
    for parola in parole:
        prova = f"{riga} {parola}".strip()
        if draw.textbbox((0, 0), prova, font=font)[2] <= larghezza_massima:
            riga = prova
        else:
            if riga: righe.append(riga)
            riga = parola
    if riga: righe.append(riga)
    return righe

def scarica_immagine(url):
    if not url: return None
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    res.raise_for_status()
    return Image.open(BytesIO(res.content)).convert("RGBA")

def centra_testo(draw, testo, box, font, colore):
    x1, y1, x2, y2 = box
    bbox = draw.textbbox((0, 0), testo, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = x1 + ((x2 - x1) - w) // 2
    y = y1 + ((y2 - y1) - h) // 2 - bbox[1]
    draw.text((x, y), testo, fill=colore, font=font)

def crea_immagine_offerta(prodotto):
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError("File template.png non trovato nel repository")

    template = Image.open(TEMPLATE_PATH).convert("RGBA").resize((1536, 1536), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(template)

    try:
        foto = scarica_immagine(str(prodotto.get("immagine_url", "")))
        if foto:
            foto = ImageOps.contain(foto, (690, 850), method=Image.Resampling.LANCZOS)
            template.paste(foto, (425 - foto.width // 2, 805 - foto.height // 2), foto)
    except Exception as e:
        print(f"Errore caricamento immagine prodotto: {e}")

    titolo = str(prodotto.get("titolo", "")).strip()
    box_titolo = (885, 355, 1450, 600)
    w_box, h_box = box_titolo[2] - box_titolo[0], box_titolo[3] - box_titolo[1]
    dim = 62
    righe = []
    
    while dim >= 30:
        font_t = carica_font(dim, grassetto=True)
        righe = spezza_testo(draw, titolo, font_t, w_box - 30)
        h_riga = int(dim * 1.12)
        if len(righe) <= 4 and (len(righe) * h_riga) <= h_box - 20:
            break
        dim -= 2

    righe = righe[:4]
    y_t = box_titolo[1] + (h_box - (len(righe) * int(dim * 1.12))) // 2
    for riga in righe:
        font_t = carica_font(dim, grassetto=True)
        w_riga = draw.textbbox((0, 0), riga, font=font_t)[2] - draw.textbbox((0, 0), riga, font=font_t)[0]
        draw.text((box_titolo[0] + (w_box - w_riga) // 2, y_t), riga, fill="white", font=font_t)
        y_t += int(dim * 1.12)

    p_att = str(prodotto.get("prezzo_attuale", "")).replace("€", "").strip()
    f_att = carica_font(76, grassetto=True)
    centra_testo(draw, p_att, (1078, 1048, 1438, 1158), f_att, "#0D4B35")

    p_prec = str(prodotto.get("prezzo_precedente", "")).replace("€", "").strip()
    f_prec = carica_font(40, grassetto=True)
    centra_testo(draw, p_prec, (1225, 1214, 1442, 1286), f_prec, "#143D2D")

    bbox_v = draw.textbbox((0, 0), p_prec, font=f_prec)
    w_v = bbox_v[2] - bbox_v[0]
    draw.line((1333 - w_v // 2 - 7, 1250, 1333 + w_v // 2 + 7, 1250), fill="#E23B27", width=6)

    template.convert("RGB").save(OUTPUT_PATH, "PNG", optimize=True)
    return OUTPUT_PATH

# ============================================================
# SERVER FLASK & INVIO TELEGRAM
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

async def invia_offerta(prodotto):
    link = f"https://www.amazon.it/dp/{prodotto['asin']}?tag={AMAZON_TAG}"
    foto = crea_immagine_offerta(prodotto)

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
                print(f"[PUBBLICAZIONE] Inviato ASIN: {prodotto['asin']}. Bloccato per le prossime 24 ore.")
                await asyncio.sleep(INTERVALLO_MINUTI * 60)
            else:
                print("[PUBBLICAZIONE] Nessun nuovo prodotto in coda. Attendo 2 minuti...")
                await asyncio.sleep(120)
        except Exception as e:
            print(f"[ERRORE LOOP]: {e}")
            await asyncio.sleep(60)

async def main():
    init_db()
    await asyncio.gather(
        avvia_canale_spia(),
        ciclo_pubblicazione()
    )

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
    
