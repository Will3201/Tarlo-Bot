"""Coupon osservati su Amazon e prezzo condizionato alla loro attivazione."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def leggi_coupon_amazon(soup):
    found = []
    for node in soup.select('#couponFeature, #coupon_feature_div, #promoPriceBlockMessage_feature_div'):
        text = node.get_text(' ', strip=True)
        hidden = any(p.get('aria-hidden') == 'true' or p.has_attr('hidden') or
                     re.search(r'display\s*:\s*none', p.get('style', ''), re.I)
                     for p in [node, *node.parents] if hasattr(p, 'get'))
        if hidden or node.select_one('[disabled]'):
            continue
        if not re.search(r'\bcoupon\b', text, re.I):
            continue
        # Non sommare promozioni riservate ad abbonamenti o acquisti multipli.
        if re.search(r'scadut|non disponibile|esaurit|iscriviti|abbonament|subscribe|prima consegna|primo ordine|almeno|acquist[ai].*\b[2-9]\b', text, re.I):
            continue
        values = set(re.findall(r'(\d+(?:[,.]\d{1,2})?)\s*(€|%|euro\b)', text, re.I))
        if len(values) != 1:
            continue
        amount, unit = values.pop()
        found.append((amount.replace(',', '.'), '%' if unit == '%' else '€'))
    if len(set(found)) != 1:
        return None
    amount, unit = found[0]
    return {'importo': amount.replace('.', ','), 'unita': unit, 'casella': True,
            'fonte': 'pagina_amazon', 'verificato_amazon': True}


def prezzo_con_coupon(prodotto):
    """Copia per immagine/caption; conserva i prezzi originali nell'archivio."""
    coupon = prodotto.get('coupon') or {}
    if coupon.get('fonte') != 'pagina_amazon' or not coupon.get('verificato_amazon'):
        return prodotto
    try:
        price = Decimal(str(prodotto['prezzo_attuale']).replace(',', '.'))
        amount = Decimal(str(coupon['importo']).replace(',', '.'))
        unit = coupon['unita']
        if not price.is_finite() or not amount.is_finite() or price <= 0 or amount <= 0:
            return prodotto
        if unit not in ('€', '%') or (unit == '%' and amount >= 100):
            return prodotto
        saving = (price * amount / 100 if unit == '%' else amount).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if saving <= 0 or saving >= price:
            return prodotto
        final = price - saving
        percent = int((saving / price * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, KeyError, ValueError, TypeError):
        return prodotto
    return {**prodotto, 'prezzo_attuale': str(final.quantize(Decimal('.01'))).replace('.', ','),
            'prezzo_precedente': str(price.quantize(Decimal('.01'))).replace('.', ','),
            'sconto': percent, 'coupon_applicato': True}
