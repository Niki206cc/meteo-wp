# Meteo Montagne & Paesi (WordPress)

Servizio containerizzato (FastAPI) con mini-GUI web su porta 8124 per:
- leggere meteo Open-Meteo su alcune città lombarde
- generare immagine di copertina
- pubblicare post su WordPress con immagine in evidenza
- cancellare sempre l'immagine generata (pulizia spazio su Raspberry)

## Avvio (Portainer)
Usa lo stack `docker-compose.yml` e imposta `WP_APP_PASSWORD` nelle variabili d'ambiente.

GUI:
http://IP_RASPBERRY:8124/
Health:
http://IP_RASPBERRY:8124/health
