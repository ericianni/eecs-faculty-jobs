#!/usr/bin/env python3
"""Dry-run the relevance filter over the current jobs.json: python3 check_relevance.py [-v]"""
import json, sys, relevance as R
J = json.load(open("jobs.json"))["jobs"]
keep, drop = [], []
for j in J:
    ok, why = R.classify(j); (keep if ok else drop).append((why, j))
print(f"total {len(J)} | keep {len(keep)} | drop {len(drop)}")
print("\n=== WOULD DROP")
for why, j in drop: print(f"  [{j['field']}] {j['title'][:90]} @ {j['institution'][:45]}\n      {why}")
print("\n=== KEPT, BORDERLINE (off-topic word present, or generic title)")
for why, j in keep:
    if any(k in why for k in ("borderline", "off-topic", "generic")):
        print(f"  [{j['field']}] {j['title'][:90]} @ {j['institution'][:45]}\n      {why}")
if "-v" in sys.argv:
    print("\n=== KEPT, EECS area")
    for why, j in keep: print(f"  {j['title'][:90]} @ {j['institution'][:40]} -- {why}")
