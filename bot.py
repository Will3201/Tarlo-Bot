import asyncio
import html
import os
import threading
from io import BytesIO
from pathlib import Path

import requests
from flask import Flask
from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Bot

# ============================================================
# MODIFICA SOLO QUESTE RIGHE
# ============================================================
TELEGRAM_TOKEN = "INCOLLA_QUI_IL_TOKEN_BOTFATHER"
CANALE_CHAT_ID = "@iltarlodelrisparmio"
AMAZON_TAG = "iltarlodelrisp-21"
INTERVALLO_MINUTI = 30
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)


@app.route("/")
def home():
    return "Il Tarlo del Risparmio è attivo!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "template": TEMPLATE_PATH.exists(),
        "token_inserito": TELEGRAM_TOKEN != "INCOLLA_QUI_IL_TOKEN_BOTFATHER",
        "canale": CANALE_CHAT_ID,
    }


def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def carica_font(dimensione, grassetto=False):
    percorsi = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if grassetto
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if grassetto
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for percorso in percorsi:
        if Path(percorso).exists():
            return ImageFont.truetype(percorso, dimensione)
    return ImageFont.load_default()


def spezza_titolo(draw, testo, font, larghezza_massima):
    parole = testo.split()
    righe = []
    riga = ""

    for parola in parole:
        prova = f"{riga} {parola}".strip()
        larghezza = draw.textbbox((0, 0), prova, font=font)[2]
        if larghezza <= larghezza_massima:
            riga = prova
        else:
            if riga:
                righe.append(riga)
            riga = parola

    if riga:
        righe.append(riga)

    return righe


def scarica_immagine(url):
    if not url:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
    }
    risposta = requests.get(url, headers=headers, timeout=20)
    risposta.raise_for_status()

    tipo = risposta.headers.get("content-type", "")
    if "image" not in tipo.lower():
        raise ValueError(f"L'URL non restituisce un'immagine: {tipo}")

    return Image.open(BytesIO(risposta.content)).convert("RGBA")


