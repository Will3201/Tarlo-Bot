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
from telegram.error import NetworkError
from telegram.helpers import escape_markdown
from pesca_layout import componi
from pesca_rules import e_pesca
from coupon_notice import avviso_coupon
from tarlo_daily import valuta
import pesca_amazon

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

PORT = int(os.getenv("PORT", os.getenv("PORT_PESCA", "10001")))  # porta diversa se sullo stesso host

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
        return datetime.now() - row[0] < timedelta(hours=72)
    else:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT inviato_il FROM prodotti WHERE asin = ?", (asin,))
            row = cursor.fetchone()
        if not row:
            return False
        inviato_dt = datetime.fromisoformat(row[0])
        return datetime.now() - inviato_dt < timedelta(hours=72)

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
        return datetime.now() - row[0] < timedelta(hours=72)
    else:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT inviato_il FROM titoli_inviati WHERE titolo_norm = ?", (titolo_norm,))
            row = cursor.fetchone()
        if not row:
            return False
        inviato_dt = datetime.fromisoformat(row[0])
        return datetime.now() - inviato_dt < timedelta(hours=72)

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

    # Termini tecnici carpfishing in inglese (spesso usati così anche nei
    # titoli italiani) - mancavano nonostante le query di ricerca dedicate
    "rod pod", "bank stick", "bank sticks", "buzz bar", "bite alarm",
    "sleeping bag", "bedchair", "rod rest", "rod rests",
    "chair carpfishing", "unhooking mat",
    "throwing stick", "spod", "spomb", "method feeder",

    # Termini generici inglesi molto comuni nei titoli (spesso venditori
    # scrivono tutto in inglese anche su Amazon.it) + refusi comuni
    "fishing", "lure", "lures", "bait", "baits", "tackle", "minow",

    # Abbigliamento e accessori tecnici specifici da pesca
    "occhiali polarizzati", "stivali da pesca", "giacca da pesca",
    "guanti da pesca", "cappello da pesca", "gilet da pesca",
    "cerata pesca", "salopette pesca", "scarponi pesca",
]

def e_prodotto_pesca(titolo):
    return e_pesca(titolo or "")

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
    return pesca_amazon.scarica_dettagli_amazon(asin, strict=True)

# --- GENERAZIONE IMMAGINE ---
def crea_immagine(prodotto):
    base = Image.open(BytesIO(cairosvg.svg2png(url=str(SVG_TEMPLATE_PATH)))).convert('RGBA')
    response = requests.get(prodotto['immagine_url'], timeout=15)
    response.raise_for_status()
    product_image = Image.open(BytesIO(response.content))
    componi(base, product_image, prodotto)
    result = BytesIO()
    base.convert('RGB').save(result, 'PNG')
    return result.getvalue()

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

    # --- Discipline/tecniche specifiche (da Bass Store Italy) ---
    "bassfishing",
    "black bass pesca",
    "pike fishing luccio",
    "catfishing siluro",
    "trout area pesca trota",
    "pesca al colpo",
    "pesca a mosca",
    "rock fishing",
    "street fishing",

    # --- Tipi specifici di esche hardbait/softbait ---
    "crankbait",
    "swimbait",
    "jerkbait",
    "spinnerbait",
    "chatterbait",
    "buzzbait",
    "wire bait pesca",
    "topwater pesca",
    "worm pesca esca",
    "tube jig pesca",

    # --- Pasture ed esche vive/naturali ---
    "pastura pesca",
    "pellet pesca",
    "boilies pesca",
    "esche vive pesca",

    # --- Accessori tecnici aggiuntivi ---
    "porta canne pesca",
    "avvolgilenza pesca",
    "rastrelliera canne pesca",
]

# Con una lista lunga come questa, ogni query può restituire decine di
# risultati: limito quanti ASIN processare per query per tenere sotto
# controllo la durata di un ciclo completo di polling
MAX_ASIN_PER_QUERY = 15
MAX_POST_PER_QUERY = max(1, min(15, int(os.getenv("MAX_POST_PER_QUERY_PESCA", "3"))))

# Sessione condivisa e persistente tra tutte le ricerche: mantiene i cookie
# come farebbe un browser vero, invece di sembrare una richiesta "nuda" isolata
_sessione_amazon = requests.Session()
_sessione_riscaldata = False



