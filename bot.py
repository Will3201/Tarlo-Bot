import time
import asyncio
import threading
import textwrap
from PIL import Image, ImageDraw, ImageFont
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from telegram import Bot
from flask import Flask

# --- CONFIGURAZIONE PERSONALE ---
TELEGRAM_TOKEN = "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU"
CANALE_CHAT_ID = "@TarloDelRisparmio"  
AMAZON_TAG = "iltarlodelrisp-21"          

bot = Bot(token=TELEGRAM_TOKEN)

# --- MINI SERVER WEB PER IL PIANO FREE DI RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Il Tarlo del Risparmio - Bot Automatico Gratuito attivo e online!"

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

    # Scarica la foto del prodotto reale da Amazon
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
        response = requests.get(prodotto['immagine_url'], headers=headers)
        img_prodotto = Image.open(BytesIO(response.content)).convert("RGBA")
        img_prodotto = img_prodotto.resize((460, 460))
        template.paste(img_prodotto, (95, 290), img_prodotto)
    except Exception as e:
        print(f"Errore caricamento immagine prodotto: {e}")

    # Scrive il titolo con a capo automatico
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

def cerca_offerte_automatiche():
    offerte_trovate = []
    try:
        # Pagina delle offerte del giorno di Amazon Italia
        url = "https://www.amazon.it/gp/goldbox"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Errore di connessione ad Amazon: Status {response.status_code}")
            return []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Cerca i blocchi delle offerte nella pagina (la struttura di Amazon può variare, prendiamo i box generici)
        items = soup.select('.Grid-child') or soup.select('.DealGridItem-module__dealItemStyle')
        
        for item in items[:10]: # Analizza i primi 10 elementi
            try:
                # Estrae il titolo
                title_elem = item.select_string('a.a-link-normal') or item.select_one('.a-size-base')
                if not title_elem:
                    continue
                titolo = title_elem.get_text(strip=True)
                
                # Estrae il link e l'ASIN
                link_elem = item.select_one('a.a-link-normal')
                if not link_elem or 'href' not in link_elem.attrs:
                    continue
                href = link_elem['href']
                
                if "/dp/" in href:
                    asin = href.split("/dp/")[1].split("/")[0].split("?")[0]
                else:
                    continue
                
                # Estrae l'immagine
                img_elem = item.select_one('img')
                img_url = img_elem['src'] if img_elem and 'src' in img_elem.attrs else ""
                
                # Estrae il prezzo
                price_elem = item.select_one('.a-price .a-offscreen')
                prezzo_attuale = price_elem.get_text(strip=True) if price_elem else "0,00€"
                
                # Per sicurezza sui dati minimi, se abbiamo trovato un ASIN valido aggiungiamo l'offerta
                if len(asin) == 10 and img_url:
                    offerte_trovate.append({
                        "titolo": titolo[:80], # Tronca se troppo lungo
                        "categoria": "Casa",
                        "sconto": 20, # Valore indicativo di default
                        "asin": asin,
                        "prezzo_attuale": prezzo_attuale,
                        "prezzo_precedente": "Valore stimato", # Da calcolare o mostrare
                        "immagine_url": img_url
                    })
            except Exception as inner_e:
                continue
                
    except Exception as e:
        print(f"Errore nello scraping automatico: {e}")
        
    return offerte_trovate

async def invia_offerta(prodotto):
    link_affiliato = f"https://www.amazon.it/dp/{prodotto['asin']}?tag={AMAZON_TAG}"
    percorso_foto = crea_immagine_offerta(prodotto)
    
    didascalia = (
        f"🐜 **Il Tarlo ha colpito ancora!**\n\n"
        f"📦 **{prodotto['titolo']}**\n"
        f"📉 Offerta lampo selezionata per te!\n"
        f"💰 Prezzo: **{prodotto['prezzo_attuale']}**\n\n"
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
    print("Il Tarlo del Risparmio - Bot Automatico (Gratuito) avviato!")
    inviati = set()
    
    while True:
        try:
            offerte = cerca_offerte_automatiche()
            for off in offerte:
                if off["asin"] not in inviati:
                    await invia_offerta(off)
                    inviati.add(off["asin"])
                    await asyncio.sleep(60) # Pausa tra un invio e l'altro
            
            # Controlla nuove offerte ogni 1 ora (3600 secondi)
            await asyncio.sleep(3600) 
            
        except Exception as e:
            print(f"Errore nel ciclo principale: {e}")
            await asyncio.sleep(300)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    asyncio.run(main())
    