def crea_immagine_offerta(prodotto):
    if TEMPLATE_PATH.exists():
        template = Image.open(TEMPLATE_PATH).convert("RGBA")
    else:
        template = Image.new("RGBA", (1536, 1536), "#064E3B")

    template = template.resize((1536, 1536), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(template)

    # Pulisce le zone del template dove scriveremo i dati variabili.
    draw.rounded_rectangle((900, 350, 1490, 670), radius=25, fill="#064E3B")
    draw.rounded_rectangle((915, 950, 1490, 1190), radius=30, fill="#F58213")
    draw.rounded_rectangle((915, 1200, 1490, 1295), radius=30, fill="#F5F1E8")

    # FOTO PRODOTTO: mantiene le proporzioni e non la deforma.
    try:
        foto = scarica_immagine(prodotto["immagine_url"])
        if foto:
            foto = ImageOps.contain(
                foto,
                (700, 800),
                method=Image.Resampling.LANCZOS,
            )
            x = 430 - foto.width // 2
            y = 790 - foto.height // 2
            template.alpha_composite(foto, (x, y))
    except Exception as errore:
        print(f"Errore immagine prodotto: {errore}")
        draw.multiline_text(
            (220, 720),
            "IMMAGINE\nNON DISPONIBILE",
            fill="#777777",
            font=carica_font(52, True),
            spacing=12,
            align="center",
        )

    # TITOLO: riduce il font finché entra.
    titolo = str(prodotto["titolo"])
    for dimensione in range(68, 31, -2):
        font_titolo = carica_font(dimensione, True)
        righe = spezza_titolo(draw, titolo, font_titolo, 540)
        if len(righe) <= 4:
            break

    y = 375
    for riga in righe[:4]:
        draw.text((920, y), riga, fill="white", font=font_titolo)
        y += int(dimensione * 1.18)

    # SCONTO
    draw.text(
        (930, 875),
        f"SCONTO -{prodotto['sconto']}%",
        fill="#A8D840",
        font=carica_font(52, True),
    )

    # PREZZO ATTUALE
    prezzo = str(prodotto["prezzo_attuale"])
    font_prezzo = carica_font(96, True)
    larghezza = draw.textbbox((0, 0), prezzo, font=font_prezzo)[2]
    draw.text(
        (1205 - larghezza // 2, 1015),
        prezzo,
        fill="white",
        font=font_prezzo,
    )

    # PREZZO PRECEDENTE BARRATO
    prezzo_vecchio = str(prodotto["prezzo_precedente"])
    font_vecchio = carica_font(46, True)
    bbox = draw.textbbox((0, 0), prezzo_vecchio, font=font_vecchio)
    larghezza_vecchio = bbox[2] - bbox[0]
    x_vecchio = 1240 - larghezza_vecchio // 2

    draw.text(
        (x_vecchio, 1218),
        prezzo_vecchio,
        fill="#143D2D",
        font=font_vecchio,
    )
    draw.line(
        (
            x_vecchio - 10,
            1248,
            x_vecchio + larghezza_vecchio + 10,
            1248,
        ),
        fill="#E24A2A",
        width=8,
    )

    template.convert("RGB").save(OUTPUT_PATH, "PNG", optimize=True)
    return OUTPUT_PATH


def ottieni_catalogo_reale():
    # Sostituisci qui i prodotti con quelli reali.
    return [
        {
            "titolo": "Dash Pods Detersivo Lavatrice, 54 Lavaggi",
            "categoria": "Casa",
            "sconto": 25,
            "asin": "B0BT7V2P2Q",
            "prezzo_attuale": "18,99 €",
            "prezzo_precedente": "25,99 €",
            "immagine_url": "https://m.media-amazon.com/images/I/71XgG9sWc1L._AC_SL1500_.jpg",
        },
        {
            "titolo": "Fairy Platinum Plus Pastiglie Lavastoviglie, 84 Caps",
            "categoria": "Casa",
            "sconto": 30,
            "asin": "B08XN3Z699",
            "prezzo_attuale": "21,99 €",
            "prezzo_precedente": "31,49 €",
            "immagine_url": "https://m.media-amazon.com/images/I/81q2Kx5yS2L._AC_SL1500_.jpg",
        },
    ]


async def invia_offerta(prodotto):
    link = (
        f"https://www.amazon.it/dp/{prodotto['asin']}"
        f"?tag={AMAZON_TAG}"
    )

    foto = crea_immagine_offerta(prodotto)

    didascalia = (
        "🐜 <b>Il Tarlo ha colpito ancora!</b>\n\n"
        f"📦 <b>{html.escape(str(prodotto['titolo']))}</b>\n"
        f"📉 Sconto: <b>-{prodotto['sconto']}%</b>\n"
        f"💰 <s>{html.escape(str(prodotto['prezzo_precedente']))}</s> "
        f"➜ <b>{html.escape(str(prodotto['prezzo_attuale']))}</b>\n\n"
        f'👉 <a href="{html.escape(link, quote=True)}">'
        "ACQUISTA SUBITO IN OFFERTA</a>\n\n"
        "In qualità di Affiliato Amazon ricevo un guadagno "
        "dagli acquisti idonei.\n"
        "#IlTarloDelRisparmio"
    )

    with open(foto, "rb") as file_foto:
        await bot.send_photo(
            chat_id=CANALE_CHAT_ID,
            photo=file_foto,
            caption=didascalia,
            parse_mode="HTML",
        )


async def main():
    if TELEGRAM_TOKEN == "INCOLLA_QUI_IL_TOKEN_BOTFATHER":
        raise RuntimeError(
            "Devi inserire il token Telegram dentro bot.py."
        )

    print("Bot avviato. Pubblico subito la prima offerta.")
    catalogo = ottieni_catalogo_reale()
    indice = 0

    while True:
        try:
            prodotto = catalogo[indice]
            print(f"Pubblicazione: {prodotto['titolo']}")
            await invia_offerta(prodotto)

            indice = (indice + 1) % len(catalogo)
            print(
                f"Offerta inviata. Prossima tra "
                f"{INTERVALLO_MINUTI} minuti."
            )
            await asyncio.sleep(INTERVALLO_MINUTI * 60)

        except Exception as errore:
            print(f"ERRORE: {type(errore).__name__}: {errore}")
            await asyncio.sleep(60)


if __name__ == "__main__":
    server = threading.Thread(target=run_flask, daemon=True)
    server.start()
    asyncio.run(main())
