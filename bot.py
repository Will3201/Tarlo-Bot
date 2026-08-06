import time
import asyncio
import threading
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from telegram import Bot
from flask import Flask

# --- CONFIGURAZIONE PERSONALE ---
TELEGRAM_TOKEN = "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU"
CANALE_CHAT_ID = "@TarloDelRisparmio"  
AMAZON_TAG = "iltarlodelrisp-21"          

# Parametri di filtro e categorie mirate
SCONTO_MINIMO = 25  
CATEGORIE_ACCETTATE = ["casa", "elettrodomestici", "consumabili", "igiene e pulizia"]

bot = Bot(token=TELEGRAM_TOKEN)

# --- MINI SERVER WEB PER IL PIANO FREE DI RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Il Tarlo del Risparmio Bot è attivo e online!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)
# --------------------------------------------------
import textwrap

def crea_immagine_offerta(prodotto):
    try:
        template = Image.open("template.png").convert("RGBA")
    except Exception as e:
        print(f"ERRORE: Impossibile aprire template.png: {e}")
        template = Image.new("RGBA", (1254, 1254), "#0B3B24")
    
    draw = ImageDraw.Draw(template)
    
    # Caricamento font dimensionati per il 1254x1254
    try:
        font_titolo = ImageFont.truetype("arialbd.ttf", 42)
        font_prezzo_1 = ImageFont.truetype("arialbd.ttf", 68)
        font_prezzo_2 = ImageFont.truetype("arial.ttf", 38)
    except:
        font_titolo = ImageFont.load_default()
        font_prezzo_1 = ImageFont.load_default()
        font_prezzo_2 = ImageFont.load_default()

    # 1. SCARICA E INCOLLA LA FOTO NEL RIQUADRO BIANCO A SINISTRA (1254x1254)
    try:
        response = requests.get(prodotto['immagine_url'])
        img_prodotto = Image.open(BytesIO(response.content)).convert("RGBA")
        
        # Ridimensiona l'immagine per farla entrare perfettamente nel box bianco
        img_prodotto = img_prodotto.resize((460, 460))
        
        # Coordinate X e Y precise per il riquadro bianco a sinistra
        template.paste(img_prodotto, (95, 290), img_prodotto)
    except Exception as e:
        print(f"Errore caricamento immagine prodotto: {e}")

    # 2. SCRIVE IL TITOLO (Area verde in alto a destra, con a capo automatico)
    # Calcola le righe in base alla larghezza dello spazio disponibile
    titolo_testo = prodotto['titolo']
    linee_titolo = textwrap.wrap(titolo_testo, width=28) # Circa 28 caratteri per riga
    
    y_testo = 310
    for linea in linee_titolo[:3]: # Stampa massimo 3 righe per sicurezza
        draw.text((610, y_testo), linea, fill="white", font=font_titolo)
        y_testo += 52 # Spaziatura verticale tra una riga e l'altra

    # 3. SCRIVE IL PREZZO SCONTATO (Nel box arancione in basso a destra)
    draw.text((670, 725), str(prodotto['prezzo_attuale']), fill="white", font=font_prezzo_1)

    # 4. SCRIVE IL PREZZO PIENO BARRATO (Nel box bianco sotto "INVECE DI")
    draw.text((830, 895), str(prodotto['prezzo_precedente']), fill="#333333", font=font_prezzo_2)
    
    # Linea rossa barrata calibrata sulla lunghezza del prezzo vecchio
    draw.line([(820, 915), (1000, 915)], fill="red", width=5)

    # Salva l'immagine finale pronta per Telegram
    percorso_finale = "offerta_finale.png"
    template.convert("RGB").save(percorso_finale)
    return percorso_finale

def simula_ricerca_offerte():
    return [
        {
            "titolo": "Detersivo Lavatrice Liquido 100 Lavaggi + Carta Igienica Scorta",
            "categoria": "Consumabili",
            "sconto": 30,
            "asin": "B07XYZ1234",
            "prezzo_attuale": "14,99€",
            "prezzo_precedente": "21,99€",
            "immagine_url": "https://m.media-amazon.com/images/I/71s5834e2bL._AC_SL1500_.jpg"
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
    print("Il Tarlo del Risparmio - Bot avviato con Web Server integrato!")
    inviati = set()
    
    while True:
        try:
            offerte = simula_ricerca_offerte()
            for off in offerte:
                if off["categoria"].lower() in CATEGORIE_ACCETTATE and off["sconto"] >= SCONTO_MINIMO:
                    if off["asin"] not in inviati:
                        await invia_offerta(off)
                        inviati.add(off["asin"])
            await asyncio.sleep(10) 
        except Exception as e:
            print(f"Errore nel ciclo: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    # Avvia Flask in un thread separato così non blocca il bot di Telegram
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    # Avvia il loop principale del bot
    asyncio.run(main())
