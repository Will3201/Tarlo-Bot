import time
import asyncio
import threading
import textwrap
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from telegram import Bot
from flask import Flask

# --- CONFIGURAZIONE PERSONALE ---
TELEGRAM_TOKEN = "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU"
CANALE_CHAT_ID = "@IlTarloDelRisparmio"  
AMAZON_TAG = "iltarlodelrisp-21"          

bot = Bot(token=TELEGRAM_TOKEN)

# --- MINI SERVER WEB PER IL PIANO FREE DI RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Il Tarlo del Risparmio - Bot Curato Definitivo attivo e online!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)
# --------------------------------------------------

def crea_immagine_offerta(prodotto):
    try:
        template = Image.open("template.png").convert("RGBA")
    except Exception as e:
        print(f"ERRORE: Impossibile aprire template.png: {e}")
        template = Image.new("RGBA", (1254, 1254), "#0B3B24")
    
    draw = ImageDraw.Draw(template)
    
    try:
        font_titolo = ImageFont.truetype("arialbd.ttf", 42)
        font_prezzo_1 = ImageFont.truetype("arialbd.ttf", 68)
        font_prezzo_2 = ImageFont.truetype("arial.ttf", 38)
    except:
        font_titolo = ImageFont.load_default()
        font_prezzo_1 = ImageFont.load_default()
        font_prezzo_2 = ImageFont.load_default()

    # Scarica la foto del prodotto con gli Headers per sicurezza
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(prodotto['immagine_url'], headers=headers)
        img_prodotto = Image.open(BytesIO(response.content)).convert("RGBA")
        img_prodotto = img_prodotto.resize((460, 460))
        template.paste(img_prodotto, (95, 290), img_prodotto)
    except Exception as e:
        print(f"Errore caricamento immagine prodotto: {e}")

    # Scrive il titolo con a capo automatico nel riquadro verde
    titolo_testo = prodotto['titolo']
    linee_titolo = textwrap.wrap(titolo_testo, width=28)
    
    y_testo = 310
    for linea in linee_titolo[:3]:
        draw.text((610, y_testo), linea, fill="white", font=font_titolo)
        y_testo += 52

    # Scrive il prezzo scontato nel box arancione
    draw.text((670, 725), str(prodotto['prezzo_attuale']), fill="white", font=font_prezzo_1)

    # Scrive il prezzo pieno barrato
    draw.text((830, 895), str(prodotto['prezzo_precedente']), fill="#333333", font=font_prezzo_2)
    draw.line([(820, 915), (1000, 915)], fill="red", width=5)

    percorso_finale = "offerta_finale.png"
    template.convert("RGB").save(percorso_finale)
    return percorso_finale

def ottieni_catalogo():
    # Elenco dei prodotti monitorati (puoi aggiungerne quanti ne vuoi)
    return [
        {
            "titolo": "Dash Pods Detersivo Lavatrice, 54 Lavaggi, Pods All in 1 Pods Regular",
            "categoria": "Consumabili",
            "sconto": 25,
            "asin": "B0BT7V2P2Q",
            "prezzo_attuale": "18,99€",
            "prezzo_precedente": "25,99€",
            "immagine_url": "https://m.media-amazon.com/images/I/71XgG9sWc1L._AC_SL1500_.jpg"
        },
        {
            "titolo": "Fairy Platinum Plus Pastiglie Lavastoviglie, 84 Caps, Limone",
            "categoria": "Consumabili",
            "sconto": 30,
            "asin": "B08XN3Z699",
            "prezzo_attuale": "21,99€",
            "prezzo_precedente": "31,49€",
            "immagine_url": "https://m.media-amazon.com/images/I/81q2Kx5yS2L._AC_SL1500_.jpg"
        },
        {
            "titolo": "Scottonelle Carta Igienica, 96 Rotoli, Morbidezza e Resistenza",
            "categoria": "Casa",
            "sconto": 20,
            "asin": "B07H8Q4Z12",
            "prezzo_attuale": "29,99€",
            "prezzo_precedente": "37,99€",
            "immagine_url": "https://m.media-amazon.com/images/I/71Y+gL33cZL._AC_SL1500_.jpg"
        }
    ]

async def invia_offerta(prodotto):
    link_affiliato = f"https://www.amazon.it/dp/{prodotto['asin']}?tag={AMAZON_TAG}"
    percorso_foto = crea_immagine_offerta(prodotto)
    
    didascalia = (
        f"🐜 **Il Tarlo ha colpito ancora!**\n\n"
        f"📦 **{prodotto['titolo']}**\n"
        f"📉 Sconto bomba: **-{prodotto['sconto']}%**\n"
        f"💰 Crollo prezzo: ~~{prodotto['prezzo_precedente']}~~ ➔ **{prodotto['prezzo_attuale']}**\n\n"
        f"👉 [ACQUISTA SUBITO IN OFFERTA]({link_affiliato})\n\n"
        f"In qualità di Affiliato Amazon ricevo un guadagno dagli acquisti idonei.\n"
        f"#IlTarloDelRisparmio #Casa #{prodotto['categoria']}"
    )
    
    with open(percorso_foto, 'rb') as foto_file:
        await bot.send_photo(
            chat_id=CANALE_CHAT_ID,
            photo=foto_file,
            caption=didascalia,
            parse_mode="Markdown"
        )

async def main():
    print("Il Tarlo del Risparmio - Bot avviato con successo!")
    catalogo = ottieni_catalogo()
    indice = 0
    
    while True:
        try:
            prodotto_corrente = catalogo[indice]
            print(f"Pubblicando: {prodotto_corrente['titolo']}")
            
            await invia_offerta(prodotto_corrente)
            
            # Passa ciclicamente al prodotto successivo della lista
            indice = (indice + 1) % len(catalogo)
            
            # Attende 30 minuti (1800 secondi) prima della prossima offerta
            await asyncio.sleep(1800) 
            
        except Exception as e:
            print(f"Errore nel ciclo principale: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    asyncio.run(main())
    
