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
TELEGRAM_TOKEN = "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU"
CANALE_CHAT_ID = "@TarloDelRisparmii"
AMAZON_TAG = "tarlodelrispa-21"
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
        if grassetto else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",

        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if grassetto else
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for percorso in percorsi:
        if Path(percorso).exists():
            return ImageFont.truetype(percorso, dimensione)

    return ImageFont.load_default()


def spezza_testo(draw, testo, font, larghezza_massima):
    parole = str(testo).split()
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
        raise ValueError(
            f"L'indirizzo non restituisce un'immagine: {tipo}"
        )

    return Image.open(BytesIO(risposta.content)).convert("RGBA")


def centra_testo(draw, testo, box, font, colore):
    x1, y1, x2, y2 = box
    bbox = draw.textbbox((0, 0), testo, font=font)

    larghezza = bbox[2] - bbox[0]
    altezza = bbox[3] - bbox[1]

    x = x1 + ((x2 - x1) - larghezza) // 2
    y = y1 + ((y2 - y1) - altezza) // 2 - bbox[1]

    draw.text((x, y), testo, fill=colore, font=font)


def crea_immagine_offerta(prodotto):
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"template.png non trovato: {TEMPLATE_PATH}"
        )

    template = Image.open(TEMPLATE_PATH).convert("RGBA")
    template = template.resize(
        (1536, 1536),
        Image.Resampling.LANCZOS,
    )

    draw = ImageDraw.Draw(template)

    # ========================================================
    # 1. IMMAGINE DEL PRODOTTO
    # Riquadro bianco: circa x 30-835, y 305-1300
    # ========================================================
    try:
        foto = scarica_immagine(
            str(prodotto.get("immagine_url", ""))
        )

        if foto:
            foto = ImageOps.contain(
                foto,
                (690, 850),
                method=Image.Resampling.LANCZOS,
            )

            x = 425 - foto.width // 2
            y = 800 - foto.height // 2

            template.alpha_composite(foto, (x, y))

    except Exception as errore:
        print(f"Errore immagine prodotto: {errore}")

        messaggio = "IMMAGINE\nNON DISPONIBILE"
        font_errore = carica_font(48, True)

        bbox = draw.multiline_textbbox(
            (0, 0),
            messaggio,
            font=font_errore,
            spacing=10,
            align="center",
        )

        larghezza = bbox[2] - bbox[0]
        altezza = bbox[3] - bbox[1]

        draw.multiline_text(
            (
                425 - larghezza // 2,
                800 - altezza // 2,
            ),
            messaggio,
            fill="#777777",
            font=font_errore,
            spacing=10,
            align="center",
        )

    # ========================================================
    # 2. TITOLO NEL RIQUADRO VERDE VUOTO
    # Box: circa x 865-1450, y 350-595
    # ========================================================
    titolo = str(prodotto.get("titolo", "")).strip()
    sottotitolo = str(
        prodotto.get("sottotitolo", "")
    ).strip()

    box_titolo = (885, 365, 1435, 540)

    font_titolo = carica_font(58, True)
    righe = spezza_testo(
        draw,
        titolo,
        font_titolo,
        box_titolo[2] - box_titolo[0],
    )

    while len(righe) > 3 and font_titolo.size > 30:
        font_titolo = carica_font(
            font_titolo.size - 2,
            True,
        )
        righe = spezza_testo(
            draw,
            titolo,
            font_titolo,
            box_titolo[2] - box_titolo[0],
        )

    altezza_riga = int(font_titolo.size * 1.13)
    altezza_totale = len(righe[:3]) * altezza_riga

    y = box_titolo[1] + max(
        0,
        (
            (box_titolo[3] - box_titolo[1])
            - altezza_totale
        ) // 2,
    )

    for riga in righe[:3]:
        draw.text(
            (box_titolo[0], y),
            riga,
            fill="white",
            font=font_titolo,
        )
        y += altezza_riga

    # Sottotitolo nella striscia verde vuota sotto.
    if sottotitolo:
        box_sottotitolo = (885, 535, 1435, 590)

        font_sottotitolo = carica_font(31, True)

        while (
            draw.textbbox(
                (0, 0),
                sottotitolo,
                font=font_sottotitolo,
            )[2]
            > box_sottotitolo[2] - box_sottotitolo[0]
            and font_sottotitolo.size > 20
        ):
            font_sottotitolo = carica_font(
                font_sottotitolo.size - 1,
                True,
            )

        centra_testo(
            draw,
            sottotitolo.upper(),
            box_sottotitolo,
            font_sottotitolo,
            "#9ACA3C",
        )

    # ========================================================
    # 3. PREZZO ATTUALE
    # Nel box bianco a destra del simbolo €
    # Scrivere solo il numero, senza simbolo euro.
    # ========================================================
    prezzo_attuale = str(
        prodotto.get("prezzo_attuale", "")
    ).replace("€", "").strip()

    box_prezzo = (1065, 1050, 1435, 1155)
    font_prezzo = carica_font(76, True)

    while (
        draw.textbbox(
            (0, 0),
            prezzo_attuale,
            font=font_prezzo,
        )[2]
        > box_prezzo[2] - box_prezzo[0] - 20
        and font_prezzo.size > 40
    ):
        font_prezzo = carica_font(
            font_prezzo.size - 2,
            True,
        )

    centra_testo(
        draw,
        prezzo_attuale,
        box_prezzo,
        font_prezzo,
        "#0D4B35",
    )

    # ========================================================
    # 4. PREZZO PRECEDENTE
    # Nel box bianco sotto, senza simbolo euro.
    # ========================================================
    prezzo_precedente = str(
        prodotto.get("prezzo_precedente", "")
    ).replace("€", "").strip()

    box_vecchio = (1210, 1215, 1435, 1285)
    font_vecchio = carica_font(42, True)

    while (
        draw.textbbox(
            (0, 0),
            prezzo_precedente,
            font=font_vecchio,
        )[2]
        > box_vecchio[2] - box_vecchio[0] - 10
        and font_vecchio.size > 28
    ):
        font_vecchio = carica_font(
            font_vecchio.size - 1,
            True,
        )

    centra_testo(
        draw,
        prezzo_precedente,
        box_vecchio,
        font_vecchio,
        "#143D2D",
    )

    bbox_vecchio = draw.textbbox(
        (0, 0),
        prezzo_precedente,
        font=font_vecchio,
    )

    larghezza_vecchio = (
        bbox_vecchio[2] - bbox_vecchio[0]
    )

    centro_x = (
        box_vecchio[0] + box_vecchio[2]
    ) // 2

    linea_y = (
        box_vecchio[1] + box_vecchio[3]
    ) // 2

    draw.line(
        (
            centro_x - larghezza_vecchio // 2 - 8,
            linea_y,
            centro_x + larghezza_vecchio // 2 + 8,
            linea_y,
        ),
        fill="#E23B27",
        width=7,
    )

    template.convert("RGB").save(
        OUTPUT_PATH,
        "PNG",
        optimize=True,
    )

    return OUTPUT_PATH


