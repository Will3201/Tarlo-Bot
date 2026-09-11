"""Filtri pesca e coupon letti soltanto dal blocco del prodotto Amazon."""
import re
from coupon_notice import estrai_coupon

SPECIFICI = r'mulinell\w*|lenz\w*|guadin\w*|waders?|surfcasting|carpfishing|fishfinder|ecoscandagli\w*|boilies|bivvy|rapala|daiwa|minn?ow|wobbler|crankbait|swimbait|jerkbait|spinnerbait|chatterbait|buzzbait|spomb|spod|fishing|lures?|baits?|tackle|bedchair'
CONTESTO = r'cann[ae]|am[oi]|esc(?:a|he)|fil[oi]|bors[ae]|zain\w*|cassetta|stival\w*|guant\w*|occhial\w*|giacc\w*|gilet|torc\w*|kayak|galleggiant\w*|piomb\w*|girell\w*|abbigliament\w*|pastur\w*|pellet|seggiolin\w*|sedi[ae]|avvisator\w*|artificial\w*'


def e_pesca(title):
    title = title.lower()
    if re.search(r'\b(?:'+SPECIFICI+r')\b', title):
        return True
    if re.search(r'\b(?:rod pod|bank sticks?|buzz bar|bite alarm|rod rests?|unhooking mat|method feeder)\b', title):
        return True
    return bool(re.search(r'\bpesca\b', title) and re.search(r'\b(?:'+CONTESTO+r')\b', title))


def coupon_amazon(soup):
    # Niente testo dell'intera pagina: potrebbe appartenere a prodotti correlati.
    for node in soup.select('#couponFeature, #coupon_feature_div, #promoPriceBlockMessage_feature_div'):
        if node.select_one('[disabled]') or node.get('aria-hidden') == 'true':
            continue
        text = node.get_text(' ', strip=True)
        if not re.search(r'\bcoupon\b', text, re.I) or re.search(r'scadut|non disponibile|esaurit', text, re.I):
            continue
        value = estrai_coupon(text)
        if value is None:
            amounts = re.findall(r'\d+(?:[,.]\d{1,2})?\s*(?:€|%)', text)
            if len(set(amounts)) == 1:
                value = estrai_coupon('Coupon '+amounts[0])
        if value:
            value['fonte'] = 'pagina_amazon'
            value['casella'] = bool(node.select_one('input[type="checkbox"]')) or value['casella']
            # Osservato nella pagina, non verificato al checkout per ogni account.
            return value
    return None
