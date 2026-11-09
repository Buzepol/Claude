"""Commandoregel: dupescout scan | compare | clean | quarantine | organize."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from typing import Optional

from . import __version__
from .core import quarantine as q
from .core.compare import compare_folders
from .core.db import Cache
from .core.engine import DetectOptions, run_detection
from .core.models import human_size
from .core.organizer import executor, rules as rules_mod
from .core.organizer.planner import build_plan
from .core.scanner import ScanOptions, scan_roots
from .core.selection import STRATEGIES, auto_select_all, build_requests, resolution_lookup

KINDS = {"exact": "exact", "foto": "similar_image", "naam": "name_size", "media": "similar_media"}


def _progress(stage: str, done: int, total: int, detail: str) -> None:
    if sys.stderr.isatty():
        msg = f"\r[{stage}] {done}/{total or '?'} {detail[-60:]:<60}"
        sys.stderr.write(msg)
        sys.stderr.flush()


def _end_progress() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r" + " " * 90 + "\r")


def _scan_opts(args: argparse.Namespace) -> ScanOptions:
    return ScanOptions(excludes=args.exclude or [], min_size=args.min_size)


def cmd_scan(args, cache: Cache) -> int:
    modes = set(args.mode)
    opts = DetectOptions(
        exact="exact" in modes, similar_images="foto" in modes, name_size="naam" in modes,
        similar_media="media" in modes, image_threshold=args.threshold, scan=_scan_opts(args),
    )
    res = run_detection(args.paths, opts, cache, progress=_progress)
    _end_progress()
    print(f"{len(res.files)} bestanden gescand, {len(res.groups)} groepen gevonden.")
    for lib in res.skipped_libraries:
        print(f"  overgeslagen (Apple Foto's-bibliotheek): {lib}")
    for g in res.groups:
        print(f"\n[{g.kind}/{g.confidence}] {len(g.files)} bestanden, {human_size(g.reclaimable)} terug te winnen")
        for f in g.files:
            print(f"   {human_size(f.size):>9}  {f.path}")
    if res.errors:
        print(f"\n{len(res.errors)} waarschuwingen/fouten (eerste 5):", file=sys.stderr)
        for e in res.errors[:5]:
            print("  " + e, file=sys.stderr)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([{"kind": g.kind, "confidence": g.confidence, "files": [f.path for f in g.files]} for g in res.groups], fh, ensure_ascii=False, indent=1)
    return 0


def cmd_compare(args, cache: Cache) -> int:
    res = compare_folders(args.a, args.b, cache, _scan_opts(args), progress=_progress)
    _end_progress()
    print(f"In beide mappen: {sum(len(m.b_files) for m in res.in_both)} bestanden in B ({len(res.in_both)} inhouden), "
          f"{human_size(res.reclaimable)} terug te winnen in B")
    print(f"Alleen in A: {len(res.only_a)}   Alleen in B: {len(res.only_b)}   Hardlinks A↔B: {len(res.linked)}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["categorie", "pad_a", "pad_b", "grootte"])
            for m in res.in_both:
                for fb in m.b_files:
                    w.writerow(["in_beide", m.a_files[0].path, fb.path, fb.size])
            for f in res.only_a:
                w.writerow(["alleen_a", f.path, "", f.size])
            for f in res.only_b:
                w.writerow(["alleen_b", "", f.path, f.size])
        print(f"Rapport: {args.csv}")
    if args.apply:
        reqs = [q.QuarantineRequest(fb.path, [fa.path for fa in m.a_files]) for m in res.in_both for fb in m.b_files]
        if not reqs:
            print("Niets te doen.")
            return 0
        r = q.move_to_quarantine(reqs, cache)
        print(f"{len(r.moved)} bestanden in B naar quarantaine ({human_size(r.bytes_moved)}); batch {', '.join(r.batch_ids)}")
        for p, why in r.failed:
            print(f"  mislukt: {p}: {why}", file=sys.stderr)
    elif res.in_both:
        print("Dry-run: voeg --apply toe om de duplicaten in B naar quarantaine te verplaatsen.")
    return 0


def cmd_clean(args, cache: Cache) -> int:
    opts = DetectOptions(exact=True, similar_images=args.similar, scan=_scan_opts(args), image_threshold=args.threshold)
    res = run_detection(args.paths, opts, cache, progress=_progress)
    _end_progress()
    resolution = resolution_lookup(cache) if args.rule == "highest_resolution" else None
    marks = auto_select_all(res.groups, args.rule, args.prefer or [], resolution)
    reqs, _skipped = build_requests(res.groups, marks)
    total = sum(os.path.getsize(r.path) for r in reqs if os.path.exists(r.path))
    print(f"{len(reqs)} bestanden komen in aanmerking voor quarantaine ({human_size(total)}).")
    if not args.apply:
        for r in reqs[:20]:
            print("  ", r.path)
        print("Dry-run: voeg --apply toe om uit te voeren.")
        return 0
    r = q.move_to_quarantine(reqs, cache)
    print(f"{len(r.moved)} bestanden naar quarantaine ({human_size(r.bytes_moved)}); batch {', '.join(r.batch_ids)}")
    for p, why in r.failed:
        print(f"  mislukt: {p}: {why}", file=sys.stderr)
    return 0


def cmd_quarantine(args, cache: Cache) -> int:
    batches = q.list_batches(cache, tuple(args.root or ()))
    if args.action == "list":
        if not batches:
            print("Geen quarantaine-batches.")
        for b in batches:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.created))
            print(f"{b.batch_id}  {when}  {b.stored_count} bestanden  {human_size(b.stored_bytes)}  ({b.mount_point})")
        return 0
    if not args.batch:
        print("Geef een batch-id op.", file=sys.stderr)
        return 2
    sel = [b for b in batches if b.batch_id == args.batch]
    if not sel:
        print("Batch niet gevonden.", file=sys.stderr)
        return 1
    for b in sel:
        if args.action == "restore":
            n = len(q.restore(b, cache=cache))
            print(f"{n} bestanden hersteld.")
        elif args.action == "purge":
            print(f"Batch {b.batch_id}: {b.stored_count} bestanden, {human_size(b.stored_bytes)} worden DEFINITIEF gewist.")
            if not args.yes:
                if not sys.stdin.isatty() or input("Typ 'wissen' om te bevestigen: ").strip() != "wissen":
                    print("Afgebroken; niets gewist (gebruik --yes of typ 'wissen').")
                    return 1
            n, size = q.purge(b, confirmed=True, cache=cache)
            print(f"{n} bestanden gewist ({human_size(size)}).")
    return 0


def cmd_organize(args, cache: Cache) -> int:
    if args.undo:
        n, failed = executor.undo(args.undo, cache)
        print(f"{n} acties ongedaan gemaakt.")
        for p, why in failed:
            print(f"  mislukt: {p}: {why}", file=sys.stderr)
        return 1 if failed else 0
    if not args.dest or not args.paths:
        print("Gebruik: dupescout organize BRON... --dest DOEL", file=sys.stderr)
        return 2
    text = open(args.rules, encoding="utf-8").read() if args.rules else (
        rules_mod.PRESET_PHOTOS if args.preset == "fotos" else rules_mod.PRESET_DATE)
    rules = rules_mod.load_rules(text)
    sc = scan_roots(args.paths, _scan_opts(args), progress=_progress)
    plan = build_plan(sc.files, rules, args.dest, "copy" if args.copy else "move", progress=_progress)
    _end_progress()
    s = plan.summary()
    print(f"Voorstel: {s['ok']} te {'kopiëren' if args.copy else 'verplaatsen'}, {s['duplicate']} al aanwezig, "
          f"{s['already']} al op plek, {s['skip']} overgeslagen.")
    for folder, (n, size) in plan.per_folder().items():
        print(f"  {folder + '/':<40} {n:>6} bestanden  {human_size(size)}")
    for w in plan.warnings:
        print("  ! " + w)
    if not args.apply:
        for it in plan.active()[:15]:
            print(f"   {it.src}\n     -> {it.dst}  [{it.rule}, datum uit {it.date_source}]")
        print("Dry-run: voeg --apply toe om uit te voeren.")
        return 0
    res = executor.execute(plan, cache, progress=_progress)
    _end_progress()
    print(f"{res.done} bestanden verwerkt. Ongedaan maken: dupescout organize --undo {res.batch_id}")
    for p, why in res.failed:
        print(f"  mislukt: {p}: {why}", file=sys.stderr)
    return 1 if res.failed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dupescout", description="Dubbele bestanden vinden, opruimen en ordenen.")
    p.add_argument("--version", action="version", version=f"dupescout {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--exclude", action="append", help="glob-patroon om uit te sluiten (herhaalbaar)")
        sp.add_argument("--min-size", type=int, default=1, help="minimale bestandsgrootte in bytes")

    sp = sub.add_parser("scan", help="dubbelen zoeken (read-only)")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--mode", nargs="+", default=["exact"], choices=list(KINDS), help="exact, foto, naam, media")
    sp.add_argument("--threshold", type=int, default=5, help="max. Hamming-afstand voor gelijkende foto's (0-12)")
    sp.add_argument("--json")
    common(sp)
    sp.set_defaults(fn=cmd_scan)

    sp = sub.add_parser("compare", help="twee mappen vergelijken (A = referentie)")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("--csv")
    sp.add_argument("--apply", action="store_true", help="duplicaten in B naar quarantaine")
    common(sp)
    sp.set_defaults(fn=cmd_compare)

    sp = sub.add_parser("clean", help="duplicaten naar quarantaine (standaard dry-run)")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--rule", default="oldest", choices=list(STRATEGIES))
    sp.add_argument("--prefer", action="append", help="map waarvan bestanden bij voorkeur blijven")
    sp.add_argument("--similar", action="store_true", help="ook gelijkende foto's")
    sp.add_argument("--threshold", type=int, default=3)
    sp.add_argument("--apply", action="store_true")
    common(sp)
    sp.set_defaults(fn=cmd_clean)

    sp = sub.add_parser("quarantine", help="quarantaine beheren")
    sp.add_argument("action", choices=["list", "restore", "purge"])
    sp.add_argument("batch", nargs="?")
    sp.add_argument("--root", action="append", help="extra volume/mountpoint om te doorzoeken")
    sp.add_argument("--yes", action="store_true", help="wissen zonder interactieve bevestiging")
    sp.set_defaults(fn=cmd_quarantine)

    sp = sub.add_parser("organize", help="ordenen in nieuwe mappen (standaard dry-run)")
    sp.add_argument("paths", nargs="*")
    sp.add_argument("--dest")
    sp.add_argument("--rules", help="YAML-bestand met regels")
    sp.add_argument("--preset", choices=["datum", "fotos"], default="datum")
    sp.add_argument("--copy", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--undo", metavar="BATCH")
    common(sp)
    sp.set_defaults(fn=cmd_organize)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cache = Cache()
    try:
        return args.fn(args, cache)
    except (ValueError, rules_mod.RuleError, q.QuarantineError) as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nAfgebroken.", file=sys.stderr)
        return 130
    finally:
        cache.close()


if __name__ == "__main__":
    sys.exit(main())
