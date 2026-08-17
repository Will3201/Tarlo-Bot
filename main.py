import asyncio
import html
import os
import re
import sqlite3
import threading
import traceback
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

WEBHOOK_TIKTOK_URL = os.getenv(
    "WEBHOOK_TIKTOK_URL", 
    "https://hook.eu1.make.com/sex4ialbchbtuuja1jzdo3yhgzef42dt"
)

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "31134748"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "ba4265cff56d0687c6c5171b47f76e02")

CANALI_SPIA = [
    "sparky_offerte",
    "AstroHouse_Casa_Cucina",
    "ultimaofferta",
    "offerte5"
]

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
TEMPLATE_TIKTOK_PATH = BASE_DIR / "template_tiktok.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
OUTPUT_TIKTOK_PATH = BASE_DIR / "offerta_tiktok.png"
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
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT inviato_il FROM prodotti WHERE asin = ?", (asin,))
        row = cursor.fetchone()
        
        if not row:
            return False
            
        inviato_il = row[0]
        if inviato_il is None:
            return True
            
        if isinstance(inviato_il, str):
            inviato_dt = datetime.fromisoformat(inviato_il)
        else:
            inviato_dt = inviato_il
            
        return datetime.now() - inviato_dt < timedelta(hours=ore)

def aggiungi_prodotto_db(p):
    if prodotto_inviato_recentemente(p["asin"], ore=24):
        print(f"[DB] ASIN {p['asin']} già inviato nelle ultime 24h o in coda, ignorato.")
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
        print(f"[DB] Prodotto salvato in coda: {p['asin']}")
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
# SCRAPER AMAZON AVANZATO
# ============================================================
def estrai_asin(testo):
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    if not match:
        match = re.search(r'\b(B[0-9A-Z]{9})\b', testo)
    return match.group(1) if match else None

def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code != 200:
            return None
            
        soup = BeautifulSoup(res.text, "html.parser")
        
        titolo_elem = soup.find("span", {"id": "productTitle"})
        if not titolo_elem:
            return None
        titolo = titolo_elem.get_text().strip()

        # 1. Prezzo Attuale
        prezzo_attuale = None
        p_elem = soup.find("span", class_="a-price")
        if p_elem:
            off_elem = p_elem.find("span", class_="a-offscreen")
            if off_elem:
                txt = off_elem.get_text().replace("€", "").strip().replace(".", ",")
                m = re.search(r'(\d+[\.,]\d{2})', txt)
                if m:
                    prezzo_attuale = m.group(1).replace('.', ',')

        if not prezzo_attuale:
            pw = soup.find("span", class_="a-price-whole")
            pf = soup.find("span", class_="a-price-fraction")
            if pw:
                p_w = re.sub(r'[^\d]', '', pw.get_text())
                p_f = pf.get_text().strip() if pf else "00"
                prezzo_attuale = f"{p_w},{p_f}"

        if not prezzo_attuale:
            return None

        p_att_num = float(prezzo_attuale.replace('.', '').replace(',', '.'))

        # 2. Prezzo Precedente / Listino
        prezzo_precedente = None
        candidati_prec = []

        for elem in soup.find_all("span", class_=re.compile(r"a-text-price|basisPrice|a-color-secondary|a-size-small")):
            off = elem.find("span", class_="a-offscreen")
            t = off.get_text() if off else elem.get_text()
            m = re.search(r'(\d+[\.,]\d{2})', t)
            if m:
                try:
                    v = float(m.group(1).replace('.', '').replace(',', '.'))
                    if v > p_att_num:
                        candidati_prec.append((v, m.group(1).replace('.', ',')))
                except ValueError:
                    pass

        if candidati_prec:
            candidati_prec.sort(key=lambda x: x[0], reverse=True)
            prezzo_precedente = candidati_prec[0][1]

        # 3. Calcolo Sconto
        if prezzo_precedente:
            p_prec_num = float(prezzo_precedente.replace('.', '').replace(',', '.'))
            sconto = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
        else:
            prezzo_precedente = prezzo_attuale
            sconto = 0

        # 4. Immagine
        img_elem = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"id": "imgBlkFront"})
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
        print(f"[ERRORE SCRAPING ASIN {asin}]: {e}")
        return None

