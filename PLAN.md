# Plan: DupeScout – hdd-vergelijker voor dubbele bestanden

Desktop-app (Python + Qt) om één of meer schijven te scannen, dubbele bestanden te vinden, te bekijken, veilig op te ruimen (quarantaine) en bestanden te ordenen in nieuwe mappen.

## 1. Gekozen uitgangspunten

| Onderwerp | Keuze |
|---|---|
| Platform | **Alleen macOS, Apple Silicon (arm64) en nieuwste macOS**. Desktop-app, Python 3.11+ met **PySide6 (Qt)**, verpakt als `.app` (arm64). |
| Gebruik | Voornamelijk **foto's**; schijven van ca. **1 TB per stuk** → foto-voorbeeld in de app en efficiënt scannen hebben prioriteit |
| Detectie | Exact (hash), gelijkende foto's, naam + grootte, gelijkende audio/video |
| Verwijderen | **Verplaatsen naar quarantainemap**; definitief wissen gebeurt **altijd handmatig** (nooit automatisch) |
| Ordenen | Op datum (jaar/maand) + eigen regels, **altijd eerst voorbeeldweergave** |
| Map-vergelijk | **Twee mappen (A en B) vergelijken**: duplicaten tussen A en B + alleen-in-A / alleen-in-B; dubbelen in B naar quarantaine |

Principe: *de scan is altijd read-only; elke wijziging is eerst een voorstel, dan een bevestiging, dan een omkeerbare actie met logboek.*

## 2. Architectuur

```
dupescout/
  core/
    scanner.py        # bestanden doorlopen, metadata verzamelen
    hashing.py        # gedeeltelijke + volledige hash
    detectors/
      exact.py        # grootte -> partial hash -> volledige hash
      compare.py      # 2-mappen-vergelijk (A vs B)
      namesize.py     # naam (genormaliseerd) + grootte
      image.py        # perceptuele hash (pHash/dHash)
      media.py        # audio/video-fingerprint
    cache.py          # SQLite-cache
    quarantine.py     # verplaatsen, manifest, herstellen, opruimen
    organizer/
      rules.py        # regel-engine
      metadata.py     # EXIF/mediadatum lezen
      planner.py      # voorstel (dry-run) opbouwen
      executor.py     # uitvoeren met journal + undo
    drives.py         # schijf-identificatie (UUID/serienummer)
  ui/                 # PySide6: vensters, modellen, voorbeeldpanelen
  tests/
```

- `core` bevat geen UI-code, zodat het los te testen is en later ook als CLI gebruikt kan worden.
- Zwaar werk draait in worker-threads/processen (QThreadPool / multiprocessing) met voortgang, pauzeren en annuleren.