def ottieni_catalogo_reale():
    return [
        {
            "titolo": "Dash Pods Detersivo Lavatrice",
            "sottotitolo": "54 lavaggi",
            "categoria": "Casa",
            "sconto": 25,
            "asin": "B0BT7V2P2Q",
            "prezzo_attuale": "18,99",
            "prezzo_precedente": "25,99",
            "immagine_url": (
                "https://m.media-amazon.com/images/I/"
                "71XgG9sWc1L._AC_SL1500_.jpg"
            ),
        },
        {
            "titolo": "Fairy Platinum Plus",
            "sottotitolo": "84 capsule lavastoviglie",
            "categoria": "Casa",
            "sconto": 30,
            "asin": "B08XN3Z699",
            "prezzo_attuale": "21,99",
            "prezzo_precedente": "31,49",
            "immagine_url": (
                "https://m.media-amazon.com/images/I/"
                "81q2Kx5yS2L._AC_SL1500_.jpg"
            ),
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
        f"💰 <s>{html.escape(str(prodotto['prezzo_precedente']))} €</s> "
        f"➜ <b>{html.escape(str(prodotto['prezzo_attuale']))} €</b>\n\n"
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

            print(
                f"Pubblicazione: {prodotto['titolo']}"
            )

            await invia_offerta(prodotto)

            indice = (indice + 1) % len(catalogo)

            print(
                f"Offerta inviata. Prossima tra "
                f"{INTERVALLO_MINUTI} minuti."
            )

            await asyncio.sleep(
                INTERVALLO_MINUTI * 60
            )

        except Exception as errore:
            print(
                f"ERRORE: "
                f"{type(errore).__name__}: {errore}"
            )

            await asyncio.sleep(60)


if __name__ == "__main__":
    server = threading.Thread(
        target=run_flask,
        daemon=True,
    )

    server.start()
    asyncio.run(main())
