# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
# Pinnata per DIGEST oltre che per tag: un tag e' un'etichetta e si puo' ripuntare
# sotto lo stesso nome. Il ragionamento per esteso sta in docs/DECISIONS.md.
FROM prom/prometheus:v3.13.2@sha256:508729e0e2d18e11fd742a5a5ca70e557b940a93948c3c95fd0123a6fd538b69

# nobody (65534), which is what the base image already uses — and what makes the
# volume-ownership question in railway/README.md worth checking on first deploy.
USER 65534:65534

COPY docker/prometheus.yml /etc/prometheus/prometheus.yml

# Flags baked in rather than passed as a Railway startCommand: the image has an
# ENTRYPOINT, and how a platform composes its own command with an existing
# ENTRYPOINT is exactly the kind of detail that fails on the first deploy.
# --storage.tsdb.path must match the volume mount path configured on the service.
# Due limiti, non uno: vince quello che scatta per primo.
#
# Con la sola retention a tempo Prometheus riempie qualunque volume prima che i
# giorni scadano — non esiste contropressione dal disco. Il 13-08-2026 il volume
# da 500 MB si è riempito e la compaction ha iniziato a fallire una volta al
# minuto ("no space left on device"): l'ingestione reggeva ancora, perché il head
# sta in RAM, quindi da fuori non si vedeva nulla mentre la persistenza era già
# rotta. `retention.size` è il limite che quel guasto non poteva avere.
#
# I numeri di seguito valgono per un volume da 5 GB e NON si ricavano da soli: il
# 19-08-2026 il volume è passato da 500 MB a 5 GB (piano Hobby: su free/trial il
# massimo è 0.5 GB e il resize non esiste proprio) ed è stato azzerato a mano. I
# 300 MB di prima erano tarati su 500: lasciati lì avrebbero troncato la storia al
# 6% di un disco già pagato — sbagliati nella direzione opposta, ma sbagliati.
#
# 3 GB su 5 disponibili — e `3GB` qui è 3 GiB, perché Prometheus usa potenze di 2
# ("Based on powers-of-2, so 1KB is 1024B"): 3.22 GB decimali, quindi il margine
# reale su un volume da 5 GB decimali è ~36%, non il 40% tondo.
#
# Il margine non è prudenza generica: la compaction scrive il blocco nuovo PRIMA di
# cancellare i vecchi, e retention.size si applica DOPO, non riserva spazio. Quello
# che invece va corretto rispetto a quanto questo file diceva prima: il WAL **è**
# contato dentro retention.size — "WAL and m-mapped chunks are counted in the total
# size" — insieme agli m-mapped chunks. Erano esclusi solo dalla cancellazione, non
# dal conteggio: si eliminano soltanto i blocchi persistenti per rispettare il tetto.
#
# ATTENZIONE: i ~50 MB/giorno qui sotto sono stati misurati quando i job di scrape
# erano quattro. Dal 2026-08-20 ce n'e' uno in piu' (`grafana`), e un audit ha
# misurato ~3186 campioni per scrape da quel target contro i ~305 del Collector:
# l'ordine di grandezza dell'ingestione cambia, e con lui il rapporto fra i due
# limiti. NON riscrivo il numero qui, perche' non l'ho misurato in questa
# configurazione e un numero inventato e' peggio di uno vecchio dichiarato tale.
# Va rimisurato dopo il deploy con
#   rate(prometheus_tsdb_head_samples_appended_total[3m])
# e questi commenti vanno aggiornati nello stesso commit. Se a scattare per primo
# diventa la DIMENSIONE invece del tempo, la finestra si accorcia sotto i 30 giorni
# dichiarati e Prometheus non fallisce: cancella i blocchi vecchi e risponde
# correttamente a tutto. Il watchdog guarda `compactions_failed_total`, che in quel
# caso resta a zero — e' un guasto senza testimone, e la serie che lo direbbe
# (`prometheus_tsdb_size_retentions_total`) non la legge nessuno.
#
# Alla crescita misurata NEL 2026-08-19 (~50 MB/giorno) 30 giorni erano ~1.5 GB, e a
# scattare per primo è il TEMPO e la dimensione resta la rete di sicurezza —
# l'opposto del rapporto precedente, ed è il motivo per cui la finestra ora si può
# dichiarare in giorni senza mentire.
#
# La retention a 30 giorni è tornata sostenibile perché lo storage si paga sul
# consumato ($0.15/GB/mese): ~1.5 GB sono ~$0.23 al mese. Era una decisione di
# costo, non tecnica, e il costo è cambiato. Se il volume viene ridimensionato di
# nuovo, entrambi i numeri vanno rivisti a mano.
# --web.cors.origin: il DEFAULT e' `.*`, e non e' una svista del fornitore — e'
# pensato per chi mette Prometheus dietro un proxy che decide lui le origini.
# Qui non c'e' nessun proxy davanti: misurato il 2026-08-20, `Origin:
# https://evil.example` si riprende indietro `Access-Control-Allow-Origin:
# https://evil.example`, quindi qualunque pagina aperta nel browser di chi puo'
# raggiungere questa porta legge l'INTERO TSDB in JavaScript — trenta giorni di
# profilo di lavoro, che SECURITY.md identifica come il contenuto sensibile del
# volume. Sulla rete privata di Railway il raggio e' piccolo, e in locale il
# compose non pubblica nessuna porta sull'host (fino al 21/08/2026 questa riga
# diceva il contrario, e il compose non aveva nemmeno il flag).
# `^$` non matcha nessuna origine: le API restano interrogabili da server a
# server (Grafana e la status API non mandano `Origin`), e smettono di esserlo
# da un browser di terzi. Le altre tre superfici di scrittura erano gia' chiuse
# e sono state riverificate lo stesso giorno: admin-api, lifecycle e
# remote-write-receiver tutte `false`.
CMD [ \
  "--config.file=/etc/prometheus/prometheus.yml", \
  "--web.cors.origin=^$", \
  "--storage.tsdb.path=/prometheus", \
  "--storage.tsdb.retention.time=30d", \
  "--storage.tsdb.retention.size=3GB" \
]
