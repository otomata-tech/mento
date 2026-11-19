# mento

CLI Python (Linux, macOS, Windows) pour enregistrer des réunions et les transcrire.
S'arrête au transcript : minutes et synthèse sont faites par la session Claude qui a le
contexte. Le dépôt est aussi un plugin Claude Code (et sa propre marketplace) qui livre
la skill `skills/mento/`. Le CLI s'appelle comme le service en ligne mento.cc (stockage et
partage, serveur dans `otomata-tech/mento.cc`), où `mento push` envoie les enregistrements.
Le dépôt garde le nom `audio-recorder`. Config : `~/.config/mento/config`.

## Installation et dev

```bash
uv tool install -e .     # poste d'Alexis : la commande suit le checkout
mento install   # icône au démarrage, entrée de menu, plugin dans chaque ~/.claude*/
mento setup     # LOCAL_SPEAKER, jetons (HF, Mistral, mento), moteur…
uv run pytest -q         # via tests-poste sur le poste partagé ; la CI couvre les 3 OS
```

WhisperX est un outil à part (`uv tool install whisperx --python 3.12`), appelé en
commande : il amène PyTorch et impose sa version de Python.

## Architecture (`src/mento/`)

- `recorder.py` : processus d'enregistrement détaché (`control.spawn_recorder`). Un thread
  par périphérique (soundcard) écrit `mic.pcm` / `system.pcm` (s16le 48 kHz mono) dans le
  dossier ; pause = périphériques fermés + pistes réalignées ; stop = encodage en
  `audio.mp3` stéréo (micro à gauche, système à droite, tag `DUAL_TRACK_TAG`).
- `session.py` : `state.json` écrit par le seul recorder, avec heartbeat ; ordres par le
  fichier `control`. Heartbeat périmé = `interrupted`, et `stop` encode ce qui a été capté.
  Dossier d'état privé (`platformdirs.user_runtime_dir`).
- `devices.py` : micro par défaut + loopback du haut-parleur par défaut (moniteur
  Pulse/PipeWire, WASAPI) ; macOS : BlackHole / Background Music, sinon micro seul.
- `transcribe.py` : WhisperX (commande) ou Voxtral (API Mistral, `diarize`,
  `context_bias`) → segments → `transcript.py` fixe `LOCAL_SPEAKER` d'après la piste micro.
- `library.py` : list/show/cat/search/rename, `meta.json`. `push.py` : API mento.
- `tray.py` : Linux → `tray_appindicator.py` sous le Python système (PyGObject) ;
  Windows/macOS → pystray. Les deux passent par `tray_core.py` et le CLI.
- `install.py` : intégration bureau par OS, plugin Claude par compte (`CLAUDE_CONFIG_DIR`).

## Pièges

- soundcard perd un tiers des trames sans `blocksize` explicite au `recorder()`.
- soundcard (Pulse) nomme son client d'après `sys.argv[1]` et plante sans ; et il lui
  faut le vrai `XDG_RUNTIME_DIR` (ne pas le surcharger en test).
- Pas de ffprobe dans imageio-ffmpeg : durée et tag se lisent dans la bannière de
  `ffmpeg -i` (`media.probe`).
- Deux pistes : chaque canal est normalisé à part avant le downmix (le moniteur suit le
  volume de sortie), puis `transcript.py` attribue à `LOCAL_SPEAKER` les segments où le
  micro est actif (trames de 100 ms, au-dessus du bruit de fond et de la fuite des
  haut-parleurs). Sans audio distant (présentiel), diarization seule.
- Transcripts : `[mm:ss]`, `[h:mm:ss]` au-delà d'une heure ; les anciens laissaient les
  minutes dépasser 59, `library.LINE_RE` lit les deux.
- Plugin : Claude Code le met en cache **par version** ; toute modification de la skill
  exige d'incrémenter `version` dans `.claude-plugin/plugin.json` (et `pyproject.toml`).
  Valider avec `claude plugin validate .`.
- `push` suit le contrat de l'API mento (`/api/v1`, Bearer `MENTO_API_TOKEN`, erreurs RFC
  9457) : PUT by-ref idempotent, transcript en text/plain, audio par URL S3 signée
  (Cloudflare plafonne les corps à 100 Mo). Les synthèses passent par le MCP ou oto.
