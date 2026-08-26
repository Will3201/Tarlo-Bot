import asyncio
import functools
import os
import re
import sqlite3
try:
    import psycopg2
    PSYCOPG2_DISPONIBILE = True
except ImportError:
    PSYCOPG2_DISPONIBILE = False
import sys
import textwrap
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import cairosvg
import requests
from bs4 import BeautifulSoup
from flask import Flask
from PIL import Image, ImageDraw, ImageFont
from telegram import Bot

# Forza il flush immediato di ogni print(): su Render/hosting cloud lo stdout
# viene spesso bufferizzato quando non è un terminale interattivo, causando
# righe di log mancanti o ritardate. Questo garantisce che ogni riga di debug
# compaia subito nei log, nell'ordine corretto.
print = functools.partial(print, flush=True)
sys.stdout.reconfigure(line_buffering=True)

# --- CONFIGURAZIONE ---
# ATTENZIONE: sostituisci questi due placeholder:
# - TELEGRAM_TOKEN: nuovo token creato con @BotFather (bot separato dal generico)
# - CANALE_CHAT_ID: username del nuovo canale dedicato alla pesca
# Questo bot NON ha più bisogno di API_ID/API_HASH/SESSION_STRING: non ascolta
# altri canali, cerca direttamente su Amazon, quindi gli basta il solo token bot.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN_PESCA", "8879365110:AAH9i2KJSvffnHc-FfR8K35kL9oPTkPxiyk")
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID_PESCA", "@TarloDellaPesca")
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelrispa-21")

PORT = int(os.getenv("PORT_PESCA", 10001))  # porta diversa se sullo stesso host

BASE_DIR = Path(__file__).resolve().parent
SVG_TEMPLATE_PATH = BASE_DIR / "template_pesca.svg"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte_pesca.db"
# Se impostata (es. connection string di Neon/Postgres), il bot usa un DB
# persistente che sopravvive ai deploy. Se assente, usa SQLite locale come
# prima (funziona, ma si azzera ad ogni deploy su Render free tier).
DATABASE_URL = os.getenv("DATABASE_URL_PESCA", "")
USA_POSTGRES = bool(DATABASE_URL) and PSYCOPG2_DISPONIBILE

from telegram.request import HTTPXRequest

# Timeout generosi: l'istanza free di Render ha rete lenta e l'upload di una
# foto con i valori di default (5s) finisce spesso in telegram.error.TimedOut
_request = HTTPXRequest(
    connect_timeout=30.0,
    read_timeout=30.0,
    write_timeout=60.0,
    pool_timeout=30.0,
)
bot = Bot(token=TELEGRAM_TOKEN, request=_request)
app = Flask(__name__)

# --- RICERCA AUTOMATICA FONT ---
def carica_font_locale(size):
    font_files = list(BASE_DIR.rglob("*.ttf")) + list(BASE_DIR.rglob("*.otf"))
    if font_files:
        try:
            return ImageFont.truetype(str(font_files[0]), size)
        except Exception as e:
            print(f"[ERRORE CARICAMENTO FONT]: {e}")
    return ImageFont.load_default()

# --- HELPER: CONVERSIONE PREZZI ROBUSTA ---
def parse_prezzo(testo):
    if not testo: return None
    t = testo.replace("€", "").strip()
    t = re.sub(r'[^\d.,]', '', t)
    if not t: return None
    
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    else:
        if t.count('.') > 1:
            t = t.replace('.', '')
        elif t.count('.') == 1:
            parts = t.split('.')
            if len(parts[1]) == 3:
                t = t.replace('.', '')
    try:
        return float(t)
    except:
        return None

# --- HELPER: CENTRATURA TESTO PRECISA ---
def draw_centrato(draw, center_x, center_y, testo, font, fill, stroke_width=0, stroke_fill=None, align="center"):
    bbox = draw.multiline_textbbox(
        (0, 0), testo, font=font, stroke_width=stroke_width, align=align
    )
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = center_x - w / 2 - bbox[0]
    y = center_y - h / 2 - bbox[1]
    draw.multiline_text(
        (x, y), testo, fill=fill, font=font,
        align=align, stroke_width=stroke_width, stroke_fill=stroke_fill
    )
    return bbox, (x, y)

# --- WEB SERVER ---
@app.route("/")
def home():
    return "Bot Online", 200

