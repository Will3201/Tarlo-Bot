# TikTok Studio — test 9–22 ottobre 2026
L'utente autorizza la ripresa di TikTok con contenuti professionali, vari e negli orari di punta. Spesa aggiuntiva: zero. Sei pubblicazioni, poi valutazione prima di estendere. Destinazione esclusiva: TikTok @tarlodelrisparmio, Metricool brand 6911341. Le vecchie automazioni ogni sei ore e Top5 restano sospese.

## Calendario
Leggere calendar.json e questo protocollo alla HEAD verificata del ramo tarlo-publishing di Will3201/Tarlo-Bot. Usare solo la voce del giorno italiano corrente, mai recuperare giornate perse. I picchi provengono dai suggerimenti Metricool e vanno testati, non garantiscono risultati.
Pubblicare esclusivamente nella fascia 10:00–10:45 o 18:00–18:45 prevista. Se pronti prima, programmare alle 10/18; se fuori fascia saltare. Una sola pubblicazione per voce. Non aggiungere costi, altri social, annunci, musica o giveaway. Non modificare main, Render o post già pubblicati.

## Duplicazione
Chiave studio-YYYY-MM-DD, hashtag #TarloStudioYYYYMMDD. Controllare records/<chiave>.json: soltanto 404 significa assente. Verificare brand e planner (ultimi sette giorni e futuri sette), analytics TikTok TKPO02, TKPO03, TKPO05, TKPO07, TKPO08, TKPO09, TKPO10, TKPO22 nelle ultime 72 ore. Riconciliare attempted/scheduled tramite UUID, hashtag e prove reali: niente reinvio cieco. Non pubblicare la bozza privata 375828751.
Per offerte escludere ASIN pubblicati/programmati nelle ultime 24 ore e tutti gli attempted irrisolti, inclusi registri di recupero e recap. Per editoriali controllare duplicazione del contenuto, non ASIN.

## Editoriali
educational_carousel e comparison_guide non richiedono /daily/prepare: asin=null, asins=[], window_start/window_end=null, selection_scope="editoriale, nessuna offerta". Usare esattamente cards/caption del calendario. Per guide tecniche verificare i fatti su fonti ufficiali e conservare URL e data: USB-IF/produttori per USB-C, IATA/compagnia per batterie. Se non verificabili o contraddetti saltare, senza improvvisare.
Caroselli di 2–3 pagine: domanda concreta, contenuto utile, invito conclusivo a Telegram. Grafiche originali con imagegen incluso, nessun upgrade o addebito. Testi grandi e margini per TikTok, palette verde bosco/crema/arancio. Cambiare composizione fra post; evitare il solito cartellone logo+prezzo. Non inventare test personali, voce dell'utente, prodotti, recensioni, partnership o link in bio.

## Offerte
Preparare selezione fresca con POST VUOTO a https://tarlo-bot-1.onrender.com/daily/prepare senza query né Origin; una sola richiesta se esito incerto. Poll /daily/status e /daily/latest senza cache per massimo sei minuti, rispettare 429 e limite 15 minuti. Nessun riavvio.
Lo slot backend supporta 01/07/13/19 in Europe/Rome: alle 10 usare lo slot corrente 07, alle 18 lo slot 13. La chiave di pubblicazione studio è separata; non chiedere slot 10/18 al backend. Verificare status ready, day italiano, window_end entro 20 minuti dall'ora attuale e window_start=end-6h, valid_until oltre ora+5min, ASIN escluso dai duplicati e timestamp della fonte nella finestra.
Senza filesystem leggere automation/RECOVERY.md, media_worker.py, media_integrity.js e workflow prepare-publishing-media.yml alla HEAD: richiesta single fresca con slot backend ed excluded_asins, senza sovrascrivere una richiesta in corso. Non fare altri commit durante il worker. Risultato ready con publication=false significa solo preparazione.
Scegliere solo se utile al tema del giorno; se manca una scelta coerente o dati/scadenza non validi saltare. Nessun riempitivo o endpoint inventato.
Verificare hash SHA256, PNG, dimensioni e leggibilità; poi imagegen riferito al PNG verificato. Preservare fedelmente prodotto, prezzo, condizioni e riferimento consigliato/mediano. Non inventare minimi storici, migliore prezzo, coupon o errore di prezzo. Usare recensioni/acquisti nella selezione solo quando presenti. Se live_verified=false e price_source=telegram_snapshot conservare data/ora della segnalazione e avviso di ricontrollo. Caption breve: utilità concreta, prezzo e sconto con riferimento esplicito solo se presenti, CTA e disclosure. Condizioni coupon sempre conservate.

## Archivio e invio
Verificare visivamente ogni pagina e JPEG effettivo: RGB, qualità95, massimo lato1080, proporzioni invariate. Conservare SHA256 originali/derivati/JPEG. Se manca filesystem usare worker convert da PNG archiviato in prepared/ o media/ con hash indipendente verificato; convert non esegue /daily/prepare.
Archiviare JPEG media/studio-YYYY-MM-DD-{1,2,3}.jpg e record attempted PRIMA dell'invio: experiment_id, format, caption, ASIN array, fonti, timestamps, hash, asset, backend_slot se offerta, publication_date, stato. GitHub create_blob(base64), create_tree, create_commit, update_ref force=false da HEAD verificata; se cambia fermarsi e riconciliare.
createScheduledPost blogId "6911341", date ISO con offset italiano, info con autoPublish=true, draft=false, providers=[{network:"tiktok"}], publicationDate={dateTime:locale senza offset,timezone:"Europe/Rome"}, text=caption+#offerte #amazon #risparmio #TarloStudioYYYYMMDD, media=[URL raw immutabili dei JPEG al commit archivio], mediaAltText=[], descendants=[], firstCommentText="", hasNotReadNotes=false, shortener=false, smartLinkData={ids:[]}.
tiktokData={privacyOption:"PUBLIC_TO_EVERYONE",commercialContentThirdParty:true,commercialContentOwnBrand:false,disableDuet:true,disableStitch:true,disableComment:false,autoAddMusic:false,photoCoverIndex:0,title:titolo breve}.
Omettere mediaFiles per trasporto URL. Alternativa: soltanto JPEG locali reali in mediaFiles e media=[]; mai URL in mediaFiles, mai entrambe le vie.
Risposta positiva: scheduled con ID/UUID/planner URL; rileggere planner e verificare testo, destinazione, importazione di tutte le pagine, fascia. Published e publicUrl solo con prova reale. Esito incerto resta attempted/scheduled senza nuovo invio. Se verifica, archivio o caricamento impossibili saltare e spiegare il blocco preciso, non riusare vecchie grafiche.
CTA t.me/TarloDelRisparmio o "Cerca @TarloDelRisparmio su Telegram". Disclosure "Prezzi e disponibilità possono variare. Nel canale usiamo link affiliati." Non inventare link d'invito o tracking; non promettere iscrizioni certe.

## Analisi
A 48–72 ore misurare views, like, commenti, condivisioni e tempi di visione/completamento quando disponibili, confrontando età simili dei post. Visite profilo/follower solo dalle metriche ufficiali disponibili. Dati mancanti non sono zero.
Conteggi Telegram soltanto verificati e con timestamp; incremento netto non è attribuzione alle promozioni. Niente commissioni/vendite attribuite senza report Amazon. Rivalutare qualità e segnali verso il canale prima di continuare, non le sole views.