def cerca_asin_amazon(query):
    """Cerca su Amazon.it per una query e restituisce la lista di ASIN trovati
    nella pagina dei risultati. Non guarda lo sconto qui: quello lo verifichiamo
    dopo con scarica_dettagli_amazon(), che è già affidabile per quello."""

    url = f"https://www.amazon.it/s?k={requests.utils.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
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
        if res.status_code in (429, 503):
            pesca_amazon.BLOCCATO = True
        if res.status_code != 200:
            print(f"[DEBUG RICERCA] '{query}' -> status_code={res.status_code}, primi 300 char: {res.text[:300]!r}")
            return []
        soup = BeautifulSoup(res.text, 'html.parser')
        if soup.select_one('form[action*="validateCaptcha"]') or 'automated access' in res.text.lower():
            pesca_amazon.BLOCCATO = True
            return []
        asin_trovati = [node.get('data-asin', '') for node in
                       soup.select('[data-component-type="s-search-result"][data-asin]')]
        asin_trovati = [a for a in asin_trovati if re.fullmatch(r'[A-Z0-9]{10}', a)]
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
    return '🎣 Offerta selezionata dal Tarlo della Pesca!\n\n'


def offerta_valida(p):
    return bool(p and p.get('disponibile') and p.get('immagine_url')
                and e_prodotto_pesca(p['titolo'])
                and p.get('tipo_riferimento') in {'consigliato', 'mediano', 'piu_basso_30gg', 'precedente'}
                and p['sconto'] >= SCONTO_MINIMO_DA_PUBBLICARE)


async def processa_asin(asin):
    if gia_inviato(asin):
        return False
    # Nuova verifica subito prima dell'invio; niente prezzi raccolti ore prima.
    p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
    if not offerta_valida(p):
        return False
    titolo_norm = normalizza_titolo(p['titolo'])
    if titolo_gia_inviato(titolo_norm):
        return False
    try:
        foto = await asyncio.to_thread(crea_immagine, p)
        url = f"https://www.amazon.it/dp/{asin}?tag={AMAZON_TAG}"
        title = escape_markdown(p['titolo'][:220], version=1)
        msg = frase_iniziale(p['sconto']) + f"🎣 *{title}*\n\n"
        msg += f"💰 *{p['prezzo_attuale']} €*\n"
        labels = {'consigliato':'prezzo consigliato', 'mediano':'prezzo mediano',
                  'piu_basso_30gg':'prezzo più basso degli ultimi 30 giorni', 'precedente':'prezzo precedente'}
        msg += f"Riferimento Amazon ({labels[p['tipo_riferimento']]}): {p['prezzo_precedente']} € (-{p['sconto']}%).\n"
        msg += avviso_coupon(p.get('coupon'))
        msg += f"👉 [Apri su Amazon]({url})\n\n🪝 Il Tarlo della Pesca\nLink affiliato. #OffertePesca"
        await bot.send_photo(chat_id=CANALE_CHAT_ID, photo=BytesIO(foto), caption=msg, parse_mode='Markdown')
    except NetworkError:
        # Potrebbe essere arrivato: niente retry automatici che creano doppioni.
        segna_inviato(asin)
        segna_titolo_inviato(titolo_norm)
        print(f'[INVIO INCERTO] {asin}: verificare il canale')
        return False
    except Exception as exc:
        print(f'[INVIO FALLITO] {asin}: {type(exc).__name__}')
        return False
    segna_inviato(asin)
    segna_titolo_inviato(titolo_norm)
    print(f'[INVIATO PESCA] {asin}')
    return True

async def main():
    init_db()
    print('[PESCA] Ricerca Amazon attiva; selezione per sconto, recensioni e acquisti disponibili.')
    async with bot:
        while True:
            visti = set()
            pesca_amazon.BLOCCATO = False
            for query in QUERY_RICERCA_PESCA:
                try:
                    asin_list = await asyncio.to_thread(cerca_asin_amazon, query)
                    candidati = []
                    for asin in asin_list[:MAX_ASIN_PER_QUERY]:
                        if pesca_amazon.BLOCCATO:
                            break
                        if asin in visti or gia_inviato(asin):
                            continue
                        visti.add(asin)
                        p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
                        if offerta_valida(p) and not titolo_gia_inviato(normalizza_titolo(p['titolo'])):
                            score = valuta(p)
                            if score:
                                candidati.append((score['punteggio'], asin))
                        await asyncio.sleep(2)
                    for _, asin in sorted(candidati, reverse=True)[:MAX_POST_PER_QUERY]:
                        if pesca_amazon.BLOCCATO:
                            break
                        await processa_asin(asin)
                        await asyncio.sleep(2)
                except Exception as exc:
                    print(f'[CICLO PESCA] {type(exc).__name__}: query saltata')
                if pesca_amazon.BLOCCATO:
                    print('[PESCA] Amazon limita le richieste: pausa fino al prossimo ciclo.')
                    break
                await asyncio.sleep(15)
            print('[PESCA] Ciclo completato, pausa di 30 minuti.')
            await asyncio.sleep(INTERVALLO_POLLING_MINUTI * 60)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
