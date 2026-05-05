# Reddit Story Scraper · TikTok Video Generator

Sammelt automatisch die besten Reddit-Stories, bereitet sie mit einem lokalen KI-Modell (Ollama) für TikTok auf und generiert daraus fertige 9:16-Videos mit Text-to-Speech, Wort-Untertiteln und Hintergrundclips.

---

## Inhaltsverzeichnis

1. [Was es macht](#was-es-macht)
2. [Voraussetzungen](#voraussetzungen)
3. [Schnellstart — Lokal](#schnellstart--lokal)
4. [Schnellstart — Docker](#schnellstart--docker)
5. [NVIDIA GPU aktivieren](#nvidia-gpu-aktivieren)
6. [Konfiguration](#konfiguration)
7. [Alle Befehle](#alle-befehle)
8. [Video-Generierung](#video-generierung)
9. [Projektstruktur](#projektstruktur)
10. [URL-Struktur](#url-struktur)

---

## Was es macht

```
Reddit API  →  Scraper  →  SQLite DB  →  Webserver
                                ↓
                           Ollama LLM
                                ↓
                    TikTok-JSON (Titel, Beschreibung,
                     Hashtags, bereinigte Story)
                                ↓
                         Video Engine (SARA)
                          (TTS + Whisper +
                         ASS-Untertitel +
                          FFmpeg-Render)
                                ↓
                       part_1.mp4, part_2.mp4 …
```

**Features im Überblick:**

- Scraped Stories von 8 Subreddits (konfigurierbar) per Reddit-API (PRAW)
- Filtert Duplikate und Stories unter 200 Wörtern automatisch
- Jede Story bekommt einen eindeutigen **6-stelligen Code** (z. B. `AB3X7K`)
- Material-Design-3-Webinterface — filtert nach Subreddit, sortiert nach Score / Datum / Wörtern
- **Ollama-Integration**: wandelt Story zu TikTok-fertigem JSON um (Titel, Beschreibung, Hashtags, bereinigter Text)
- **Ollama wird beim ersten Start automatisch installiert** wenn es fehlt
- **Video Engine (SARA)**: TTS via Kokoro-82M → Wort-Timing via Whisper → ASS-Untertitel → FFmpeg-Render
- Vollständige Docker-Compose-Unterstützung inkl. optionaler GPU-Beschleunigung

---

## Voraussetzungen

### Lokal

| Komponente | Mindestversion | Installieren |
|---|---|---|
| Python | 3.11 | [python.org](https://www.python.org/downloads/) |
| FFmpeg | beliebig | `winget install Gyan.FFmpeg` (Windows) · `apt install ffmpeg` (Linux) |
| Ollama | beliebig | wird **automatisch installiert** wenn nicht vorhanden |
| Reddit-Account | — | für API-Credentials (kostenlos, kein Abo nötig) |

### Docker

| Komponente | Hinweis |
|---|---|
| Docker Desktop | [docker.com/get-started](https://www.docker.com/get-started/) |
| Docker Compose | in Docker Desktop bereits enthalten |
| NVIDIA Container Toolkit | **nur** für GPU-Unterstützung — Anleitung weiter unten |

---

## 🖥️ Web-Dashboard & Automatisierung

Das Projekt verfügt über ein modernes **Material Design 3 Dashboard**, über das der gesamte Workflow gesteuert werden kann:

- **Scrape**: Startet den Reddit-Scraper im Hintergrund.
- **Process**: Verarbeitet neue Stories mit Ollama (KI-Umschreibung). Inklusive **Fortschrittsbalken** und **Pause/Fortsetzen**-Funktion.
- **Video Generieren**: Erstellt die finalen TikTok-Videos direkt aus der Story-Ansicht.

---

## 🚀 Schnellstart — Docker (Empfohlen)

```bash
# 1. Alles starten (baut Image, startet Ollama + App)
docker compose up -d
```

**Was passiert automatisch?**
1. Der **Ollama-Service** wird initialisiert.
2. Die App wartet, bis Ollama bereit ist.
3. Das Modell `llama3.2` wird im Hintergrund geladen.
4. Öffne **http://localhost:5000** im Browser.

**Credentials:**
Stelle sicher, dass deine Reddit-API-Daten in der `docker-compose.yml` unter `environment` eingetragen sind.

---
Daten bleiben dauerhaft erhalten:

| Host-Pfad | Container-Pfad | Inhalt |
|---|---|---|
| `./storage/` | `/app/storage/` | SQLite-Datenbank + Story-Rohtexte |
| `./data/` | `/app/data/` | Videos, TTS-Dateien, Kokoro-Modell |
| Docker Volume `ollama_data` | `/root/.ollama` | Ollama-Modelle |

---

## NVIDIA GPU aktivieren

GPU-Beschleunigung macht Ollama (LLM-Inferenz) und Whisper (Wort-Timing) **deutlich schneller** — besonders bei längeren Stories.

### Schritt 1 — NVIDIA Container Toolkit installieren (einmalig, nur Linux)

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**Windows (WSL2):** Docker Desktop → Settings → Resources → GPU Support aktivieren — kein Toolkit nötig.

### Schritt 2 — `docker-compose.yml` anpassen

Den auskommentierten `deploy`-Block im `ollama`-Service aktivieren:

```yaml
  ollama:
    image: ollama/ollama
    # ... (andere Felder bleiben gleich)
    deploy:                      # ← Kommentarzeichen (#) entfernen
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

### Schritt 3 — Whisper in der Video Engine auf GPU umstellen

In `video_engine.py` die Zeile mit `WhisperModel` ändern:

```python
# CPU (Standard):
_WHISPER_MODEL = WhisperModel("tiny", device="cpu", compute_type="int8")

# NVIDIA GPU (schneller):
_WHISPER_MODEL = WhisperModel("tiny", device="cuda", compute_type="float16")
```

Danach Image neu bauen:

```bash
docker compose up --build
```

### GPU-Erkennung prüfen

```bash
docker exec -it $(docker compose ps -q ollama) nvidia-smi
```

---

## Konfiguration

### Reddit API Credentials einrichten

1. Auf [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) einloggen
2. **„Create another app"** klicken → Typ: **script** wählen
3. `client_id` (unter dem App-Namen) und `client_secret` kopieren
4. In `config.json` eintragen:

```json
{
  "settings": {
    "reddit_api": {
      "client_id": "DEINE_CLIENT_ID",
      "client_secret": "DEIN_CLIENT_SECRET"
    }
  }
}
```

### Subreddits anpassen

```json
{
  "subreddits": [
    "AmItheAsshole",
    "tifu",
    "relationship_advice",
    "MaliciousCompliance",
    "ProRevenge",
    "entitledparents",
    "confession",
    "TrueOffMyChest"
  ]
}
```

### Weitere Einstellungen

| Key | Standard | Bedeutung |
|---|---|---|
| `min_word_count` | `200` | Mindestlänge einer Story in Wörtern |
| `stories_per_fetch` | `50` | Stories pro Subreddit pro Scraping-Lauf |
| `request_delay_seconds` | `1.5` | Pause zwischen Reddit-API-Anfragen |
| `db_path` | `stories.db` | SQLite-Datenbankpfad (lokal) |
| `stories_dir` | `stories` | Verzeichnis für Rohtext-Dateien (lokal) |

In Docker werden `db_path` und `stories_dir` automatisch per Umgebungsvariable auf `storage/` umgeleitet — `config.json` muss nicht geändert werden.

---

## Alle Befehle

### Stories scrapen

```bash
python main.py scrape                        # alle Subreddits
python main.py scrape --subreddit tifu       # nur ein Subreddit
```

### Mit Ollama verarbeiten (→ TikTok-JSON)

```bash
python main.py process                       # alle unverarbeiteten Stories
python main.py process --subreddit tifu      # nur ein Subreddit
python main.py process --code AB3X7K         # eine einzelne Story
python main.py process --model gemma3        # anderes Ollama-Modell verwenden
```

Beim allerersten Aufruf:
- Ollama wird automatisch installiert (via `winget` auf Windows, Install-Script auf Linux/Mac)
- Das Modell `llama3.2` (~2 GB) wird automatisch heruntergeladen

### Video generieren

```bash
python main.py generate --code AB3X7K           # Standard: ~250 Wörter/Part
python main.py generate --code AB3X7K --words 180  # kürzere Parts (= mehr Videos)
python main.py generate --code AB3X7K --words 400  # längere Parts (= weniger Videos)
```

**Voraussetzung:** Story muss zuerst mit `process` verarbeitet worden sein.  
**Output:** `data/outputs/AB3X7K/part_1.mp4`, `part_2.mp4`, …

### Webserver

```bash
python main.py serve                         # http://127.0.0.1:5000
python main.py serve --host 0.0.0.0          # von außen erreichbar (LAN/Server)
python main.py serve --port 8080             # anderen Port nutzen
python main.py serve --debug                 # Debug-Modus mit Auto-Reload
```

---

## Video-Generierung

### Pipeline im Detail

```
Story-Text (aus tiktok.json)
    ↓  (in ~250-Wort-Parts aufteilen)
Kokoro-82M TTS  →  audio.wav
    ↓
Faster-Whisper  →  Wort-Zeitstempel
    ↓
ASS-Untertitel  →  subtitle.ass  (ein Wort pro Frame, zentriert, gelb)
    ↓
FFmpeg:
  Hintergrundvideo (Loop, zugeschnitten auf 1080×1920)
  + audio.wav
  + subtitle.ass (Untertitel eingebettet)
    ↓
part_N.mp4  (1080×1920, H.264, AAC, TikTok-Format)
```

### Hintergrundvideos einrichten

Eigene Clips in `data/backgrounds/` ablegen:

```
data/
└── backgrounds/
    ├── minecraft_parkour.mp4
    ├── subway_surfers.mp4
    └── satisfying_slime.mp4
```

- Format: **9:16 (1080×1920)** empfohlen — andere Formate werden automatisch zugeschnitten
- Mindestens **1 Clip** erforderlich, sonst schlägt `generate` mit Fehlermeldung fehl
- Mehrere Clips werden zufällig gemischt und geloopt

### Kokoro TTS-Modell

Wird beim ersten `generate`-Aufruf **automatisch heruntergeladen** (~80 MB):

```
data/kokoro_models/
├── kokoro-v1.0.int8.onnx   ← TTS-Modell
└── voices-v1.0.bin          ← Stimmen
```

Keine manuelle Installation nötig.

### FFmpeg installieren

```bash
# Windows:
winget install Gyan.FFmpeg

# Ubuntu / Debian:
sudo apt install ffmpeg

# macOS:
brew install ffmpeg
```

> **Docker-Hinweis:** Wenn `generate` innerhalb des Containers laufen soll, muss FFmpeg ins Image. Zeile in `Dockerfile` ergänzen:
> ```dockerfile
> RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
> ```

---

## Projektstruktur

```
REDDIT/
├── main.py              # CLI-Einstiegspunkt (scrape / process / generate / serve)
├── scraper.py           # Reddit-API (PRAW) + Textdatei-Speicherung
├── processor.py         # Ollama-Integration + Auto-Install/Auto-Start
├── video_engine.py      # SARA Video Engine (TTS · Whisper · ASS · FFmpeg)
├── server.py            # Flask-Webserver + REST-API
├── database.py          # SQLite-Datenbankschicht
├── config.json          # Subreddits + Einstellungen + Reddit-Credentials
├── requirements.txt     # Python-Pakete
│
├── Dockerfile           # Docker-Image der App
├── docker-compose.yml   # Orchestrierung: App + Ollama
├── docker-entrypoint.sh # Warte auf Ollama → Modell laden → App starten
├── .dockerignore        # Ausschlüsse für Docker-Build-Kontext
│
├── stories/             # Rohtext-Dateien (lokal, in Docker: storage/stories/)
│   └── tifu/
│       └── AB3X7K/
│           ├── original.txt   ← Originaltext vom Reddit-Post
│           └── tiktok.json    ← verarbeiteter TikTok-Content
│
├── data/                # Video-Engine-Daten (persistent gemountet)
│   ├── backgrounds/     # ← HIER eigene Hintergrundclips ablegen
│   ├── outputs/
│   │   └── AB3X7K/
│   │       ├── part_1.mp4
│   │       └── part_2.mp4
│   ├── covers/          # Cover-Bilder (automatisch generiert)
│   ├── tts/             # Temporäre Audio-/Untertitel-Dateien
│   └── kokoro_models/   # TTS-Modell (automatisch heruntergeladen)
│
├── static/css/app.css   # Material Design 3 CSS (Reddit Orange Seed)
└── templates/
    ├── base.html         # Layout mit Nav Rail
    ├── index.html        # Story-Grid (Filter-Chips, Sort, Pagination)
    ├── story.html        # Story-Detailseite (3 Tabs: Original / TikTok / Video)
    └── 404.html
```

---

## URL-Struktur

| URL | Beschreibung |
|---|---|
| `/` | Alle Stories, sortiert nach Score |
| `/?subreddit=tifu` | Gefiltert nach Subreddit |
| `/?sort=date` | Sortiert nach Scrapdatum |
| `/?sort=words` | Sortiert nach Wortanzahl |
| `/?page=2` | Pagination (24 Stories/Seite) |
| `/story/AB3X7K` | Story-Detailseite mit 3 Tabs |
| `/video/AB3X7K/1.mp4` | Generiertes Video Part 1 (direkt streambar) |
| `/api/stories` | JSON-API (Parameter: subreddit, sort, limit, offset) |
| `/api/subreddits` | Subreddit-Statistiken als JSON |
| `/api/ollama/status` | Ollama-Status + verfügbare Modelle |

---

## Nützliche Docker-Befehle

```bash
# Alles starten
docker compose up --build

# Im Hintergrund starten
docker compose up --build -d

# Logs live verfolgen
docker compose logs -f app
docker compose logs -f ollama

# Nur App-Container neu starten (ohne Rebuild)
docker compose restart app

# Ollama-Modell manuell herunterladen / wechseln
docker exec -it $(docker compose ps -q ollama) ollama pull mistral

# Alles stoppen und aufräumen
docker compose down

# Auch Volumes löschen (alle Daten weg!)
docker compose down -v
```
