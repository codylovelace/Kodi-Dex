# Dex Hub seek preview container

Shows a picture of the moment you are seeking to when the stream comes from
Stremio or a debrid service. Plex, Emby and Jellyfin make these pictures
themselves and Dex Hub uses theirs; this container is only for streams no
server makes previews for. It runs on your Docker host, so the Kodi box
does none of the work.

How it works: Dex Hub sends the container the URL of the stream you are
playing. The container grabs one frame per 10 seconds of the film with
ffmpeg (only the nearest key frame, a few MB of the stream each time) and
answers Kodi's image requests from its cache.

## Cost

* **Pre-scan** (Dex Hub setting, default every 30 s): starts 30 s after
  playback starts. A 2-hour film is about 240 grabs, roughly 0.5-1 GB read
  from the debrid service, spread over a few minutes. At 10 s it is about
  720 grabs and 1-3 GB.
* **On demand only** (pre-scan 0): nothing is read until you seek; each new
  10-second step then takes 0.5-3 s over debrid, so the picture often
  arrives after the seek has already happened.
* Disk: about 6-15 KB per frame. The cache keeps 48 hours and at most
  2 GB by default.

## Set up (Portainer)

1. **Stacks → Add stack → Repository**: this repository, compose path
   `docker/seekthumbs/compose.yaml`.
2. Under **Environment variables** add `DEXHUB_THUMBS_KEY` with a long
   random value (for example the output of `openssl rand -hex 24`).
3. Deploy. Check `http://<docker-host>:8765/health` answers `ok`.
4. In Kodi: **Dex Hub → Settings → Playback → Seek previews**, set
   *Preview container* to `http://<docker-host>:8765` and *Preview container
   key* to the same key.

Previews are drawn by the Dex Hub skin (`skin.dexhub`) only.

## Settings (environment)

| Variable | Default | What it does |
| --- | --- | --- |
| `DEXHUB_THUMBS_KEY` | (required) | Shared key; requests without it are refused |
| `DEXHUB_THUMBS_JOBS` | 2 | ffmpeg processes at once |
| `DEXHUB_THUMBS_WIDTH` | 384 | Frame width in pixels |
| `DEXHUB_THUMBS_TIMEOUT` | 20 | Seconds before a grab is given up |
| `DEXHUB_THUMBS_CACHE_MB` | 2048 | Cache size limit |
| `DEXHUB_THUMBS_CACHE_HOURS` | 48 | Frames unused this long are deleted |
| `LOG_LEVEL` | INFO | `DEBUG` also logs each request |

## Safety

The container fetches whatever URL it is given, so it refuses to start
without a key, and ffmpeg may only open `http` and `https` (no local files).
Keep port 8765 on your LAN; do not forward it from the internet. Debrid
links can be tied to your public IP address; the Docker host normally
shares it with the Kodi box, so this works on the same network.

## Endpoints

* `GET /thumb?k=KEY&u=URL&t=SECONDS[&s=STEP][&n=NEAR][&h=HEADERS]`: JPEG
* `GET /scan?k=KEY&u=URL&every=SECONDS[&s=STEP][&h=HEADERS]`: starts a pre-scan
* `GET /health`: `ok`

Tests: `python3 -m unittest discover -s tests` from the repository root.