### Gebruikte bibliotheken
- `PySide6` (UI), `xxhash`/`blake3` (snelle hash) + `hashlib.sha256` (verificatie)
- `Pillow`, `imagehash`, `pillow-heif` (foto's incl. HEIC), `exifread`/`piexif` (EXIF)
- `rapidfuzz` (naam-vergelijking)
- `ffmpeg`/`ffprobe` + `chromaprint (fpcalc)` voor audio/video (externe tools, optioneel gedetecteerd)
- `sqlite3` (cache en journals), `PyYAML` (regelbestanden), `pytest`, `PyInstaller` (verpakken)

## 3. Detectie

### 3.1 Exact identiek (standaard, 100% betrouwbaar)
Opeenvolgende filters zodat zo min mogelijk gelezen wordt:
1. Groepeer op **bestandsgrootte** (unieke groottes vallen af).
2. **Gedeeltelijke hash** (eerste + laatste 64 KB).
3. **Volledige hash** alleen voor overgebleven kandidaten.
4. Vóór elke verwijdering: byte-voor-byte controle.

Bijzonderheden: lege bestanden apart behandelen; symlinks niet volgen; hardlinks (zelfde inode) niet als dubbel tellen; minimale/maximale grootte als filter.

### 3.2 Naam + grootte
Genormaliseerde naam (zonder " (1)", "- kopie", hoofdletters) + gelijke grootte. Snel maar onzeker: in de UI gelabeld als *"mogelijk dubbel"* en nooit automatisch geselecteerd.

### 3.3 Gelijkende foto's
- pHash + dHash per afbeelding, ook voor HEIC/RAW-previews.
- Zoeken met **BK-tree** of LSH i.p.v. alles-met-alles; instelbare drempel (Hamming-afstand) met schuifregelaar.
- Groep toont resolutie, bestandsgrootte, datum; voorstel om de hoogste kwaliteit te behouden.

### 3.4 Gelijkende audio/video
- Audio: Chromaprint-fingerprint + duur (±2 s) → vergelijking.
- Video: duur + frames op vaste posities (bijv. 10 punten) → pHash-reeks, vergelijken met tolerantie.
- Alleen actief als ffmpeg/fpcalc gevonden wordt; anders duidelijke melding.
- Dit is het zwaarste onderdeel en komt daarom in een latere mijlpaal.

### 3.5 Cache
SQLite-tabel met sleutel `(schijf_id, relatief pad, grootte, mtime)` → hashes. Een tweede scan van een grote schijf is hierdoor veel sneller en onderbroken scans kunnen hervat worden.

## 3b. Foto-focus en macOS-specifiek

**Foto's (hoofdgebruik)**
- Ondersteunde formaten: JPEG, PNG, HEIC/HEIF, TIFF en RAW (CR2/CR3, NEF, ARW, DNG e.a.) via `pillow-heif` en `rawpy`/ingebedde preview.
- **Voorbeeld in de app:** thumbnails (gecachet op schijf, gegenereerd met Pillow/Quick Look-achtige downscaling), grote preview met zoom/pan, naast-elkaar-vergelijking met gesynchroniseerde zoom, EXIF-paneel (camera, resolutie, datum, GPS) en verschil-overlay. Met sneltoetsen (←/→, spatie = markeren, ⌫ = naar quarantaine).
- **Slimme standaardkeuze bij gelijkende foto's:** hoogste resolutie → grootste bestand → oudste opnamedatum; altijd te overrulen.
- **Bijbehorende bestanden:** RAW+JPEG-paren, HEIC + Live Photo `.MOV`, en `.xmp`/`.aae`-sidecars blijven bij elkaar (samen verplaatst naar quarantaine of doelmap).
- Foto's vergelijken kan op exacte inhoud én op gelijkenis (pHash), zodat dezelfde foto in andere resolutie/compressie ook gevonden wordt.

**macOS**
- **Volledige schijftoegang** (Full Disk Access) en toegang tot externe volumes worden bij eerste start uitgelegd en gecontroleerd.
- Schijf-identificatie via volume-UUID (`diskutil info`), zodat een externe hdd na herverbinden dezelfde cache houdt.
- **Meerdere bestandssystemen tegelijk** (APFS, HFS+, exFAT, FAT32 en NTFS). Per volume detecteert de app bij het toevoegen: bestandssysteem, schrijfbaarheid, hoofdlettergevoeligheid, maximale bestandsnaamlengte en tijdstempel-precisie, en past gedrag daarop aan:
  - APFS/HFS+/exFAT/FAT32: volledig (scannen, quarantaine, ordenen).
  - **NTFS is op macOS standaard read-only** (tenzij een extra driver is geïnstalleerd) → scannen en vergelijken werkt, maar quarantaine/ordenen wordt dan voor dat volume uitgeschakeld met uitleg.
  - Tussen verschillende bestandssystemen vergelijken we op inhoud, nooit op tijdstempel of inode (exFAT/FAT32 hebben grovere tijdstempels; bestandsnamen kunnen verschillen in Unicode-normalisatie, dat wordt genormaliseerd).
  - Verplaatsen over bestandssystemen = kopiëren + hash-verificatie + pas dan de bron naar quarantaine.
  - Tekens die op een doelsysteem niet mogen (bijv. `:` op exFAT/NTFS) worden bij ordenen gemeld in het voorbeeld.
- **Apple Foto's wordt nooit doorzocht:** `*.photoslibrary`-pakketten worden altijd overgeslagen (geen instelling om dat aan te zetten).
- Standaard uitsluiten: `.DS_Store`, `._*` (AppleDouble), `.Spotlight-V100`, `.fseventsd`, `.Trashes`, `.TemporaryItems`.
- APFS-kloonbestanden (`cp -c`) worden herkend als hetzelfde fysieke blok waar mogelijk; hardlinks via inode.
- Verpakken als ondertekende `.app` (PyInstaller of py2app), dmg; notarisatie als optioneel eindstap.

**Schaal: ca. 1 TB per schijf**
- Aanname: 100.000–400.000 foto's per schijf. Volledige hash van elk bestand zou op een hdd uren kosten, daarom: filtercascade (3.1), sequentieel lezen per schijf, gedeeltelijke hash eerst.
- Hdd-lezen bij 100–150 MB/s: ruim 1 TB ≈ 2–3 uur voor een eerste volledige scan; de cache (SQLite, `WAL`-modus) maakt herhaalde scans en hervatten snel.
- Perceptuele hashes alleen berekenen voor beeldbestanden en parallel over CPU-kernen; thumbnails pas on-demand of op de achtergrond.
- Geheugen constant door streaming; resultaten worden uit SQLite geladen (gevirtualiseerde lijst in de UI, niet alles tegelijk in RAM).
- Voortgang toont geschatte resttijd, en een scan kan veilig worden gepauzeerd/afgebroken.

## 4. Meerdere schijven vergelijken
- Gebruiker voegt **bronnen** toe (mappen/schijven) en kan per bron een rol geven: *"behouden"* (referentie) of *"opruimen"*.
- Schijven worden herkend op **UUID/volumeserienummer**, niet op stationsletter, zodat de cache blijft kloppen als een externe hdd een andere letter krijgt.
- Vergelijkingsmodi: binnen één schijf, tussen schijven, "alles in A dat ook in B staat".
- Uitsluitingen: mappen, extensies, systeem- en verborgen bestanden.
- Trage hdd's: sequentieel lezen per schijf (niet parallel op dezelfde schijf), parallel over verschillende schijven.

## 4b. Twee mappen vergelijken (A ↔ B)

Aparte modus naast "alles in één pool zoeken": de gebruiker kiest **map A (referentie)** en **map B (te controleren)**, op dezelfde of op verschillende schijven.

**Uitkomst, in vier categorieën** (op inhoud, via dezelfde hash-cascade als 3.1):
1. **In beide mappen (duplicaten)** – zelfde inhoud, ook als naam of submap verschilt. Per rij: pad in A, pad in B, grootte, datum.
2. **Alleen in A** – ontbreekt in B.
3. **Alleen in B** – ontbreekt in A.
4. *(Informatief)* **Zelfde pad, andere inhoud** wordt als aanduiding bij duplicaten-/alleen-in-lijsten gemarkeerd, maar is geen aparte actie in de eerste versie.

**Opties:** vergelijken op inhoud (standaard) of ook op naam+grootte (snel); submappen meenemen ja/nee; optioneel gelijkende foto's (3.3) meenemen in de vergelijking.

**Acties:** A is de referentie. De duplicaten in B worden via de bestaande quarantaine (sectie 6) opgeruimd: voorbeeldweergave, herverificatie, manifest, herstellen. Bestanden in A worden in deze modus nooit aangeraakt. De lijsten *alleen in A* / *alleen in B* zijn bekijk- en exporteerbaar (CSV/tekst).

**UI:** een tab "Mappen vergelijken" met twee mapkiezers, een samenvatting ("x gelijk, y alleen in A, z alleen in B, w GB terug te winnen"), drie filterbare lijsten en het bestaande vergelijkpaneel voor de voorbeeldweergave.

**Vangrails:** A en B mogen niet dezelfde of geneste map zijn (waarschuwing); hardlinks/dezelfde inode tussen A en B tellen niet als duplicaat; selectie in B kan nooit alle kopieën wissen omdat A altijd behouden blijft.

## 5. Bekijken (UI)

Tabs/vensters:
1. **Bronnen & instellingen** – mappen kiezen, detectiemodi, drempels, uitsluitingen, start/pauze/stop met voortgang.
2. **Mappen vergelijken** – zie 4b.
3. **Resultaten** – lijst/boom van duplicaatgroepen: sorteren op bespaarde ruimte, aantal, type; filteren op type, schijf, map.
4. **Voorbeeld/vergelijken** – naast elkaar: afbeelding (zoom, verschil-overlay), tekst/PDF-voorbeeld, audio/video afspelen, metadata-tabel.
5. **Selectie & acties** – slimme selectieregels (zie 6.1), samenvatting "x bestanden, y GB vrijgemaakt", knop *Naar quarantaine*.
6. **Ordenen** – zie sectie 7.
7. **Quarantaine & logboek** – overzicht, herstellen, definitief wissen, geschiedenis van acties.

## 6. Veilig verwijderen

### 6.1 Selecteren
Handmatig of via regels: behoud oudste / nieuwste, kortste pad, voorkeursschijf, voorkeursmap, hoogste resolutie. Regels zijn combineerbaar en altijd zichtbaar vóór uitvoeren.

### 6.2 Quarantaine
- Verplaatsen naar `.dupescout-quarantine/<datum>/` **op dezelfde schijf** (niet de macOS-prullenbak, die per volume `.Trashes` gebruikt en zich anders gedraagt op externe schijven) (instant rename, werkt ook zonder prullenbak op externe hdd's).
- Per batch een `manifest.json`: oorspronkelijk pad, hash, grootte, tijdstip, reden, behouden kopie.
- **Herstellen** per bestand of batch.
- **Opruimen: uitsluitend handmatig.** Er is geen timer of automatische verwijdering. In het quarantaine-tabblad zie je per batch en per schijf hoeveel ruimte het inneemt; definitief wissen vraagt per batch een expliciete bevestiging (met aantal bestanden en GB).

### 6.3 Vangrails
- Nooit alle kopieën in een groep tegelijk verwijderen (minimaal 1 blijft altijd staan, afgedwongen in `core`, niet alleen in de UI).
- Herverificatie vlak voor verplaatsing (bestand bestaat nog, grootte/hash klopt).
- Dry-run/overzicht met bevestiging; extra waarschuwing bij grote aantallen of systeemmappen.
- Read-only of niet-schrijfbare schijf: alleen rapporteren, geen acties.
- Volledig logboek (tekst + SQLite) van elke actie.

## 7. Extra functie: ordenen in nieuwe mappen

### 7.1 Datum-ordening
Doelsjabloon bijv. `{jaar}/{maand:02d}-{maandnaam}/{bestandsnaam}`.
Datumbron in volgorde: EXIF-opnamedatum → media-metadata → bestandsnaam-patroon (bijv. `IMG_20230415`) → wijzigingsdatum. Bron per bestand zichtbaar.

### 7.2 Eigen regels
Regels in UI én als YAML-bestand (deelbaar/herbruikbaar), bijvoorbeeld:

```yaml
- naam: Foto's per jaar/maand
  als: { extensie: [jpg, jpeg, heic, png], kleiner_dan: 50MB }
  doel: "Foto's/{jaar}/{maand:02d}"
- naam: Documenten per type
  als: { extensie: [pdf, docx, xlsx] }
  doel: "Documenten/{extensie}"
- naam: Screenshots
  als: { naam_regex: "^Screenshot" }
  doel: "Screenshots/{jaar}"
```

Voorwaarden: extensie/MIME, naam (regex), grootte, datum, bronmap, schijf. Eerste passende regel wint; volgorde aanpasbaar; terugvalregel voor "overig".

### 7.3 Voorbeeldweergave (verplicht)
- Planner bouwt een **voorstel** zonder iets aan te raken: boomweergave *voor → na*, aantallen per doelmap, conflicten en waarschuwingen.
- Gebruiker kan items uitsluiten of de doelmap aanpassen.
- Uitvoeren pas na expliciete bevestiging.

### 7.4 Uitvoeren
- Zelfde schijf: verplaatsen (rename). Andere schijf: kopiëren → hash verifiëren → pas dan bron verwijderen/naar quarantaine.
- Naamconflicten: automatisch nummeren, overslaan of vragen; identieke inhoud wordt gemeld als duplicaat.
- **Journal** van elke verplaatsing in SQLite → **Ongedaan maken** voor de hele batch.
- Optie "kopiëren i.p.v. verplaatsen".

## 8. Mijlpalen

| # | Mijlpaal | Resultaat |
|---|---|---|
| 1 | Skelet + scanner + cache + exacte detectie (macOS, volume-UUID) | Werkende CLI die dubbelen rapporteert op een 1 TB-schijf |
| 2 | Qt-UI: bronnen, resultaten, voortgang + foto-thumbnails | Dubbelen bekijken in de app |
| 3 | Fotovoorbeeld: groot voorbeeld, naast elkaar, EXIF, sneltoetsen | Prettig foto's vergelijken |
| 4 | Quarantaine + herstel + logboek (handmatig wissen) | Veilig opruimen |
| 5 | Twee mappen vergelijken (A↔B) | Duplicaten / alleen-in-A / alleen-in-B, dubbelen in B naar quarantaine |
| 6 | Gelijkende foto's (pHash, HEIC/RAW, drempelregelaar, bijbehorende bestanden) | Ook herschaalde/hercomprimeerde kopieën |
| 7 | Ordenen: regel-engine, voorbeeld, uitvoeren, undo | Datum- en eigen-regel-ordening |
| 8 | Gelijkende audio/video | ffmpeg/chromaprint (laagste prioriteit gezien foto-gebruik) |
| 9 | Afwerking | Instellingen, ondertekende `.app`/dmg, documentatie |

Mijlpalen 1–6 geven al een volledig bruikbare fototool; 7–8 zijn uitbreidingen.

## 9. Teststrategie
- Unit-tests op `core` met gegenereerde testmappen (identieke, bijna-identieke, hardlinks, lege bestanden, Unicode-/lange paden).
- Tests voor vangrails: nooit alle kopieën weg, herstel brengt exact originele staat terug.
- Tests voor map-vergelijk: A/B met overlap, hernoemde bestanden, geneste mappen en hardlinks; B-acties laten A onaangeroerd.
- Property-test: scan → quarantaine → herstel = identieke boomstructuur en hashes.
- Prestatietest op ~100.000 bestanden; geheugen blijft constant (streaming hash).
- UI-smoketests met `pytest-qt`; handmatige test met een echte externe hdd.

## 10. Risico's

| Risico | Aanpak |
|---|---|
| Per ongeluk waardevolle data verwijderen | Quarantaine, vangrails in core, byte-verificatie, dry-run |
| Trage hdd / grote schijven | Filtercascade, cache, sequentieel lezen, hervatbare scans |
| Valse positieven bij foto's/video | Drempels instelbaar, nooit auto-selecteren, visuele vergelijking |
| Wisselende stationsletters | Schijf-UUID i.p.v. pad |
| Quarantaine vult de schijf | Zelfde schijf = geen extra ruimte bij verplaatsen; overzicht + auto-opruimen |
| ffmpeg ontbreekt | Optionele detectie, duidelijke melding, rest blijft werken |

## 11. Beslissingen en wat er nog openstaat

Besloten: alleen macOS (Apple Silicon, nieuwste versie) · foto's uit losse mappen op de hdd's, Apple Foto's wordt niet doorzocht · meerdere bestandssystemen ondersteund · voornamelijk foto's met voorbeeld in de app · schijven van ca. 1 TB · quarantaine altijd handmatig wissen.

Er zijn geen blokkerende vragen meer; de bouw kan starten bij mijlpaal 1.