# ============================================================
# CANALE SPIA
# ============================================================
async def avvia_canale_spia():
    session_string = os.getenv("TELEGRAM_SESSION_STRING", "").strip()
    if not session_string:
        print("[SPIA] TELEGRAM_SESSION_STRING non trovata, canale spia disattivato.")
        return

    client = TelegramClient(StringSession(session_string), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        if not await client.is_user_authorized():
            await client.disconnect()
            print("[SPIA] Sessione Telegram non autorizzata.")
            return

        canali_validi = []
        for canale in CANALI_SPIA:
            try:
                entity = await client.get_entity(canale)
                canali_validi.append(entity)
            except Exception as e:
                print(f"[SPIA WARNING] Errore su @{canale}: {e}")

        if not canali_validi:
            return

        print(f"[SPIA] Monitoraggio attivo su {len(canali_validi)} canali spia.")

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
                        break

                    prodotto = await asyncio.to_thread(scarica_dettagli_amazon, asin)
                    if prodotto:
                        aggiungi_prodotto_db(prodotto)
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
        raise FileNotFoundError("File template.png non trovato")

    template = Image.open(TEMPLATE_PATH).convert("RGBA").resize((1536, 1536), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(template)

    try:
        foto = scarica_immagine(str(prodotto.get("immagine_url", "")))
        if foto:
            foto = ImageOps.contain(foto, (690, 850), method=Image.Resampling.LANCZOS)
            template.paste(foto, (425 - foto.width // 2, 805 - foto.height // 2), foto)
    except Exception as e:
        print(f"Errore caricamento immagine prodotto: {e}")

    sconto = prodotto.get("sconto", 0)
    
    # 1. Gestione Badge Sconto
    if sconto > 0:
        f_badge = carica_font(46, grassetto=True)
        centra_testo(draw, f"-{sconto}%", (160, 245, 290, 315), f_badge, "#FFFFFF")
    else:
        draw.rectangle((140, 230, 310, 330), fill="#123E2E")

    # 2. Titolo
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

    # 3. Prezzo Attuale
    p_att = str(prodotto.get("prezzo_attuale", "")).replace("€", "").strip()
    f_att = carica_font(76, grassetto=True)
    centra_testo(draw, p_att, (1078, 1048, 1438, 1158), f_att, "#0D4B35")

    # 4. Prezzo Precedente / Copertura
    p_prec = str(prodotto.get("prezzo_precedente", "")).replace("€", "").strip()
    if sconto > 0 and p_prec != p_att:
        f_prec = carica_font(40, grassetto=True)
        centra_testo(draw, p_prec, (1225, 1214, 1442, 1286), f_prec, "#143D2D")
        bbox_v = draw.textbbox((0, 0), p_prec, font=f_prec)
        w_v = bbox_v[2] - bbox_v[0]
        draw.line((1333 - w_v // 2 - 7, 1250, 1333 + w_v // 2 + 7, 1250), fill="#E23B27", width=6)
    else:
        draw.rectangle((1050, 1190, 1460, 1300), fill="#123E2E")

    template.convert("RGB").save(OUTPUT_PATH, "PNG", optimize=True)
    return OUTPUT_PATH

def crea_immagine_tiktok(prodotto):
    if not TEMPLATE_TIKTOK_PATH.exists():
        if TEMPLATE_PATH.exists():
            img_main = Image.open(crea_immagine_offerta(prodotto))
            img_tiktok = Image.new("RGB", (1080, 1920), "#0A291E")
            img_main_resized = ImageOps.contain(img_main, (1000, 1000))
            img_tiktok.paste(img_main_resized, (40, 460))
            img_tiktok.save(OUTPUT_TIKTOK_PATH, "PNG")
            return OUTPUT_TIKTOK_PATH
        return None

    template = Image.open(TEMPLATE_TIKTOK_PATH).convert("RGBA").resize((1080, 1920), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(template)

    try:
        foto = scarica_immagine(str(prodotto.get("immagine_url", "")))
        if foto:
            foto = ImageOps.contain(foto, (820, 780), method=Image.Resampling.LANCZOS)
            template.paste(foto, (540 - foto.width // 2, 870 - foto.height // 2), foto)
    except Exception as e:
        print(f"Errore foto TikTok: {e}")

    sconto = prodotto.get("sconto", 0)
    if sconto > 0:
        f_badge = carica_font(52, grassetto=True)
        centra_testo(draw, f"-{sconto}%", (215, 130, 385, 205), f_badge, "#FFFFFF")

    titolo = str(prodotto.get("titolo", "")).strip()
    box_titolo = (110, 1325, 970, 1455)
    w_box, h_box = box_titolo[2] - box_titolo[0], box_titolo[3] - box_titolo[1]
    dim = 42
    righe = []
    
    while dim >= 22:
        font_t = carica_font(dim, grassetto=True)
        righe = spezza_testo(draw, titolo, font_t, w_box - 20)
        h_riga = int(dim * 1.12)
        if len(righe) <= 2 and (len(righe) * h_riga) <= h_box - 10:
            break
        dim -= 2

    righe = righe[:2]
    y_t = box_titolo[1] + (h_box - (len(righe) * int(dim * 1.12))) // 2
    for riga in righe:
        font_t = carica_font(dim, grassetto=True)
        w_riga = draw.textbbox((0, 0), riga, font=font_t)[2] - draw.textbbox((0, 0), riga, font=font_t)[0]
        draw.text((box_titolo[0] + (w_box - w_riga) // 2, y_t), riga, fill="white", font=font_t)
        y_t += int(dim * 1.12)

    p_att = f"{str(prodotto.get('prezzo_attuale', '')).replace('€', '').strip()} €"
    f_att = carica_font(68, grassetto=True)
    centra_testo(draw, p_att, (75, 1470, 635, 1720), f_att, "#FFFFFF")

    p_prec = f"{str(prodotto.get('prezzo_precedente', '')).replace('€', '').strip()} €"
    if sconto > 0 and p_prec != p_att:
        f_prec = carica_font(48, grassetto=True)
        centra_testo(draw, p_prec, (655, 1470, 980, 1720), f_prec, "#333333")
        bbox_v = draw.textbbox((0, 0), p_prec, font=f_prec)
        w_v = bbox_v[2] - bbox_v[0]
        centx = 655 + (980 - 655) // 2
        draw.line((centx - w_v // 2 - 5, 1595, centx + w_v // 2 + 5, 1595), fill="#E23B27", width=6)

    template.convert("RGB").save(OUTPUT_TIKTOK_PATH, "PNG", optimize=True)
    return OUTPUT_TIKTOK_PATH

# ============================================================
# INVIO WEBHOOK TIKTOK
# ============================================================
def invia_webhook_tiktok(prodotto, foto_tiktok_path):
    if not WEBHOOK_TIKTOK_URL or not foto_tiktok_path or not Path(foto_tiktok_path).exists():
        print(f"[WEBHOOK TIKTOK WARNING] Immagine o URL non valido per ASIN {prodotto['asin']}")
        return

    link = f"https://www.amazon.it/dp/{prodotto['asin']}?tag={AMAZON_TAG}"
    
    data = {
        "asin": prodotto['asin'],
        "titolo": prodotto['titolo'],
        "prezzo_attuale": prodotto['prezzo_attuale'],
        "prezzo_precedente": prodotto['prezzo_precedente'],
        "sconto": str(prodotto['sconto']),
        "link": link,
        "didascalia": f"🔥 {prodotto['titolo']}\n💰 In offerta a soli {prodotto['prezzo_attuale']}€! Link nei commenti o in bio. #offerta #amazon #sconti"
    }

    try:
        with open(foto_tiktok_path, "rb") as f:
            files = {"file": ("offerta_tiktok.png", f, "image/png")}
            res = requests.post(WEBHOOK_TIKTOK_URL, data=data, files=files, timeout=20)
            if res.status_code in [200, 201]:
                print(f"[WEBHOOK TIKTOK] Inviato con successo per ASIN: {prodotto['asin']}")
            else:
                print(f"[WEBHOOK TIKTOK ERRORE]: Risposta {res.status_code} - {res.text}")
    except Exception as e:
        print(f"[WEBHOOK TIKTOK ERRORE EXCEPTION]: {e}")

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
    app.run(host="0.0.0.0", port=port, use_reloader=False)

async def invia_offerta(prodotto):
    link = f"https://www.amazon.it/dp/{prodotto['asin']}?tag={AMAZON_TAG}"
    foto = crea_immagine_offerta(prodotto)

    # Pre-elaborazione pulita per evitare sintassi errate
    titolo_esc = html.escape(str(prodotto['titolo']))
    p_att_esc = html.escape(str(prodotto['prezzo_attuale']))
    p_prec_esc = html.escape(str(prodotto['prezzo_precedente']))
    link_esc = html.escape(link, quote=True)
    sconto_val = prodotto['sconto']

    if sconto_val > 0 and prodotto['prezzo_precedente'] != prodotto['prezzo_attuale']:
        info_prezzo = (
            f"📉 Sconto: <b>-{sconto_val}%</b>\n"
            f"💰 <s>{p_prec_esc} €</s> ➜ <b>{p_att_esc} €</b>"
        )
    else:
        info_prezzo = f"💰 Prezzo speciale: <b>{p_att_esc} €</b>"

    didascalia = (
        "🐜 <b>Il Tarlo ha colpito ancora!</b>\n\n"
        f"📦 <b>{titolo_esc}</b>\n"
        f"{info_prezzo}\n\n"
        f'👉 <a href="{
