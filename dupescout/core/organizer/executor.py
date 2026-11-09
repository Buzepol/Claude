"""Ordenen uitvoeren met journal en ongedaan maken."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Optional

from ..db import Cache
from ..drives import volume_for_path
from ..hashing import files_identical
from ..jobs import JobControl, ProgressFn, checkpoint, report
from .. import quarantine as q
from .planner import Plan


@dataclass
class ExecResult:
    batch_id: str
    done: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def execute(
    plan: Plan,
    cache: Cache,
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> ExecResult:
    batch_id = "org-" + q.new_batch_id()
    res = ExecResult(batch_id)
    dest_vol = volume_for_path(_existing_parent(plan.dest_root))
    if not dest_vol.writable:
        raise q.QuarantineError(f"doelvolume '{dest_vol.name}' is alleen-lezen")
    items = plan.active()
    for seq, it in enumerate(items, 1):
        try:
            checkpoint(control)
        except Exception:
            cache.flush()
            raise
        report(progress, "organize", seq - 1, len(items), it.src)
        try:
            if os.path.exists(it.dst):
                raise FileExistsError("doelbestand bestaat al")
            os.makedirs(os.path.dirname(it.dst), exist_ok=True)
            same_vol = volume_for_path(it.src).volume_id == volume_for_path(os.path.dirname(it.dst)).volume_id
            if plan.mode == "move" and same_vol:
                os.rename(it.src, it.dst)
                cache.journal_add(batch_id, seq, "rename", it.src, it.dst)
            else:
                shutil.copy2(it.src, it.dst)
                if not files_identical(it.src, it.dst):
                    os.remove(it.dst)
                    raise OSError("controle na kopiëren mislukt")
                if plan.mode == "copy":
                    cache.journal_add(batch_id, seq, "copy", it.src, it.dst)
                else:
                    r = q.move_to_quarantine([q.QuarantineRequest(it.src, [it.dst], "verplaatst naar andere schijf")], cache)
                    if r.failed:
                        raise OSError(r.failed[0][1])
                    cache.journal_add(batch_id, seq, "copy_q", it.src, it.dst, {"qbatch": r.batch_ids[0]})
            res.done += 1
        except Exception as exc:
            res.failed.append((it.src, str(exc)))
    cache.flush()
    cache.log("organize", {"batch": batch_id, "mode": plan.mode, "done": res.done, "failed": len(res.failed)})
    report(progress, "organize", len(items), len(items), "")
    return res


def _existing_parent(path: str) -> str:
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


def undo(batch_id: str, cache: Cache) -> tuple[int, list[tuple[str, str]]]:
    """Maak een ordenen-batch ongedaan (in omgekeerde volgorde). Geeft (aantal, fouten)."""
    entries = [e for e in cache.journal_entries(batch_id) if e["state"] == "done"]
    ok = 0
    failed: list[tuple[str, str]] = []
    for e in reversed(entries):
        src, dst, op = e["src"], e["dst"], e["op"]
        try:
            if op == "rename":
                if os.path.exists(src):
                    raise FileExistsError("oorspronkelijke plek is bezet")
                os.makedirs(os.path.dirname(src), exist_ok=True)
                os.rename(dst, src)
            elif op == "copy":
                r = q.move_to_quarantine([q.QuarantineRequest(dst, [src], "ordenen ongedaan gemaakt")], cache)
                if r.failed:
                    raise OSError(r.failed[0][1])
            elif op == "copy_q":
                qb = e["extra"]["qbatch"]
                restored = False
                for b in q.list_batches(cache):
                    if b.batch_id == qb:
                        items = [i["id"] for i in b.stored_items if os.path.realpath(i["original"]) == os.path.realpath(src)]
                        if items:
                            q.restore(b, items, cache)
                            restored = True
                if not restored:
                    raise OSError("bronbestand niet meer in quarantaine (al gewist of hersteld)")
                r = q.move_to_quarantine([q.QuarantineRequest(dst, [src], "ordenen ongedaan gemaakt")], cache)
                if r.failed:
                    raise OSError(r.failed[0][1])
            cache.journal_set_state(batch_id, e["seq"], "undone")
            ok += 1
        except Exception as exc:
            failed.append((dst, str(exc)))
    cache.log("organize_undo", {"batch": batch_id, "undone": ok, "failed": len(failed)})
    return ok, failed
