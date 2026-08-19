import os
import textwrap
from PIL import Image, ImageDraw, ImageFont

def ottieni_percorso_font():
    """
    Cerca il font Montserrat-ExtraBold caricato nella cartella 'fonts/'.
    Se non lo trova, prova con font alternativi di sistema o torna a None.
    """
    percorsi_possibili = [
        os.path.join("fonts", "Montserrat-ExtraBold.ttf"),
        os.path.join("fonts", "Montserrat-Bold.ttf"),
        "Montserrat-ExtraBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" # Fallback Linux
    ]
    
    for path in percorsi_possibili:
        if os.path.exists(path):
            return path
            
    return None

def genera_grafica_offerta(
    sfondo_path,
    immagine_prodotto_path,
    titolo_prodotto,
    prezzo_attuale,
    prezzo_originale=None,
    percentuale_sconto=None,
    output_path="offerta_finale.png"
):
    """
    Genera l'immagine con le scritte in grassetto, bordo 3D e perfetto allineamento.
    """
    # 1. Caricamento dello Sfondo
    if os.path.exists(sfondo_path):
        img = Image.open(sfondo_path).convert("RGBA")
    else:
        # Tela verde di riserva in caso di mancanza del file sfondo
        img = Image.new("RGBA", (1080, 1080), (15, 60, 30, 255))
        
    draw = ImageDraw.Draw(img)

    # 2. Caricamento del Font Locale
    font_path = ottieni_percorso_font()
    
    if font_path:
        font_titolo = ImageFont.truetype(font_path, 34)
        font_prezzo = ImageFont.truetype(font_path, 85)     # Prezzo grande box arancione
        font_barrato = ImageFont.truetype(font_path, 50)    # Prezzo vecchio box bianco
        font_sconto = ImageFont.truetype(font_path, 95)     # Percentuale sconto
    else:
        print("ATTENZIONE: Font non trovato! Ricordati di caricare 'Montserrat-ExtraBold.ttf' nella cartella 'fonts/'.")
        font_titolo = font_prezzo = font_barrato = font_sconto = ImageFont.load_default()

    # 3. Inserimento Immagine del Prodotto (a sinistra)
    if os.path.exists(immagine_prodotto_path):
        prod_img = Image.open(immagine_prodotto_path).convert("RGBA")
        prod_img.thumbnail((380, 380), Image.Resampling.LANCZOS)
        
        p_w, p_h = prod_img.size
        pos_x = int(255 - p_w / 2)
        pos_y = int(520 - p_h / 2)
        img.paste(prod_img, (pos_x, pos_y), prod_img)

    # Coordinata X centrale per tutti gli elementi sulla destra
    X_CENTRO = 660  

    # 4. TITOLO SUL CARTELLINO VERDE (A capo automatico, Testo BIANCO con ombra)
    righe_titolo = textwrap.wrap(titolo_prodotto, width=20)[:4] # Max 4 righe
    start_y = 410 - (len(righe_titolo) * 18)
    
    for i, riga in enumerate(righe_titolo):
        draw.text(
            (X_CENTRO, start_y + (i * 38)),
            riga,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 100),
            font=font_titolo,
            anchor="mm"
        )

    # 5. PREZZO SPECIALE (Box Arancione - Grassetto e Bordo 3D)
    testo_prezzo = f"{prezzo_attuale:.2f} €".replace(".", ",")
    draw.text(
        (X_CENTRO, 515),
        testo_prezzo,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(230, 100, 0, 255),
        font=font_prezzo,
        anchor="mm"
    )

    # 6. PREZZO ORIGINALE BARRATO (Box Bianco)
    if prezzo_originale:
        testo_barrato = f"{prezzo_originale:.2f} €".replace(".", ",")
        draw.text(
            (X_CENTRO, 578),
            testo_barrato,
            fill=(20, 20, 20, 255),  # Testo scuro per il contrasto sul box chiaro
            stroke_width=1,
            stroke_fill=(0, 0, 0, 255),
            font=font_barrato,
            anchor="mm"
        )
        
        # Linea Rossa di Sbarramento
        bbox = draw.textbbox((X_CENTRO, 578), testo_barrato, font=font_barrato, anchor="mm")
        draw.line(
            [(bbox[0] - 8, bbox[1] + (bbox[3]-bbox[1])/2), (bbox[2] + 8, bbox[1] + (bbox[3]-bbox[1])/2)],
            fill=(220, 30, 30, 255),
            width=5
        )

    # 7. PERCENTUALE SCONTO (Banner Arancione in Basso)
    if percentuale_sconto:
        testo_sconto = f"-{abs(percentuale_sconto)}%"
        draw.text(
            (X_CENTRO, 628),
            testo_sconto,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(200, 70, 0, 255),
            font=font_sconto,
            anchor="mm"
        )

    img.save(output_path, "PNG")
    print(f"Immagine generata con successo: {output_path}")
    return output_path


def genera_testo_telegram(titolo, asin, prezzo_attuale, prezzo_originale=None, percentuale_sconto=None):
    """
    Genera il testo formattato del messaggio Telegram.
    """
    link_affiliato = f"https://www.amazon.it/dp/{asin}?tag=tarlodelrispa-21"
    
    messaggio = f"🪵 **{titolo}**\n\n"
    
    if percentuale_sconto and prezzo_originale:
        messaggio += f"📉 **Sconto:** -{abs(percentuale_sconto)}%\n"
        messaggio += f"❌ **Invece di:** ~~{prezzo_originale:.2f} €~~\n".replace(".", ",")
    
    messaggio += f"💰 **Prezzo speciale:** {prezzo_attuale:.2f} €\n".replace(".", ",")
    messaggio += f"👉 **Acquista ora:** {link_affiliato}\n\n"
    messaggio += "#IlTarloDelRisparmio"
    
    return messaggio


# --- ESEMPIO DI ESECUZIONE MAIN ---
if __name__ == "__main__":
    # Dati di test
    asin_prodotto = "B0GWQWGWFH"
    titolo_test = "Lefant M1 Robot Aspirapolvere Lavapavimenti 5500Pa Mappatura 3 in 1"
    prezzo_scontato = 119.98
    prezzo_listino = 169.00
    sconto = 30

    # 1. Genera la grafica
    genera_grafica_offerta(
        sfondo_path="template.png",
        immagine_prodotto_path="prodotto.png",
        titolo_prodotto=titolo_test,
        prezzo_attuale=prezzo_scontato,
        prezzo_originale=prezzo_listino,
        percentuale_sconto=sconto,
        output_path="offerta_finale.png"
    )

    # 2. Genera il messaggio
    testo_post = genera_testo_telegram(
        titolo=titolo_test,
        asin=asin_prodotto,
        prezzo_attuale=prezzo_scontato,
        prezzo_originale=prezzo_listino,
        percentuale_sconto=sconto
    )

    print("\n--- TESTO DA INVIARE SU TELEGRAM ---")
    print(testo_post)
