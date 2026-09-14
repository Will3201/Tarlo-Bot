# Recupero del trasporto TikTok — 14 settembre 2026

Il caricamento via Metricool è stato verificato prima in una bozza privata e poi nel post pubblico https://www.tiktok.com/@tarlodelrisparmio/video/7685506602736078102.
Post Metricool 375954818, UUID 2552520752056911380. La bozza tecnica 375828751 resta draft=true e autoPublish=false: non pubblicarla.
Il recupero della fascia 2026-09-14-19 è registrato come published; non ripeterlo.

## Trasporto confermato
Usare info.media=[URL JPEG pubblico immutabile archiviato su GitHub] e omettere mediaFiles.
Un URL NON è un percorso locale: non inserirlo in mediaFiles.
Metricool importa l'immagine nel proprio archivio: verificare la risposta e il planner.
L'alternativa locale resta mediaFiles=[percorso assoluto JPEG] con info.media=[].
Non usare entrambi i percorsi nello stesso invio. Non ritentare dopo un esito incerto senza riconciliazione.

## Preparazione senza filesystem locale
Sul solo ramo tarlo-publishing sono installati automation/media_worker.py e .github/workflows/prepare-publishing-media.yml.
Il workflow usa un runner standard GitHub Actions per questo repository pubblico; non richiede piani a pagamento o segreti nuovi.
Non modifica main/Render e NON pubblica sui social.
Self-test verificato: https://github.com/Will3201/Tarlo-Bot/actions/runs/34898420248
Prova live: prepared/repair-live-20260914-19/result.json.

1. Leggere HEAD, registri, planner, analytics e brand. Costruire excluded_asins con tutti i pubblicati/programmati nelle ultime 24h e gli attempted non riconciliati.
2. Prima controllare requests/current.json, i run recenti e il risultato relativo. Non sovrascrivere una richiesta in corso. Se manca il risultato, non presumere mancata esecuzione del POST.
3. Salvare requests/current.json con id univoco [a-zA-Z0-9_-] massimo70, kind="single", slot corrente, excluded_asins e requested_at ISO con fuso di adesso (validità20min). Esempio schema:
   {"id":"single-YYYYMMDDHH-univoco","kind":"single","slot":"YYYY-MM-DD-HH","excluded_asins":[],"requested_at":"ISO UTC attuale"}
   Il worker invia il POST VUOTO fisso /daily/prepare, senza query/Origin; rispetta429, non ripete POST dall'esito incerto, controlla per massimo6min.
4. Usare create_tree/create_commit/update_ref force=false alla HEAD verificata; evitare altri commit mentre il workflow è in corso, perché verifica HEAD prima dell'archivio.
5. Leggere run del branch via GitHub API e prepared/<id>/result.json. Solo status ready con feed valido autorizza i passi successivi. publication=false significa preparazione, mai pubblicazione.
6. Leggere source.png e offer.jpg da quel commit immutabile con github_fetch_file encoding=base64. Verificare autonomamente SHA256, formato, dimensioni e visualizzare l'immagine; non fidarsi del solo flag ready.
7. Conservare tutti i controlli originali: slot, ultime6h, valid_until, timestamp, deduplica, riferimenti prezzo, coupon, avvisi snapshot. Il worker non sostituisce la verifica editoriale.

## Verifica bytes senza crypto/atob o filesystem
automation/media_integrity.js è codice del progetto testato con vettore SHA256 noto e con il JPEG pubblicato, incluso rifiuto hash errato.
Leggerlo dalla HEAD verificata ed eseguire in functions JS:
const helper = Function(source + "\nreturn {decodeBase64,sha256,imageHeader,verifyImage};")();
const check = helper.verifyImage(base64, expectedSha256, {format:"JPEG",maxSide:1080});
Per PNG originale richiedere format:"PNG" e confrontare con feed.sha256.
Visualizzare i bytes con image("data:image/png;base64,"+base64) o JPEG; header/hash non sostituiscono revisione visiva e decodifica completa del worker.

## Grafica derivata e conversione
Imagegen incluso può usare il PNG verificato come riferimento, preservando prodotto/cifre. Se manca accesso gratuito usare originale verificato, segnalandolo.
Per una nuova grafica quando manca filesystem: visualizzare il PNG verificato, generare dal riferimento di conversazione e usare i bytes base64 restituiti. Non stampare base64 nei messaggi.
Archiviare il PNG generato in prepared/<id>/designed.png (create_blob base64). Calcolare SHA256 indipendente e ispezionarlo visivamente.
Inviare una seconda richiesta univoca kind="convert", source_path="prepared/<id>/designed.png", source_sha256="<hash verificato>", requested_at attuale.
Il worker legge soltanto un asset già archiviato sotto media/ o prepared/, converte JPEG RGB qualità95 max1080, preservando proporzioni.
Lo stesso metodo convert è valido per la guida del15 già archiviata; non chiamare daily/prepare per guide o recap.
Verificare l'effettivo JPEG restituito, poi archiviarlo in media/<slot>.jpg o media/recap-<giorno>.jpg insieme al record attempted PRIMA dell'invio.
Per la nuova grafica verticale verificare rapporto3:4; conservare hash originale, derivato e JPEG effettivo.

## Invio e stato
Mantenere parametri TikTok, dichiarazione commerciale, caption autorizzata e hashtag univoco.
URL media: https://raw.githubusercontent.com/Will3201/Tarlo-Bot/<commit-archivio>/media/<slot>.jpg
Programmare pochi minuti avanti e prima della scadenza; verificare planner, poi published/publicUrl soltanto con PUBLISHED confermato.
Il worker non raccoglie l'intero storico Telegram e non costruisce recap: per recap resta obbligatoria copertura completa delle fonti e cinque ASIN verificabili.
Il 14 settembre la lettura diretta dello storico pubblico Telegram ha restituito timeout: non equivale a zero offerte.
Le fasce saltate per assenza offerte non sono arretrati da riempire. Registri assenti non dimostrano offerte recuperabili.
