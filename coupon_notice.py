"""Avvisi coupon da messaggi con un solo prodotto, senza ricalcolare prezzi."""
import re


AMOUNT = r'(\d+(?:[.,]\d{1,2})?)\s*(€|%|euro\b)'


def estrai_coupon(text, numero_prodotti=1):
    if numero_prodotti != 1:
        return None  # Evita di attribuire il coupon al prodotto sbagliato.
    for line in (text or '').splitlines():
        if not re.search(r'\bcoupon\b', line, re.I):
            continue
        if re.search(r'come\s+(?:si\s+)?usa|scadut|non\s+(?:è\s+)?disponibile|senza\s+coupon|coupon\s+(?:terminato|esaurito)', line, re.I):
            continue
        values = []
        # Importo dopo la parola coupon, oppure prima in "2,34€ di sconto con il coupon".
        after = re.search(r'\bcoupon\b(?:\s+(?:casella|da|del|di|extra|pari|a|al|sconto))*\s*[:=\-]?\s*'+AMOUNT, line, re.I)
        before = re.search(AMOUNT+r'\s*(?:di\s+)?sconto\s+(?:con|tramite)\s+(?:il\s+)?coupon\b', line, re.I)
        for match in (after, before):
            if match:
                amount, unit = match.groups()
                unit = '€' if unit.lower() == 'euro' else unit
                number = float(amount.replace(',', '.'))
                if number > 0 and (unit != '%' or number < 100):
                    values.append((amount.replace('.', ','), unit))
        values = list(dict.fromkeys(values))
        casella = bool(re.search(r'casella|spunta|seleziona', line, re.I))
        code = re.search(r'\b(?:codice|code)\s*[:=]?\s*[`\"\']?([A-Z0-9]{4,24})\b', line)
        if code and not any(c.isdigit() for c in code[1]):
            code = None  # Non scambiare parole generiche per codici.
        if not (values or casella or code):
            continue
        return {'importo': values[0][0] if len(values) == 1 else None,
                'unita': values[0][1] if len(values) == 1 else None,
                'casella': casella, 'codice': code[1] if code else None,
                'fonte': 'canale_origine', 'verificato_amazon': False}
    return None


def avviso_coupon(coupon):
    if not coupon:
        return ''
    value = f"{coupon['importo']} {coupon['unita']} di sconto" if coupon.get('importo') else 'da verificare su Amazon'
    text = f"🎟️ Coupon segnalato: {value}.\n"
    if coupon.get('codice'):
        text += f"🔑 Codice: {coupon['codice']}. Verifica condizioni e validità.\n"
    elif coupon.get('casella'):
        text += '✅ Se disponibile, spunta la casella coupon nella pagina Amazon.\n'
    else:
        text += '✅ Verifica su Amazon come attivarlo.\n'
    return text + 'ℹ️ Prezzo mostrato rilevato su Amazon; totale dopo coupon da verificare al carrello.\n'