# --- DATABASE (Postgres persistente se DATABASE_URL è configurata, altrimenti SQLite locale) ---
def init_db():
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS prodotti (
                        asin TEXT PRIMARY KEY,
                        inviato_il TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS titoli_inviati (
                        titolo_norm TEXT PRIMARY KEY,
                        inviato_il TIMESTAMP
                    )
                """)
            conn.commit()
        print("[DEBUG] Database: Postgres (persistente)")
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prodotti (
                    asin TEXT PRIMARY KEY,
                    inviato_il DATETIME
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS titoli_inviati (
                    titolo_norm TEXT PRIMARY KEY,
                    inviato_il DATETIME
                )
            """)
        print("[DEBUG] Database: SQLite locale (ATTENZIONE: si azzera ad ogni deploy su Render free)")

def gia_inviato(asin):
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT inviato_il FROM prodotti WHERE asin = %s", (asin,))
                row = cur.fetchone()
        if not row:
            return False
        return datetime.now() - row[0] < timedelta(hours=24)
    else:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT inviato_il FROM prodotti WHERE asin = ?", (asin,))
            row = cursor.fetchone()
        if not row:
            return False
        inviato_dt = datetime.fromisoformat(row[0])
        return datetime.now() - inviato_dt < timedelta(hours=24)

def segna_inviato(asin):
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO prodotti (asin, inviato_il) VALUES (%s, %s)
                    ON CONFLICT (asin) DO UPDATE SET inviato_il = EXCLUDED.inviato_il
                """, (asin, datetime.now()))
            conn.commit()
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO prodotti (asin, inviato_il) VALUES (?, ?)",
                         (asin, datetime.now().isoformat()))

# --- CONTROLLO DUPLICATI SU TITOLO: due ASIN diversi possono essere lo
# stesso prodotto (stesso oggetto venduto da venditori diversi, o varianti
# con parole in ordine diverso). L'ASIN da solo non basta a evitarli. ---
STOPWORDS_TITOLO = {
    "da", "di", "per", "con", "il", "la", "lo", "gli", "le", "un", "una",
    "e", "a", "in", "su", "the", "of", "for", "with", "and", "to",
}

def normalizza_titolo(titolo):
    """Riduce un titolo a un insieme ordinato di parole significative, così
    'Shimano Mulinello da pesca FX Spinning' e 'Shimano FX, mulinello da
    pesca a spinning' producono la STESSA firma, nonostante ordine/punteggiatura
    diversi, permettendo di riconoscerli come lo stesso prodotto."""
    if not titolo:
        return ""
    pulito = re.sub(r'[^\w\s]', ' ', titolo.lower())
    parole = [p for p in pulito.split() if p not in STOPWORDS_TITOLO and len(p) > 1]
    return "|".join(sorted(set(parole)))

def titolo_gia_inviato(titolo_norm):
    if not titolo_norm:
        return False
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT inviato_il FROM titoli_inviati WHERE titolo_norm = %s", (titolo_norm,))
                row = cur.fetchone()
        if not row:
            return False
        return datetime.now() - row[0] < timedelta(hours=24)
    else:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT inviato_il FROM titoli_inviati WHERE titolo_norm = ?", (titolo_norm,))
            row = cursor.fetchone()
        if not row:
            return False
        inviato_dt = datetime.fromisoformat(row[0])
        return datetime.now() - inviato_dt < timedelta(hours=24)

def segna_titolo_inviato(titolo_norm):
    if not titolo_norm:
        return
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO titoli_inviati (titolo_norm, inviato_il) VALUES (%s, %s)
                    ON CONFLICT (titolo_norm) DO UPDATE SET inviato_il = EXCLUDED.inviato_il
                """, (titolo_norm, datetime.now()))
            conn.commit()
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO titoli_inviati (titolo_norm, inviato_il) VALUES (?, ?)",
                         (titolo_norm, datetime.now().isoformat()))

