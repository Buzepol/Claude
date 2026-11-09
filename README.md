# DupeScout

Vergelijk schijven en mappen, vind dubbele bestanden en foto's, bekijk ze naast elkaar, ruim ze veilig op
(via quarantaine) en orden bestanden in nieuwe mappen. Gemaakt voor macOS (Apple Silicon), met veel foto's op
externe hdd's van ca. 1 TB. De kern is gewoon Python en draait ook op Linux (zo is hij getest).

Het plan en de afwegingen staan in [`PLAN.md`](PLAN.md).

## Installeren

Projectmap: `~/Library/Mobile Documents/com~apple~CloudDocs/PROJECTEN/Claude/Code/filemanager`
(zie `CODEMAP.md` voor de volledige stappen, ook voor ophalen met git).

```bash
cd "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PROJECTEN/Claude/Code/filemanager"
python3 -m venv .venv.nosync && ln -s .venv.nosync .venv
source .venv/bin/activate
pip install -e ".[gui]"
```
Met RAW-foto's (rawpy): `pip install -e ".[gui,raw]"`.

Optioneel (voor video/audio): `brew install ffmpeg chromaprint` (levert `ffmpeg`, `ffprobe`, `fpcalc`).

Starten: `dupescout-gui` (venster) of `dupescout --help` (commandoregel).
Een `.app`/`.dmg` bouwen: zie `packaging/build_macos.sh`.

> **Rechten op macOS:** geef de app (of Terminal) *Volledige schijftoegang* en toegang tot externe volumes
> (Systeeminstellingen → Privacy en beveiliging), anders worden mappen overgeslagen.

## De app

| Tab | Wat |
|---|---|
| 1 · Bronnen & scan | Mappen of hele schijven kiezen, detectiemethoden, foto-gelijkenis (schuif), uitsluitingen, starten/pauzeren/stoppen met voortgang. |
| 2 · Resultaten | Groepen bekijken, foto's naast elkaar (gelijke zoom, EXIF, verschil-overlay), duplicaten automatisch selecteren, naar quarantaine. |
| 3 · Mappen vergelijken | Map A (referentie) ↔ map B: *in beide*, *alleen in A*, *alleen in B*; duplicaten in B naar quarantaine; CSV-export. |
| 4 · Ordenen | Regels (YAML) → **voorbeeld** (voor → na) → uitvoeren → ongedaan maken. |
| 5 · Quarantaine & logboek | Herstellen of **handmatig** definitief wissen; logboek van alle acties. |

Sneltoetsen in *Resultaten*: `←`/`→` vorige/volgende foto in de groep · `spatie` markeren · `⌫` markeren en door naar de volgende.

### Detectie
* **Exact identiek** – grootte → eerste/laatste 64 KB → volledige BLAKE2b-hash. 100% betrouwbaar; vóór verplaatsen volgt een byte-voor-byte controle.
* **Gelijkende foto's** – pHash (JPEG, PNG, HEIC, TIFF, RAW via ingebedde preview); snelle clustering op Hamming-afstand. Standaard 5/64 bits; hoger = ruimer (meer foutieve treffers).
* **Naam + grootte** – alleen "mogelijk"; wordt nooit automatisch geselecteerd.
* **Video/audio** – frame-hashes (ffmpeg) en Chromaprint (fpcalc). *Experimenteel; vooral video is getest.*
* Hardlinks tellen niet als duplicaat. Symlinks worden niet gevolgd. Andere mounts binnen een map worden niet ingestapt.
* **`*.photoslibrary` (Apple Foto's) wordt altijd overgeslagen** – hiervoor is geen instelling.
* Hashes worden gecachet (SQLite, per volume-UUID + pad + grootte + datum): een tweede scan van 1 TB is veel sneller en een afgebroken scan hervat.

### Veilig verwijderen
* Bestanden gaan naar `.dupescout-quarantine/<batch>/` **op dezelfde schijf** (direct, omkeerbaar) met een `manifest.json`.
  Op macOS wordt voor de (alleen-lezen) systeemschijf de thuismap gebruikt.
* Er blijft altijd minstens één kopie staan; dit wordt in de kern afgedwongen, niet alleen in de UI.
* **Wissen gebeurt alleen handmatig** in tab 5 (of `dupescout quarantine purge`), met bevestiging van aantal en grootte. Er is geen timer.
* `.xmp`/`.aae`-sidecars gaan mee; een Live Photo-video (`.mov`) alleen als de behouden kopie er ook een heeft.
* Alleen-lezen volumes (bijv. NTFS op macOS): scannen en vergelijken werken, quarantaine en ordenen niet.

### Ordenen
Voorbeeldregels (`dupescout organize … --rules regels.yaml`):

```yaml
- naam: Foto's op datum
  als: { extensie: [jpg, jpeg, heic, png, cr3, dng] }
  doel: "Foto's/{jaar}/{maand:02d}-{maandnaam}"
- naam: Screenshots
  als: { naam_regex: "^Screenshot" }
  doel: "Screenshots/{jaar}"
- naam: Overig
  doel: "Overig/{extensie}"
```

Datum uit EXIF → video-metadata → bestandsnaam (`IMG_20230415…`) → wijzigingsdatum (de bron staat in het voorbeeld).
Tekens die op exFAT/FAT/NTFS niet mogen worden vervangen en gemeld. Elke actie komt in een journal;
over schijven heen wordt eerst gekopieerd en geverifieerd, en de bron gaat daarna naar quarantaine.

## Commandoregel

```bash
dupescout scan  /Volumes/HDD1 --mode exact foto        # zoeken (read-only)
dupescout compare /Volumes/HDD1/Foto /Volumes/HDD2/Foto --csv rapport.csv        # dry-run
dupescout compare … --apply                            # duplicaten in B naar quarantaine
dupescout clean /Volumes/HDD1 --rule oldest --prefer /Volumes/HDD1/Origineel     # dry-run
dupescout clean … --apply
dupescout quarantine list | restore BATCH | purge BATCH
dupescout organize /Volumes/HDD1/Chaos --dest /Volumes/HDD2/Sorted --preset fotos     # dry-run
dupescout organize … --apply        ·        dupescout organize --undo BATCH
```

Alle opruim- en ordenacties zijn standaard een dry-run; `--apply` voert ze uit.

## Ontwikkelen

```bash
pip install -e ".[dev]"
pytest                      # 46 tests (kern, CLI, UI headless)
```

Structuur: `dupescout/core` (geen UI-afhankelijkheden), `dupescout/ui` (PySide6), `dupescout/cli.py`.

## Bekende beperkingen
* Ontwikkeld en getest op Linux; de macOS-specifieke stukken (`diskutil`-herkenning, `.app`-build, Full Disk Access) zijn nog niet op een echte Mac met hdd's geprobeerd.
* Audio-vergelijking (Chromaprint) is niet getest zonder `fpcalc`; drempels kunnen bijstelling nodig hebben.
* Foto-gelijkenis is een hulpmiddel: bekijk altijd het voorbeeld, vooral bij serie-opnames.
* RAW-voorbeelden vereisen `rawpy`; zonder valt de app terug op Pillow (werkt alleen voor o.a. DNG).
