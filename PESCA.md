# Bot pesca: ricerca autonoma Amazon

Il servizio Render TarloPesca-Bot esegue `python bot_pesca.py` e pubblica su
`CANALE_CHAT_ID_PESCA`. Non usa Telethon o messaggi di altri canali.

## Modifiche dell'11 settembre 2026

- Template pesca conservato. Immagini ritagliate solo nei margini bianchi o
  trasparenti, ingrandite senza deformazione, sotto il logo e sopra le decorazioni.
- Titoli su un massimo di tre righe, prezzi e percentuali adattati in pixel alle
  rispettive aree. Titoli molto lunghi abbreviati con ellissi solo nella grafica.
- Foto composte in memoria: niente file condiviso e niente post senza immagine
  quando il download fallisce.
- Coupon letti solo dai blocchi dedicati nella pagina Amazon del prodotto.
  Caption con emoji, importo quando riconosciuto e istruzioni; il prezzo non viene
  ricalcolato e la validità per l'account al checkout non è garantita.
- Filtro pesca più selettivo: un marchio o termini ambigui come canna, piombo,
  spinning o cucchiaino da soli non bastano.
- ASIN selezionato, prezzo principale, disponibilità e riferimento dello sconto
  devono essere riconoscibili. Soglia mantenuta al 15% sul riferimento Amazon.
  Nessuna dichiarazione automatica di errore di prezzo o minimo storico.
- Fino a 15 risultati per ricerca, ordinati con `tarlo_daily.valuta`: 50 punti
  sconto, 20 valutazione corretta per numero recensioni, 15 volume recensioni,
  15 acquisti mensili. Le metriche assenti non vengono inventate.
- Al massimo 3 pubblicazioni per ricerca, configurabile con
  `MAX_POST_PER_QUERY_PESCA` (1–15). È una classifica dei candidati esaminati,
  non una ricerca esaustiva di tutte le offerte Amazon. Prezzo e disponibilità
  ricontrollati prima di ogni invio.
- ASIN già esaminati saltati nel resto del ciclo. Il blocco di 72 ore si applica
  dopo un invio riuscito o ambiguo, non dopo uno scraping fallito. Niente retry
  ciechi di un invio Telegram con timeout. Errori isolati non fermano il ciclo.
- CAPTCHA, HTTP 429 e 503 fanno interrompere il ciclo e attendere 30 minuti.
  Non sono previsti servizi a pagamento o aggiramenti dei blocchi Amazon.
- Usa `PORT` di Render, con fallback a `PORT_PESCA`.

## Limiti operativi

La ricerca dipende dall'HTML pubblico di Amazon: pagine incomplete, cambi di
layout e blocchi possono impedire la selezione. Con Render Free e SQLite locale,
lo storico non è garantito dopo i deploy; il bot conserva il supporto al database
Postgres già previsto tramite DATABASE_URL_PESCA. Nessuna nuova risorsa acquistata.
Il flusso TikTok del Risparmio rimane distinto: nessun post pesca viene inviato
al profilo TikTok del Risparmio da questo aggiornamento.

Verifica offline: `python -m unittest discover -s tests -p test_pesca.py -q`.
