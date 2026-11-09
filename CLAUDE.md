# DupeScout – werkafspraken voor Claude Code

Zie `CODEMAP.md` voor waar alles staat en `README.md` voor gebruik. Taal van UI en documentatie: Nederlands.

## Commando's
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,raw]"
pytest -q                          # alle tests (UI draait headless)
dupescout-gui                      # app starten
./packaging/build_macos.sh         # .app/.dmg bouwen (Apple Silicon)
```

## Regels
* Houd `dupescout/core` vrij van UI-imports.
* Verander de veiligheidsregels onderaan `CODEMAP.md` niet zonder overleg; er zijn tests voor (`tests/test_quarantine.py`).
* Test destructieve acties nooit op echte data: gebruik tijdelijke mappen of een kleine testmap op de hdd, en begin met dry-run.
* Op macOS nog te verifiëren: `drives.py` (`diskutil`), `.app`-build, Full Disk Access, NTFS/exFAT-gedrag, foto's op een echte hdd (snelheid).
* Zonder vraag geen commit/push of PR.