# --- PULIZIA TITOLO: dal mega-titolo Amazon estraggo solo il nome essenziale ---
# --- FILTRO PESCA: pubblica solo se il prodotto è davvero attrezzatura da pesca ---
# NOTA: evito la parola singola "pesca" perché in italiano è ambigua (significa
# sia "fishing" che il frutto "peach") - ma aggiungo altre parole singole
# specifiche del gergo pesca, molto meno ambigue, per non scartare prodotti
# validi solo perché il titolo non contiene una delle frasi composte esatte
PAROLE_CHIAVE_PESCA = [
    # Frasi composte (alta precisione)
    "canna da pesca", "canne da pesca", "amo da pesca", "ami da pesca",
    "esca da pesca", "esche da pesca", "galleggiante da pesca",
    "abbigliamento da pesca", "kayak da pesca", "cassetta da pesca",
    "borsa da pesca", "carp fishing", "spinning pesca", "feeder pesca",
    "monofilo pesca", "esche artificiali", "esche siliconiche",

    # Parole singole specifiche del gergo pesca (bassa ambiguità)
    "mulinello", "mulinelli", "lenza", "lenze", "guadino", "wader",
    "waders", "surfcasting", "traina", "bolentino", "carpfishing",
    "ecoscandaglio", "fishfinder", "esche", "esca", "amo", "ami",
    "girella", "girelle", "piombo", "piombi", "boilies", "bivvy",
    "spinning", "jig", "popper", "minnow", "wobbler", "cucchiaino",
    "avannotto", "terminale", "trecciato", "canna", "canne",

    # Specie di pesci target (indicative nel contesto attrezzatura/esche)
    "trota", "carpa", "luccio", "persico", "spigola", "siluro",
    "cavedano", "orata", "black bass", "tinca",

    # Marchi noti di attrezzatura pesca
    "rapala", "shimano", "daiwa", "abu garcia", "penn", "dam",
]

def e_prodotto_pesca(titolo):
    if not titolo:
        return False
    titolo_lower = titolo.lower()
    for parola in PAROLE_CHIAVE_PESCA:
        # \b = confine di parola: evita che "ami" matchi dentro "amici",
        # o che "canna" matchi dentro parole più lunghe per caso
        if re.search(r'\b' + re.escape(parola) + r'\b', titolo_lower):
            return True
    return False

def pulisci_titolo(titolo):
    """I titoli Amazon seguono quasi sempre lo schema 'Nome prodotto, poi
    lista infinita di caratteristiche/dettagli marketing'. Taglio al primo
    separatore (virgola, o trattino se non c'è virgola) per tenere solo la
    parte che dice davvero cos'è il prodotto."""
    if not titolo:
        return titolo
    if ',' in titolo:
        base = titolo.split(',')[0].strip()
    elif ' - ' in titolo:
        base = titolo.split(' - ')[0].strip()
    elif ' – ' in titolo:
        base = titolo.split(' – ')[0].strip()
    else:
        base = titolo.strip()
    # Se il pezzo tagliato è troppo corto (es. solo il brand), includo
    # anche il pezzo successivo per non perdere informazione utile
    if len(base) < 12 and ',' in titolo:
        parti = titolo.split(',')
        base = ','.join(parti[:2]).strip()
    return base

