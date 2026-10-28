# Meteo Montagne & Paesi (WordPress)

Servizio Docker con pannello FastAPI sulla porta **8124**. Legge le previsioni
Open-Meteo per Milano, Bergamo, Brescia, Mantova e Sondrio e pubblica ogni mattina
un articolo su WordPress nella categoria Meteo (2496).

## Immagine e scritte dal pannello

La sezione **Immagine in evidenza e scritte** permette di:

- scegliere un URL oppure caricare una foto JPG, PNG o WebP (massimo 15 MB e 40 megapixel);
- mantenere l'immagine intera con margini oppure riempire il formato ritagliandola;
- personalizzare titolo e seconda scritta, anche su più righe, o nascondere tutti i testi;
- regolare posizione X/Y in percentuale, dimensioni, colore, contorno e sfondo delle scritte;
- scegliere la scritta e cliccare sull'anteprima per impostarne il centro;
- vedere le modifiche con **Aggiorna anteprima**, senza salvare o pubblicare;
- applicarle ai prossimi invii con **Salva grafica**.

I testi possono contenere questi segnaposto, aggiornati alla data di ogni invio:

| Segnaposto | Esempio |
| --- | --- |
| `{data}` | Mercoledì 28 ottobre 2026 |
| `{giorno}` | 28/10/2026 |
| `{regione}` | Lombardia |

Esempio titolo: `Meteo Lombardia`. Seconda scritta: `{data}`.
Lasciare vuota una scritta la nasconde. Il titolo dell'articolo WordPress continua
ad essere generato automaticamente, indipendentemente dai testi sulla copertina.
I testi lunghi vanno a capo e, se necessario, si riducono; ai bordi vengono mantenuti
all'interno dell'immagine. Le due scritte possono sovrapporsi se posizionate insieme.

L'anteprima e l'invio usano lo stesso rendering JPEG a **1600 × 900 pixel**.
L'ingrandimento di un originale piccolo non aggiunge dettaglio fotografico.
Il file temporaneo inviato a WordPress viene cancellato dopo la pubblicazione.
Le anteprime non contattano WordPress e non richiedono una chiamata a Open-Meteo.

## Avvio o aggiornamento in Portainer

1. Aggiornare i file dallo stesso repository e ricostruire l'immagine Docker
   (una semplice riaccensione del vecchio container non aggiorna il codice).
2. Usare lo stack `docker-compose.yml` e impostare `WP_APP_PASSWORD` tra le variabili
   di ambiente dello stack, conservando le proprie impostazioni WordPress e l'orario.
3. Verificare che il servizio abbia il volume **`meteo-data:/data`** indicato nel compose.
   Se lo stack esistente è stato incollato manualmente, aggiungere anche la dichiarazione
   `volumes: meteo-data:` a livello principale, come nel file di esempio.
4. Aprire `http://IP_RASPBERRY:8124/`, caricare la nuova immagine e salvare la grafica.

Il volume contiene `cover.json` e, se caricata, `background.png`. Non eliminare il volume
quando si ricrea lo stack; effettuare il backup di `/data` per conservare la grafica.
L'immagine caricata viene normalizzata e ridimensionata entro 3200 × 3200 pixel.
`DATA_DIR` permette di scegliere una directory diversa (default `/data`).
Il pannello è pensato per l'accesso dalla rete privata, come la versione precedente.

Senza impostazioni salvate viene ancora utilizzato `IMG_DEFAULT_URL`, con il valore
predefinito `https://www.montagneepaesi.com/wp-content/uploads/2025/09/Lombardia.jpg`.
L'orario predefinito resta **07:00 Europe/Rome** (`RUN_HOUR` / `RUN_MINUTE`).
Le credenziali WordPress restano nelle variabili d'ambiente e non sono salvate nella grafica.

Health check: `http://IP_RASPBERRY:8124/health`.

## Verifica locale

```sh
python -m pip install -r requirements.txt pytest httpx
python -m pytest -q
```

I test verificano rendering, salvataggio/ricaricamento, convalida dei file e delle
impostazioni e invio WordPress simulato; non pubblicano articoli reali.
