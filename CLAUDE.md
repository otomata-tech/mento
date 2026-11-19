# audio-recorder

CLI bash pour enregistrer des réunions et les transcrire (WhisperX). S'arrête au transcript :
minutes et synthèse sont faites par la session Claude qui a le contexte. Le dépôt est aussi
un plugin Claude Code (et sa propre marketplace) qui livre la skill `skills/audio-recorder/`.

## Installation

```bash
./install.sh           # ~/.local/bin, plugin activé dans chaque ~/.claude*/, icône au démarrage
audio-recorder setup   # configure HF_TOKEN, RECORDINGS_DIR, WHISPER_MODEL, LOCAL_SPEAKER,
                       # WHISPER_LANGUAGE, WHISPER_INITIAL_PROMPT, TULS_API_TOKEN
```

WhisperX utilise pyannote pour la diarization : token HuggingFace (Read) requis, et
il faut accepter les conditions de `pyannote/speaker-diarization-community-1` (modèle
par défaut de whisperx ≥ 3.8) sur huggingface.co avant le premier `setup`.
macOS : `brew install --cask background-music` pour capter l'audio système.

## Utilisation

```bash
audio-recorder start | pause | resume | stop [--trim] | status [--json]
audio-recorder tray                    # icône du panneau : démarrer, pause, stop
audio-recorder transcribe [rec] --prompt "vocabulaire" --speakers 3|2-4 --language fr
audio-recorder list / show / cat / search / path / rename / open   # --json partout
audio-recorder trim [rec]              # découpe auto de la fin (alias: cut)
audio-recorder push [rec]              # pousser vers tuls.me (--with-audio pour le mp3)
```

`rec` = id (nom du dossier), préfixe ou morceau unique, ou `last`. `<commande> --help` :
usage détaillé et exemples (`command_help` dans `lib/utils.sh`).

## Pièges

- Un enregistrement est fait de segments : `pause` arrête ffmpeg, `resume` en relance
  un (`audio.partNN.mp3`), `stop` les recolle en `audio.mp3` sans réencoder. L'état de
  la session vit dans `$XDG_RUNTIME_DIR/audio-recorder/` ; `stop` récupère aussi une
  session dont ffmpeg est mort (état `interrupted`).
- `meta.json` par enregistrement : début, durée, réglages de la dernière transcription.
  La lecture (`list`, `search`…) est dans `lib/recordings.py` ; l'icône (`lib/tray.py`,
  AppIndicator) ne fait qu'appeler le CLI et lire `status --json` chaque seconde.
- `stop` ne transcrit plus : la session Claude lance `transcribe` avec le vocabulaire
  de la mission (ajouté à `WHISPER_INITIAL_PROMPT`) et le nombre de participants.
- Trim : détecte les silences >3s en un seul pass ffmpeg, cherche le premier silence
  ≥10s après 5 min (fallback : rupture de densité) ; l'original est toujours gardé en
  `audio_full.mp3`.
- Deux pistes : le micro est enregistré à gauche, l'audio système à droite (tag mp3
  `comment=audio-recorder:L=mic,R=system`). À la transcription, chaque canal est
  normalisé à part avant le downmix (le monitor suit le volume de sortie), puis
  `lib/transcript.py` attribue à `LOCAL_SPEAKER` les segments où le micro est actif
  (par trames de 100 ms, au-dessus de son bruit de fond et de la fuite des
  haut-parleurs mesurée sur l'enregistrement) — pyannote ne départage plus que les
  distants. Sans audio distant (réunion en présentiel), diarization seule ; en
  hybride, toute la salle est attribuée à `LOCAL_SPEAKER`. Les anciens
  enregistrements (mixés) gardent la diarization seule.
- Détection audio : Linux suit le sink/source par défaut (`pactl`). Bluetooth sans cas
  particulier : le monitor du sink bluez capte en A2DP comme en HFP, et WirePlumber
  bascule seul en HFP quand le micro du casque s'ouvre puis revient en A2DP. macOS passe
  par AVFoundation + Background Music. Auto-unmute du micro s'il est muté.
- `WHISPER_INITIAL_PROMPT` : vocabulaire global (noms, produits, sigles) passé en amorce
  à Whisper — corrige les mots mal entendus (« Cloud » pour « Claude ») ; Whisper n'en
  garde que ~220 tokens, la fin. Watchdog toutes les 2 min : notification desktop si le niveau capté est < -60 dB.
- Plugin : `install.sh` déclare l'install (`~/.local/share/audio-recorder`, ou le checkout
  en `--link`) comme marketplace « directory » et active le plugin compte par compte
  (`CLAUDE_CONFIG_DIR`). Claude Code met le plugin en cache **par version** : toute
  modification de la skill exige d'incrémenter `version` dans `.claude-plugin/plugin.json`,
  sinon les sessions gardent l'ancienne. Valider avec `claude plugin validate .`.
- `rename` garde le préfixe de date et n'accepte que `[a-z0-9_]`, 50 caractères max.
- `push` vers `audio-recorder-transcript.tuls.me` (doc : https://tuls.me/api/docs/) ou
  `oto audio push/list/get/delete/summarize` — `TULS_API_TOKEN` vit dans
  `~/.config/audio-recorder/config` (posé par `audio-recorder setup`).

## Dev

```bash
make lint   # shellcheck
make test   # bats (via tests-poste sur le poste partagé)
```
</content>