# --- SCRAPER POTENZIATO ---
def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "it-IT,it;q=0.9"}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        print(f"[DEBUG SCRAPER] {asin} -> status_code={res.status_code}, lunghezza_html={len(res.text)}")
        if res.status_code != 200:
            print(f"[DEBUG SCRAPER] {asin} -> status non 200, primi 300 char risposta: {res.text[:300]!r}")
            return None
        soup = BeautifulSoup(res.text, "html.parser")

        # Rilevo pagine di blocco/captcha di Amazon
        if soup.find("form", {"action": re.compile("validateCaptcha")}) or "Inserisci i caratteri" in res.text or "automated access" in res.text.lower():
            print(f"[DEBUG SCRAPER] {asin} -> rilevata pagina CAPTCHA/blocco anti-bot di Amazon")
            return None

        titolo_elem = soup.find("span", {"id": "productTitle"})
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"
        # NOTA: qui NON applico pulisci_titolo() come nel bot generico —
        # per il canale pesca vogliamo il titolo/descrizione completo, non
        # accorciato al primo separatore
        print(f"[DEBUG SCRAPER] {asin} -> titolo trovato: {titolo_elem is not None} ('{titolo[:50]}')")

        def sembra_prezzo_valido(testo):
            """Scarta prezzi-per-unità tipo '0,25 €/100 ml' o '4,50€/kg':
            un prezzo reale del prodotto non contiene slash né unità di misura."""
            if not testo: return False
            if "/" in testo: return False
            if re.search(r'\b(ml|kg|gr|g|l|pz|cad)\b', testo, re.IGNORECASE): return False
            return bool(re.search(r'\d', testo))

        def e_prezzo_barrato(elem):
            """Riconosce se un elemento è il prezzo VECCHIO/barrato (da NON usare
            come prezzo attuale), guardando classi e attributi tipici di Amazon."""
            classi = elem.get("class") or []
            if "a-text-price" in classi: return True
            if "a-text-strike" in classi: return True
            if "basisPrice" in classi: return True
            if elem.get("data-a-strike") == "true": return True
            # Controllo anche il genitore diretto, spesso è lì che sta il flag
            genitore = elem.parent
            if genitore is not None:
                classi_genitore = genitore.get("class") or []
                if "basisPrice" in classi_genitore: return True
                if genitore.get("data-a-strike") == "true": return True
            return False

        prezzo_attuale_str = None

        # Prima individuo il CONTENITORE principale del box prezzo del prodotto,
        # per evitare di pescare prezzi di prodotti correlati/suggeriti altrove in pagina
        contenitore_prezzo = (
            soup.find("div", {"id": "corePriceDisplay_desktop_feature_div"})
            or soup.find("div", {"id": "apex_desktop"})
            or soup.find("div", {"id": "unifiedPrice_feature_div"})
            or soup.find("div", {"id": "centerCol"})  # colonna centrale come ultima risorsa
            or soup  # se proprio non trovo nulla, uso l'intera pagina (comportamento precedente)
        )

        # Debug: quale container ho effettivamente trovato?
        for _id in ["corePriceDisplay_desktop_feature_div", "apex_desktop", "unifiedPrice_feature_div", "centerCol"]:
            if contenitore_prezzo is soup.find("div", {"id": _id}):
                print(f"[DEBUG SCRAPER] {asin} -> contenitore prezzo usato: #{_id}")
                break
        else:
            print(f"[DEBUG SCRAPER] {asin} -> contenitore prezzo: NESSUNO trovato, uso pagina intera")

        # Nota: il controllo su "Attualmente non disponibile" è stato rimosso
        # perché quella stringa compare nell'HTML anche di prodotti disponibili
        # (varianti/correlati), dando sempre falsi positivi.

        def e_prezzo_per_unita(elem):
            """Riconosce il prezzo-per-unità (es. '0,25€ /100 ml'). Il testo dentro
            a-offscreen è solo '0,25€', mentre '/100 ml' sta in un elemento vicino.
            IMPORTANTE: guardo solo il genitore diretto e i fratelli immediati, non
            l'intero contenitore, altrimenti scarterei anche il prezzo corretto."""
            pattern_unita = r'/\s*\d*\s*(ml|l|kg|g|gr|cl|pz|conteggio|count)\b'

            # 1) Dimensione esplicitamente piccola = non è mai il prezzo principale
            if elem.get("data-a-size") in ("mini", "small"):
                return True

            # 2) Testo del genitore diretto, MA solo se è un involucro "stretto"
            #    (contiene un solo prezzo). Se il genitore è il contenitore generale
            #    che racchiude più prezzi, il suo testo includerebbe l'unità di un
            #    ALTRO prezzo e scarterebbe per errore quello giusto.
            genitore = elem.parent
            if genitore is not None:
                prezzi_nel_genitore = genitore.find_all("span", class_="a-offscreen")
                if len(prezzi_nel_genitore) <= 1:
                    if re.search(pattern_unita, genitore.get_text(" ", strip=True), re.IGNORECASE):
                        return True

            # 3) Fratelli immediatamente successivi: l'unità segue il prezzo.
            #    Salto però i fratelli che contengono un PROPRIO prezzo, perché
            #    sono blocchi-prezzo autonomi (es. il box del prezzo-per-unità)
            #    e non il semplice suffisso "/100 ml" di QUESTO prezzo.
            for fratello in list(elem.next_siblings)[:3]:
                if hasattr(fratello, "find_all") and fratello.find_all("span", class_="a-offscreen"):
                    continue
                testo_fratello = fratello.get_text(" ", strip=True) if hasattr(fratello, "get_text") else str(fratello).strip()
                if re.search(pattern_unita, testo_fratello, re.IGNORECASE):
                    return True

            return False

        # I selettori per DIMENSIONE (xl/l) per primi: su Amazon il prezzo principale
        # è sempre reso in grande, mentre il prezzo-per-unità è piccolo. Attenzione:
        # la classe 'priceToPay' viene usata da Amazon ANCHE per il prezzo-per-unità,
        # quindi non è affidabile come primo criterio (causava lo scambio 2,49€ -> 0,25€).
        candidati_prezzo = [
            ("span", {"class": "a-price", "data-a-size": "xl"}),
            ("span", {"class": "a-price", "data-a-size": "l"}),
            ("span", {"class": "apexPriceToPay"}),
            ("span", {"class": "priceToPay"}),
        ]
        def testo_prezzo_da_elemento(p_elem):
            """Estrae il prezzo da un elemento a-price. Amazon usa DUE strutture:
            1) il prezzo dentro <span class="a-offscreen">17,99 €</span>
            2) a-offscreen VUOTO e le cifre in a-price-whole + a-price-fraction
               (con a-price-decimal per la virgola). Questa seconda struttura è
               quella usata nelle pagine con sconto/offerta a tempo.
            In più c'è l'etichetta di accessibilità nel genitore, es.
            "2,49 € con 38 un risparmio percentuale", usata come ultima risorsa."""

            # Caso 1: a-offscreen popolato
            off = p_elem.find("span", class_="a-offscreen")
            if off:
                t = off.get_text().strip()
                if sembra_prezzo_valido(t):
                    return t

            # Caso 2: ricostruzione da a-price-whole + a-price-fraction
            whole = p_elem.find("span", class_="a-price-whole")
            if whole:
                # get_text() include già la virgola di a-price-decimal: "2,"
                parte_intera = re.sub(r'[^\d.,]', '', whole.get_text()).rstrip('.,')
                frazione = p_elem.find("span", class_="a-price-fraction")
                parte_dec = re.sub(r'[^\d]', '', frazione.get_text()) if frazione else ""
                if parte_intera:
                    return f"{parte_intera},{parte_dec}€" if parte_dec else f"{parte_intera}€"

            # Caso 3: etichetta di accessibilità nel genitore
            genitore = p_elem.parent
            if genitore is not None:
                etichetta = genitore.find("span", class_="aok-offscreen")
                if etichetta:
                    m = re.search(r'([\d.]*\d[,.]\d{2})\s*€', etichetta.get_text())
                    if m:
                        return f"{m.group(1)}€"

            return None

        for tag, attrs in candidati_prezzo:
            trovati_candidati = contenitore_prezzo.find_all(tag, attrs)
            print(f"[DEBUG SCRAPER] {asin} -> candidato {attrs}: {len(trovati_candidati)} elementi trovati")
            for p_elem in trovati_candidati:
                if e_prezzo_barrato(p_elem):
                    print(f"[DEBUG SCRAPER] {asin} -> scartato BARRATO in {attrs}")
                    continue
                if e_prezzo_per_unita(p_elem):
                    print(f"[DEBUG SCRAPER] {asin} -> scartato prezzo-per-unità in {attrs}")
                    continue
                testo_prezzo = testo_prezzo_da_elemento(p_elem)
                if not testo_prezzo or not sembra_prezzo_valido(testo_prezzo):
                    print(f"[DEBUG SCRAPER] {asin} -> scartato NON VALIDO: {testo_prezzo!r}")
                    continue
                prezzo_attuale_str = testo_prezzo
                break
            if prezzo_attuale_str:
                break

        # Fallback: scansiona gli span a-offscreen SOLO dentro il contenitore prezzo
        # (non più su tutta la pagina, per evitare prezzi di prodotti correlati)
        if not prezzo_attuale_str:
            tutti_offscreen = contenitore_prezzo.find_all("span", class_="a-offscreen")
            print(f"[DEBUG SCRAPER] {asin} -> fallback: {len(tutti_offscreen)} span a-offscreen nel container")
            for off_elem in tutti_offscreen:
                if e_prezzo_barrato(off_elem):
                    continue
                if e_prezzo_per_unita(off_elem):
                    continue
                testo_prezzo = off_elem.get_text().strip()
                if sembra_prezzo_valido(testo_prezzo):
                    prezzo_attuale_str = testo_prezzo
                    break

        p_att_num = parse_prezzo(prezzo_attuale_str)
        print(f"[DEBUG SCRAPER] {asin} -> prezzo_attuale_str={prezzo_attuale_str!r}, p_att_num={p_att_num}")
        if not p_att_num:
            print(f"[DEBUG SCRAPER] {asin} -> NESSUN PREZZO TROVATO -> return None")
            return None
        prezzo_attuale = f"{p_att_num:.2f}".replace(".", ",")

        sconto = 0
        prezzo_precedente = None

        strike_elem = (
            contenitore_prezzo.find("span", class_="a-text-strike") or
            contenitore_prezzo.find("span", {"id": "listPrice"}) or
            contenitore_prezzo.find("span", class_="basisPrice")
        )

        val_strike = None
        if strike_elem:
            off_strike = strike_elem.find("span", class_="a-offscreen")
            testo_strike = (off_strike.get_text() if off_strike else strike_elem.get_text()).strip()
            if sembra_prezzo_valido(testo_strike):
                val_strike = testo_strike

        if not val_strike:
            text_page = soup.get_text()
            m_mediano = re.search(
                r'Prezzo\s+(?:consigliato|mediano|più\s+basso\s+ultimi\s+30gg)[:\s]*([\d.,]+)\s*€',
                text_page, re.IGNORECASE
            )
            if m_mediano:
                val_strike = m_mediano.group(1)

        print(f"[DEBUG SCRAPER] {asin} -> val_strike={val_strike!r}")

        if val_strike:
            p_prec_num = parse_prezzo(val_strike)
            if p_prec_num and p_prec_num > p_att_num:
                sconto = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                prezzo_precedente = f"{p_prec_num:.2f}".replace(".", ",")

        img_elem = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"id": "imgBlkFront"})
        img_url = img_elem["src"] if img_elem else ""

        return {"asin": asin, "titolo": titolo, "prezzo_attuale": prezzo_attuale, "prezzo_precedente": prezzo_precedente, "sconto": sconto, "immagine_url": img_url}
    except Exception as e:
        print(f"[ERRORE SCRAPING]: {e}")
        return None

