# Codemap – DupeScout

Overzicht van waar alles staat en hoe het samenhangt (±5.500 regels, Python 3.11+).

## Lokaal op je Mac

De projectmap (de "codemap-map" met `dupescout/`, `tests/` en dit bestand) staat in iCloud Drive:

`~/Library/Mobile Documents/com~apple~CloudDocs/PROJECTEN/Claude/Code/filemanager`

Kopieer onderstaande commando's zonder `#`-commentaar (zsh in Terminal kan daar niet tegen).

Eerste keer ophalen (alleen als de map nog niet bestaat):
```bash
mkdir -p "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PROJECTEN/Claude/Code"
git clone https://github.com/Buzepol/Claude.git "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PROJECTEN/Claude/Code/filemanager"
```

Installeren, testen en starten:
```bash
cd "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PROJECTEN/Claude/Code/filemanager"
python3 -m venv .venv.nosync && ln -s .venv.nosync .venv
source .venv/bin/activate
pip install -e ".[dev,raw]"
pytest -q
dupescout-gui
```

* `.venv.nosync`: iCloud slaat mappen met de naam `.nosync` over, zodat de virtuele omgeving niet wordt gesynchroniseerd (traag en breekbaar). De link `.venv` zorgt dat alle commando's gewoon werken.
* Zet in Finder (rechtermuisknop op de map) "Nu downloaden" aan, zodat iCloud "Mac-opslag optimaliseren" geen projectbestanden uit je Mac haalt. Voor een git-project is een map buiten iCloud (bv. `~/Developer/filemanager`) robuuster; GitHub is dan je back-up.
* Opnieuw bouwen na verplaatsen van de map: `.venv` verwijderen en de installatie hierboven herhalen.
* `.app`/`.dmg` bouwen: `./packaging/build_macos.sh` (in de projectmap).
* Claude Code lokaal: `cd "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PROJECTEN/Claude/Code/filemanager"` en dan `claude` (leest `CLAUDE.md`). Daar kan het wél tegen je echte hdd's testen en macOS-specifieke dingen (`diskutil`, rechten) controleren.

## Lagen

```
 ui/ (PySide6)  ─┐
 cli.py          ├──►  core/engine.py ──► detectors/ ─┐
                 │      core/compare.py               ├─► db.py (cache) · hashing.py · scanner.py · drives.py
                 ├──►  core/quarantine.py ────────────┘
                 └──►  core/organizer/ (rules → planner → executor)
```
`core/` kent geen UI. UI en CLI roepen dezelfde functies aan. Zware taken draaien in `ui/common.Worker` (QThread) met `core/jobs.JobControl` voor pauze/stop.

## `dupescout/core/` – de logica

| Bestand | Rol |
|---|---|
| `models.py` | `FileInfo` (bestand + volume-id + relpath), `DupGroup` (kind, confidence, files), `human_size` |
| `drives.py` | `volume_for_path()` → `VolumeInfo` (UUID, bestandssysteem, schrijfbaar, hoofdlettergevoelig). macOS: `diskutil info -plist`; Linux: `/proc/mounts` |
| `scanner.py` | `scan_roots()`: read-only doorlopen; sluit `.photoslibrary`, `.DS_Store`, `._*`, quarantaine uit; volgt geen symlinks/andere mounts |
| `hashing.py` | `partial_hash` (begin+einde), `full_hash` (BLAKE2b), `files_identical` (byte-voor-byte) |
| `db.py` | `Cache` (SQLite, WAL): hashes per (volume, pad, grootte, mtime), quarantaine-mounts, ordenen-journal, logboek. `app_home()` = `~/Library/Application Support/DupeScout` |
| `jobs.py` | `JobControl` (pauze/hervat/annuleer), `Cancelled`, `report()` voor voortgang |
| `engine.py` | `run_detection()`: scan + gekozen detectoren → `DetectResult`. Ingang voor UI en CLI |
| `detectors/exact.py` | grootte → partial → full hash; per volume sequentieel; hardlinks samengevouwen; `content_hashes()` ook gebruikt door `compare.py` |
| `detectors/image.py` | pHash per foto (cache) → `cluster_hashes` → groepen `similar_image` |
| `detectors/media.py` | video: 5 frame-hashes via ffmpeg; audio: Chromaprint (`fpcalc`); duur ±2 s |
| `detectors/namesize.py` | genormaliseerde naam + grootte (`possible`) |
| `perceptual.py` | DCT-pHash (`phash_from_gray32`) en `cluster_hashes` (multi-index op bits, numpy) |
| `imageio.py` | afbeeldingen openen (JPEG-draft, HEIC via pillow-heif, RAW via rawpy), EXIF-datum/camera, `load_preview` |
| `thumbs.py` | thumbnail-cache op schijf |
| `compare.py` | `compare_folders(A, B)` → `CompareResult` (in_both / only_a / only_b / linked / changed_paths) |
| `selection.py` | `pick_keeper`, `auto_select_all` (overlap-veilig), `build_requests`, `resolution_lookup` |
| `quarantine.py` | **veiligheidskern**: `move_to_quarantine` (≥1 keeper, byte-check, sidecars), `restore`, `purge(confirmed=True)`, `quarantine_base` (thuismap-fallback), manifest per batch |
| `organizer/rules.py` | YAML-regels (`als`/`doel`), sjablonen `{jaar}` `{maandnaam}` …, presets |
| `organizer/metadata.py` | datum: EXIF → video-metadata → bestandsnaam → mtime |
| `organizer/planner.py` | `build_plan()` (dry-run), tekens opschonen per bestandssysteem, conflicten/duplicaten |
| `organizer/executor.py` | `execute()` met journal; cross-volume = kopie + verificatie + bron naar quarantaine; `undo()` |

