---
name: audio-recorder
description: Enregistrements de réunions d'Alexis (audio-recorder) — retrouver, lire, chercher dans les transcripts, lancer une transcription avec le contexte de la mission (vocabulaire, nombre de participants), renommer. À charger dès qu'il est question d'une réunion enregistrée, d'un transcript ou d'un compte rendu de réunion.
---

# audio-recorder

CLI (Linux, macOS, Windows) qui enregistre les réunions (micro à gauche, audio
système à droite) et les transcrit : WhisperX en local, ou Voxtral (API Mistral)
sur une machine sans carte graphique. Il s'arrête au transcript : les minutes, la
synthèse et le classement sont faits par la session Claude qui a le contexte de la
mission. Le stockage et le partage passent, en option, par mento (mento.cc).

Un enregistrement se désigne par son id (nom du dossier), un préfixe ou un
morceau unique de ce nom, ou `last`. Toutes les commandes de lecture ont `--json` ;
`audio-recorder <commande> --help` donne l'usage détaillé et des exemples.

Si `audio-recorder` est introuvable, le CLI n'est pas installé : le dire, avec
`uv tool install git+https://github.com/AlexisLaporte/audio-recorder` puis
`audio-recorder install` (icône, plugin) et `audio-recorder setup` (jetons).

## Retrouver et lire

```bash
audio-recorder list [--since 7d|2026-11-01] [--limit N] [--json]
audio-recorder show <rec> [--json]       # durée, réglages de transcription, intervenants
audio-recorder cat <rec> [--speaker S] [--from 12:00] [--to 30:00]
audio-recorder search "texte" [--since 30d] [--speaker S] [-C 2]
audio-recorder path <rec>                # dossier : audio.mp3, transcript.txt, meta.json
```

`search` ignore la casse et les accents. Format d'une ligne de transcript :
`[mm:ss] INTERVENANT: texte` (`[h:mm:ss]` au-delà d'une heure). L'intervenant local
(Alexis) est fixé par la piste micro ; les autres sont `SPEAKER_00`, `SPEAKER_01`…

## Transcrire

`stop` ne transcrit pas : c'est à toi de lancer la transcription, avec ce que
tu sais de la réunion (agenda, mission, invitation).

```bash
# vocabulaire de la mission ; participants : N, ou 2-4 si incertain
audio-recorder transcribe <rec> --prompt "Olneia, URSSAF, DSN, Marie Dupont" --speakers 3 --language fr
```

- `--prompt` s'ajoute au vocabulaire global de la config ; 30 à 50 termes max
  (Whisper ne garde que ~220 tokens).
- `--speakers` est le réglage qui améliore le plus la diarization : compte tous
  les participants, Alexis compris. Voxtral ne le prend pas.
- `--engine voxtral` envoie l'audio à Mistral (`MISTRAL_API_KEY`) : à éviter pour
  une réunion confidentielle sans l'accord d'Alexis. Le moteur par défaut est
  `TRANSCRIBE_ENGINE` (whisperx).
- Plusieurs minutes de GPU : lance-la en arrière-plan. Les réglages sont gardés
  dans `meta.json` (`show` les affiche) ; relancer écrase le transcript.

## Après la transcription

1. Lire le transcript (`cat`), attribuer les `SPEAKER_0x` à des personnes
   d'après le contenu et le contexte, corriger les mots mal entendus.
2. Rédiger minutes ou synthèse là où vit le projet (oto, Drive) — jamais dans
   le dossier de l'enregistrement, dont le transcript reste la source brute.
3. Donner un nom parlant : `audio-recorder rename <rec> point_client_onboarding`
   (`[a-z0-9_]`, 50 caractères, le préfixe de date est conservé).
4. Si Alexis veut partager ou garder la réunion en ligne : `audio-recorder push <rec>`
   (`--with-audio` pour le mp3) l'envoie sur mento, puis la synthèse et le lien de
   partage passent par le MCP ou le connecteur oto de mento. Le push est idempotent :
   le relancer met à jour.

## Enregistrer

```bash
audio-recorder start | pause | resume | stop [--trim] | status [--json]
```

Alexis le pilote d'ordinaire par l'icône du panneau (`audio-recorder tray`).
N'arrête jamais un enregistrement en cours sans qu'il le demande.