# --- GENERAZIONE IMMAGINE ---
def crea_immagine(prodotto):
    cairosvg.svg2png(url=str(SVG_TEMPLATE_PATH), write_to=str(OUTPUT_PATH))
    base_img = Image.open(OUTPUT_PATH).convert("RGBA")
    draw = ImageDraw.Draw(base_img)

    font_titolo = carica_font_locale(22)
    font_patt = carica_font_locale(95)
    font_pvec = carica_font_locale(50)
    font_sconto = carica_font_locale(80)

    # --- Box foto prodotto: riquadro bianco a sinistra ---
    # Stimato dal layout del nuovo template (canvas 1080x1080):
    # il riquadro bianco occupa circa x:22-497, y:389-1048
    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            box_x, box_y = 30, 400
            box_w, box_h = 460, 630

            margine = 0
            img_prod.thumbnail((box_w - margine * 2, box_h - margine * 2), Image.Resampling.LANCZOS)

            base_img.paste(
                img_prod,
                (box_x + (box_w - img_prod.width) // 2, box_y + (box_h - img_prod.height) // 2),
                img_prod
            )
        except: pass

    # --- Coordinate testo: ricalibrate sul nuovo layout a 3 riquadri ---
    # ATTENZIONE: queste sono STIME dalla grafica fornita, non misurate a pixel
    # esatti (non ho potuto renderizzare l'SVG in questo ambiente per verificare).
    # Vanno quasi certamente affinate dopo il primo test reale, come già fatto
    # altre volte per il bot generico.
    CENTRO_X = 794            # centro orizzontale della colonna destra
    Y_TITOLO = 330            # centro verticale riquadro 1 (teal, nome prodotto)
    Y_PREZZO_ATTUALE = 632    # centro verticale riquadro 2 (arancione, prezzo)
    Y_PREZZO_VECCHIO = 870    # riga 1 del riquadro 3 (teal): prezzo barrato
    Y_SCONTO = 960            # riga 2 del riquadro 3 (teal): percentuale sconto

    titolo_txt = textwrap.fill(prodotto["titolo"][:140], width=30)
    draw_centrato(draw, CENTRO_X, Y_TITOLO, titolo_txt, font_titolo, "white",
                  stroke_width=2, stroke_fill="black")

    draw_centrato(draw, CENTRO_X, Y_PREZZO_ATTUALE, f"{prodotto['prezzo_attuale']} €", font_patt, "#111111",
                  stroke_width=1, stroke_fill="white")

    # Riquadro 3: prezzo barrato + sconto insieme (il nuovo template ha un
    # riquadro in meno rispetto all'originale, quindi li combino qui)
    if prodotto.get("prezzo_precedente"):
        p_vec = f"anziché {prodotto['prezzo_precedente']} €"
        bbox, _ = draw_centrato(draw, CENTRO_X, Y_PREZZO_VECCHIO, p_vec, font_pvec, "white",
                                 stroke_width=1, stroke_fill="black")
        w = bbox[2] - bbox[0]
        draw.line(
            [(CENTRO_X - w / 2 - 4, Y_PREZZO_VECCHIO), (CENTRO_X + w / 2 + 4, Y_PREZZO_VECCHIO)],
            fill="#FF3333", width=4
        )

    if prodotto.get("sconto") and prodotto["sconto"] > 0:
        draw_centrato(draw, CENTRO_X, Y_SCONTO, f"-{prodotto['sconto']}%", font_sconto, "white",
                      stroke_width=2, stroke_fill="black")

    base_img.convert("RGB").save(OUTPUT_PATH, "PNG")
    return OUTPUT_PATH

# --- RICERCA DIRETTA SU AMAZON (invece di dipendere da altri canali) ---
QUERY_RICERCA_PESCA = [
    # --- Generico / attrezzatura base ---
    "canna da pesca",
    "mulinello pesca",
    "esche pesca",
    "lenza pesca",
    "abbigliamento pesca",
    "kayak pesca",
    "guadino pesca",

    # --- Carpfishing ---
    "carpfishing",
    "tenda carpfishing",
    "bivvy carpfishing",
    "rod pod carpfishing",
    "sedia carpfishing",
    "avvisatore abboccata carpfishing",
    "boilies carpfishing",

    # --- Esche artificiali / tipi specifici ---
    "artificiali pesca",
    "minnow pesca",
    "esche siliconiche pesca",
    "cucchiaino pesca",
    "popper pesca",
    "jig pesca",

    # --- Galleggianti e piombatura ---
    "galleggianti pesca",
    "piombi pesca",
    "girelle pesca",

    # --- Tecniche specifiche ---
    "spinning pesca",
    "feeder pesca",
    "bolentino pesca",
    "traina pesca",
    "surfcasting",

    # --- Accessori vari ---
    "borsa porta canne pesca",
    "cassetta pesca",
    "ami pesca",
    "filo pesca",
    "torcia pesca",
    "ecoscandaglio pesca",
]

# Con una lista lunga come questa, ogni query può restituire decine di
# risultati: limito quanti ASIN processare per query per tenere sotto
# controllo la durata di un ciclo completo di polling
MAX_ASIN_PER_QUERY = 15

# Sessione condivisa e persistente tra tutte le ricerche: mantiene i cookie
# come farebbe un browser vero, invece di sembrare una richiesta "nuda" isolata
_sessione_amazon = requests.Session()
_sessione_riscaldata = False

def _riscalda_sessione():
    """Visita prima la homepage Amazon per ottenere cookie di sessione reali,
    prima di colpire direttamente le pagine di ricerca (più protette)."""
    global _sessione_riscaldata
    if _sessione_riscaldata:
        return
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        res = _sessione_amazon.get("https://www.amazon.it/", headers=headers, timeout=12)
        print(f"[DEBUG RICERCA] Riscaldamento sessione -> status={res.status_code}, cookie ottenuti={len(_sessione_amazon.cookies)}")
        _sessione_riscaldata = True
    except Exception as e:
        print(f"[ERRORE RICERCA] Riscaldamento sessione fallito: {e}")

def cerca_asin_amazon(query):
    """Cerca su Amazon.it per una query e restituisce la lista di ASIN trovati
    nella pagina dei risultati. Non guarda lo sconto qui: quello lo verifichiamo
    dopo con scarica_dettagli_amazon(), che è già affidabile per quello."""
    _riscalda_sessione()

    url = f"https://www.amazon.it/s?k={requests.utils.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Referer": "https://www.amazon.it/",
    }
    try:
        res = _sessione_amazon.get(url, headers=headers, timeout=12)
        if res.status_code != 200:
            print(f"[DEBUG RICERCA] '{query}' -> status_code={res.status_code}, primi 300 char: {res.text[:300]!r}")
            return []
        # Ogni risultato prodotto ha un attributo data-asin sul contenitore
        asin_trovati = re.findall(r'data-asin="([A-Z0-9]{10})"', res.text)
        # Rimuovo duplicati mantenendo l'ordine
        visti = set()
        asin_unici = []
        for a in asin_trovati:
            if a not in visti:
                visti.add(a)
                asin_unici.append(a)
        print(f"[DEBUG RICERCA] '{query}' -> {len(asin_unici)} ASIN trovati")
        return asin_unici
    except Exception as e:
        print(f"[ERRORE RICERCA] '{query}': {e}")
        return []