## `dupescout/ui/` – de app

| Bestand | Rol |
|---|---|
| `app.py` | `MainWindow` (5 tabs), `main()` |
| `common.py` | `Worker` (QThread), `confirm/warn/info`, `pil_to_pixmap` |
| `sources.py` | tab 1: bronnen, opties, scan starten/pauzeren/stoppen |
| `results.py` | tab 2: groepenboom (lazy), marks, sneltoetsen, automatisch selecteren, naar quarantaine |
| `preview.py` | `ComparePanel` + `ImageView` (gesynchroniseerde zoom, EXIF, verschil-overlay) |
| `compare_tab.py` | tab 3: map A ↔ B, drie lijsten, CSV-export |
| `organize_tab.py` | tab 4: regels-editor, voorbeeld-boom, uitvoeren, ongedaan maken |
| `quarantine_tab.py` | tab 5: batches, herstellen, handmatig wissen, logboek |

## Overig

| Pad | Rol |
|---|---|
| `dupescout/cli.py` | `scan`, `compare`, `clean`, `quarantine`, `organize` (alles dry-run tenzij `--apply`) |
| `tests/` | `test_exact`, `test_compare`, `test_quarantine`, `test_organizer`, `test_images_media`, `test_cli`, `test_ui` (headless; `DUPESCOUT_SHOTS=map` maakt screenshots) |
| `packaging/build_macos.sh` | PyInstaller → `DupeScout.app` (+ dmg, optioneel codesign) |
| `PLAN.md` / `README.md` | plan en gebruik |

## Hoe een gebruikersactie door de code loopt

* **Scan:** `SourcesTab.start` → `Worker` → `engine.run_detection` → `scanner.scan_roots` → `detectors.*` (cache in `db.py`) → `DetectResult` → `ResultsTab.set_result`.
* **Opruimen:** `ResultsTab.auto_mark` → `selection.auto_select_all` → `to_quarantine` → `selection.build_requests` → `quarantine.move_to_quarantine` → manifest + logboek.
* **Map-vergelijk:** `CompareTab.run` → `compare.compare_folders` → `quarantine.move_to_quarantine` (alleen bestanden in B).
* **Ordenen:** `OrganizeTab.make_plan` → `planner.build_plan` (raakt niets aan) → `execute` → journal → `undo`.

## Veiligheidsregels (niet breken bij wijzigingen)
1. Scannen schrijft nooit naar de bronmappen.
2. Per verplaatst bestand blijft een niet-verplaatste kopie bestaan (afgedwongen in `quarantine.py`).
3. `purge()` vereist `confirmed=True`; nergens wordt het automatisch aangeroepen.
4. `.photoslibrary` wordt altijd overgeslagen (`scanner.FORBIDDEN_DIR_SUFFIXES`).
5. Voorstel (`build_plan`) en dry-run veranderen niets op schijf.
