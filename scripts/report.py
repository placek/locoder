#!/usr/bin/env python3
"""`make report`: what the routing ledger says so far.

The question it answers: are the judge's thresholds in routing.yaml right? A coder attempt
that passed /verify is evidence the task was local-sized; one that failed is evidence it was
not. Bucketing those by the judge's P(local) shows whether the judge separates them at all,
and where local_threshold should sit.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
db_path = home / "routing" / "ledger.db"
if not db_path.exists():
    sys.exit(f"no ledger yet at {db_path}: nothing has been routed")
db = sqlite3.connect(str(db_path))
db.row_factory = sqlite3.Row

print(f"ledger: {db_path}\n")

print("rungs (all time)")
for r in db.execute("SELECT rung, COUNT(*) n, SUM(verified=1) v, SUM(verified=0) f, SUM(verified IS NULL) u, "
                    "ROUND(SUM(cost),2) cost, ROUND(AVG(duration_s)) dur FROM attempts GROUP BY rung ORDER BY rung"):
    print(f"  {r['rung']:<11} attempts {r['n']:>4}   verified {r['v'] or 0:>4}   failed {r['f'] or 0:>4}"
          f"   unlabelled {r['u'] or 0:>4}   cost {r['cost'] or 0:>8}   avg {r['dur'] or 0:>5.0f}s")

print("\nstarting rung chosen by route()")
for r in db.execute("SELECT rung, COUNT(*) n FROM decisions GROUP BY rung"):
    print(f"  {r['rung']:<11} {r['n']}")

# Judge calibration: coder outcomes bucketed by the judge's P(local).
rows = db.execute("""
    SELECT d.verdict, a.verified FROM decisions d
    JOIN attempts a ON a.decision_id = d.id AND a.rung = 'coder'
    WHERE d.verdict IS NOT NULL AND a.verified IS NOT NULL""").fetchall()
print(f"\njudge calibration on {len(rows)} labelled coder attempts")
if not rows:
    print("  none yet. The coder only runs when the judge is optimistic, so early on this")
    print("  fills slowly; failures on paid rungs do not label the judge's P(local).")
else:
    buckets: dict[int, list[int]] = {}
    for r in rows:
        p = json.loads(r["verdict"])["p_local"]
        buckets.setdefault(min(int(p * 5), 4), []).append(int(r["verified"]))
    for b in sorted(buckets):
        vals = buckets[b]
        print(f"  P(local) {b / 5:.1f}-{(b + 1) / 5:.1f}: {sum(vals):>3}/{len(vals):<3} passed "
              f"({100 * sum(vals) / len(vals):.0f}%)")
    print("  local_threshold belongs where the pass rate stops being worth the wait.")

esc = db.execute("""
    SELECT COUNT(DISTINCT decision_id) FROM attempts a
    WHERE rung != 'coder' AND decision_id IN (SELECT decision_id FROM attempts WHERE rung='coder' AND verified=0)
""").fetchone()[0]
print(f"\ncoder failures that escalated to a paid rung: {esc}")

hits = db.execute("SELECT ts, spent FROM limit_hits ORDER BY ts DESC LIMIT 4").fetchall()
if hits:
    print("\nweekly limit hits (spend when it hit; their median becomes the budget)")
    from datetime import datetime
    for h in hits:
        print(f"  {datetime.fromtimestamp(h['ts']):%Y-%m-%d %H:%M}  spent {h['spent']:.2f}")