SCONTO_MINIMO_DA_PUBBLICARE = 15  # % - sotto questa soglia non vale la pena postare

# --- BOT TELEGRAM (polling periodico su Amazon, non ascolto di altri canali) ---
INTERVALLO_POLLING_MINUTI = 30  # ogni quanto ricontrollare Amazon

def frase_iniziale(sconto):
    """Sceglie la frase ad effetto in base alla percentuale di sconto.
    Controllo dal più alto al più basso: uno sconto del 60% deve
    prendere 'errore di prezzo', non anche le soglie inferiori."""
    if sconto > 50:
        return "🚨 ERRORE DI PREZZO?! 🚨\n\n"
    elif sconto > 30:
        return "🌟 OFFERTA SPECIALE! 🌟\n\n"
    elif sconto > 10:
        return "🐛 Il Tarlo ha colpito ancora! 🐛\n\n"
    return ""

async def processa_asin(asin):
    """Scarica dettagli, verifica sconto minimo e filtro pesca, e se tutto ok
    invia il post. Restituisce True se ha effettivamente pubblicato."""
    if gia_inviato(asin):
        return False

    # Segno subito come inviato per evitare doppioni se lo stesso ASIN esce
    # da due query di ricerca diverse nello stesso ciclo di polling
    segna_inviato(asin)

    p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
    if not p:
        print(f"[DEBUG] Scraping fallito per ASIN {asin}")
        return False

    if not e_prodotto_pesca(p['titolo']):
        print(f"[DEBUG] {asin} -> '{p['titolo']}' NON è attrezzatura pesca -> scartato")
        return False

    if p['sconto'] < SCONTO_MINIMO_DA_PUBBLICARE:
        print(f"[DEBUG] {asin} -> sconto {p['sconto']}% sotto la soglia minima ({SCONTO_MINIMO_DA_PUBBLICARE}%) -> scartato")
        return False

    # Controllo duplicati "reali": due ASIN diversi possono essere lo stesso
    # prodotto (venditori diversi, ordine parole diverso nel titolo, ecc.)
    titolo_norm = normalizza_titolo(p['titolo'])
    if titolo_gia_inviato(titolo_norm):
        print(f"[DEBUG] {asin} -> '{p['titolo']}' sembra un duplicato di un prodotto già inviato -> scartato")
        return False
    segna_titolo_inviato(titolo_norm)

    foto = crea_immagine(p)
    url = f"https://www.amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}"

    msg = frase_iniziale(p['sconto'])
    msg += f"🎣 *{p['titolo']}*\n\n"
    msg += f"💰 *{p['prezzo_attuale']} €* anziché {p['prezzo_precedente']} €! (-{p['sconto']}%)\n"
    msg += f"👉 [Apri su Amazon]({url})\n\n"
    msg += "🪝 Segnalata dal canale pesca\n#OffertePesca"

    for tentativo in range(1, 4):
        try:
            with open(foto, "rb") as f:
                await bot.send_photo(chat_id=CANALE_CHAT_ID, photo=f, caption=msg, parse_mode="Markdown")
            print(f"[DEBUG] Inviato con successo ASIN {asin}")
            return True
        except Exception as e:
            print(f"[ERRORE INVIO] tentativo {tentativo}/3 per {asin}: {type(e).__name__}: {e}")
            if tentativo < 3:
                await asyncio.sleep(5 * tentativo)
            else:
                print(f"[ERRORE INVIO] {asin} definitivamente non inviato")
    return False

async def main():
    init_db()
    print(f"[DEBUG] Bot pesca avviato. Polling ogni {INTERVALLO_POLLING_MINUTI} minuti.")

    while True:
        for query in QUERY_RICERCA_PESCA:
            asin_list = await asyncio.to_thread(cerca_asin_amazon, query)
            asin_list = asin_list[:MAX_ASIN_PER_QUERY]
            for asin in asin_list:
                await processa_asin(asin)
                await asyncio.sleep(2)  # piccola pausa tra un prodotto e l'altro
            await asyncio.sleep(15)  # pausa tra una query e l'altra

        print(f"[DEBUG] Ciclo completato, prossimo tra {INTERVALLO_POLLING_MINUTI} minuti")
        await asyncio.sleep(INTERVALLO_POLLING_MINUTI * 60)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
