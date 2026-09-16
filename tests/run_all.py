#!/usr/bin/env python3
"""Run every suite and summarise. Qt needs a platform even for offscreen widgets:

    QT_QPA_PLATFORM=offscreen python3 tests/run_all.py
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
failed, total = [], 0
for suite in sorted(here.glob("test_*.py")):
    done = subprocess.run([sys.executable, str(suite)], capture_output=True, text=True)
    last = [line for line in done.stdout.splitlines() if "passed" in line]
    summary = last[-1].strip() if last else "no summary"
    print(f"{suite.stem:<16} {summary}")
    if done.returncode != 0:
        failed.append(suite.stem)
        print(done.stdout[-1500:] or done.stderr[-1500:])
    total += int(summary.split()[0]) if summary.split()[0].isdigit() else 0

print(f"\n{total} checks across {len(list(here.glob('test_*.py')))} suites")
if failed:
    print("FAILED:", ", ".join(failed))
sys.exit(1 if failed else 0)
