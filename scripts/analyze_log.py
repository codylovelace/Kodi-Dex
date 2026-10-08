#!/usr/bin/env python3
"""Where Dex Hub's time goes, from a Kodi debug log.

Usage:
    python3 scripts/analyze_log.py kodi.log [--top 15]

Needs Kodi's debug logging (Settings > System > Logging > Enable debug
logging) while you use Dex Hub; then send kodi.log.

What it reads (formats from Kodi 21's own code):
  * CPythonInvoker(<id>, <script>): start processing   PythonInvoker.cpp:145
  * CPythonInvoker(<id>):  <arg>   (after "adding args:")  PythonInvoker.cpp:271
  * CPythonInvoker(<id>, <script>): script successfully run / failure in
    script / script aborted                             PythonInvoker.cpp:332-348
  * CPythonInvoker(<id>, <script>): script termination took <n>ms   :508
  * CPythonInvoker(<id>, <script>): waiting on thread <n>             :396
  * Dex Hub's own "[DexHub] skin: <what> in <n> ms" lines (skinui/serve.py)

For each Dex Hub plugin call: total = start processing -> script finished
(includes starting a fresh Python, which Kodi does for every call here),
own = Dex Hub's logged time for the call, overhead = total - own.
"""
import argparse
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime

LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) T:(\d+)\s+(\w+) <[^>]*>: (.*)$")
START = re.compile(r"^CPythonInvoker\((\d+), (.+?)\): start processing$")
ARG = re.compile(r"^CPythonInvoker\((\d+)\):  (.*)$")
END = re.compile(r"^CPythonInvoker\((\d+), (.+?)\): (script successfully run|failure in script|script aborted)$")
TERM = re.compile(r"^CPythonInvoker\((\d+), .+?\): script termination took (\d+)ms$")
WAIT = re.compile(r"^CPythonInvoker\((\d+), .+?\): waiting on thread (\S+)")
OWN = re.compile(r"^\[DexHub\] skin: (.+?) in (\d+) ms")
TIMED = re.compile(r"^\[DexHub\].*?\bin (\d+(?:\.\d+)?) ?ms\b")
ADDON = "plugin.video.dexhub"
BURST_GAP = 5.0  # seconds without a new call that end a burst (e.g. one Home opening)


class Call(object):
    def __init__(self, inv_id, script, ts, thread):
        self.id, self.script, self.start, self.thread = inv_id, script, ts, thread
        self.args = []
        self.end = None
        self.outcome = None
        self.term_ms = None
        self.own = []          # (what, ms)
        self.waits = 0

    @property
    def service(self):
        return self.script.replace("\\", "/").endswith("/service.py")

    @property
    def query(self):
        return self.args[2] if len(self.args) > 2 else ""

    @property
    def action(self):
        m = re.search(r"(?:^|[?&])action=([^&]+)", self.query)
        return m.group(1) if m else ("(service)" if self.service else "(no action)")

    @property
    def total_ms(self):
        if self.end is None:
            return None
        return (self.end - self.start).total_seconds() * 1000.0

    @property
    def own_ms(self):
        """The line timing the whole call ("<action> in N ms", or
        "skin_row <tab>/<id> in N ms"); other lines time steps inside it."""
        act = self.action
        whole = [ms for what, ms in self.own if what == act or what.startswith(act + " ")]
        return max(whole) if whole else None


def stamp(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")


def parse(path):
    calls, by_id, timed, notes = [], {}, [], defaultdict(int)
    open_on_thread = {}
    debug_lines = 0
    last_arg_id = None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            m = LINE.match(raw.rstrip("\n"))
            if not m:
                continue
            ts, thread, level, msg = stamp(m.group(1)), m.group(2), m.group(3), m.group(4)
            if level == "debug":
                debug_lines += 1
            s = START.match(msg)
            if s:
                call = Call(s.group(1), s.group(2), ts, thread)
                if ADDON in call.script:
                    calls.append(call)
                    by_id[call.id] = call
                    open_on_thread[thread] = call
                continue
            if msg.endswith("adding args:"):
                last_arg_id = re.match(r"^CPythonInvoker\((\d+)\)", msg)
                last_arg_id = last_arg_id.group(1) if last_arg_id else None
                continue
            a = ARG.match(msg)
            if a and a.group(1) in by_id and a.group(1) == last_arg_id:
                by_id[a.group(1)].args.append(a.group(2))
                continue
            e = END.match(msg)
            if e and e.group(1) in by_id:
                call = by_id[e.group(1)]
                call.end, call.outcome = ts, e.group(3)
                if open_on_thread.get(call.thread) is call:
                    del open_on_thread[call.thread]
                continue
            t = TERM.match(msg)
            if t and t.group(1) in by_id:
                by_id[t.group(1)].term_ms = int(t.group(2))
                continue
            w = WAIT.match(msg)
            if w and w.group(1) in by_id:
                by_id[w.group(1)].waits += 1
                continue
            if msg.startswith("[DexHub]"):
                o = OWN.match(msg)
                call = open_on_thread.get(thread)
                if o and call is not None:
                    call.own.append((o.group(1), int(o.group(2))))
                tm = TIMED.match(msg)
                if tm:
                    timed.append((ts, "service" if call is not None and call.service else
                                  ("call" if call is not None else "worker thread"), msg))
                if level in ("warning", "error"):
                    notes["Dex Hub %s lines" % level] += 1
    return calls, timed, notes, debug_lines


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def fmt(v):
    return "-" if v is None else "%.0f" % v


def max_overlap(calls):
    events = []
    for c in calls:
        if c.end is not None:
            events += [(c.start, 1), (c.end, -1)]
    best = cur = 0
    for _, step in sorted(events):
        cur += step
        best = max(best, cur)
    return best


def bursts(calls):
    out, current, last = [], [], None
    for c in sorted(calls, key=lambda c: c.start):
        if last is not None and (c.start - last).total_seconds() > BURST_GAP:
            out.append(current)
            current = []
        current.append(c)
        last = max(last or c.start, c.end or c.start)
    if current:
        out.append(current)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log")
    parser.add_argument("--top", type=int, default=15, help="slowest calls to list")
    args = parser.parse_args()
    calls, timed, notes, debug_lines = parse(args.log)

    if not debug_lines:
        sys.exit("No debug lines in this log: turn on Settings > System > Logging > "
                 "Enable debug logging, restart Kodi, use Dex Hub, then send kodi.log again.")
    plugin = [c for c in calls if not c.service and c.end is not None]
    if not plugin:
        sys.exit("Debug logging is on, but no finished Dex Hub plugin calls were found.")

    print("Dex Hub plugin calls: %d (max %d at once)  |  service starts: %d"
          % (len(plugin), max_overlap(plugin), sum(1 for c in calls if c.service)))
    print()
    print("Per action (ms)          calls  total p50  p90   max   own p50  overhead p50  termination p50")
    groups = defaultdict(list)
    for c in plugin:
        groups[c.action].append(c)
    for action, group in sorted(groups.items(), key=lambda kv: -sum(c.total_ms for c in kv[1])):
        totals = [c.total_ms for c in group]
        owns = [c.own_ms for c in group if c.own_ms is not None]
        over = [c.total_ms - c.own_ms for c in group if c.own_ms is not None]
        terms = [c.term_ms for c in group if c.term_ms is not None]
        print("%-24s %5d  %9s %5s %5s   %7s  %12s  %15s" % (
            action[:24], len(group), fmt(pct(totals, .5)), fmt(pct(totals, .9)), fmt(max(totals)),
            fmt(pct(owns, .5)), fmt(pct(over, .5)), fmt(pct(terms, .5))))
    print()
    print("Bursts (calls with under %.0f s between them, e.g. one Home opening):" % BURST_GAP)
    for b in bursts(plugin):
        span = (max(c.end for c in b) - b[0].start).total_seconds() * 1000
        rows = sum(1 for c in b if c.action == "skin_row")
        print("  %s  %3d calls (%d rows) over %6.0f ms, %d at once"
              % (b[0].start.strftime("%H:%M:%S"), len(b), rows, span, max_overlap(b)))
    print()
    print("Slowest %d calls:" % args.top)
    for c in sorted(plugin, key=lambda c: -c.total_ms)[:args.top]:
        own = ", ".join("%s %d" % w for w in c.own) or "no Dex Hub timing"
        print("  %6.0f ms  %-14s %s  [%s]%s" % (c.total_ms, c.action[:14], c.start.strftime("%H:%M:%S.%f")[:-3],
                                                own, "  waited on threads" if c.waits else ""))
    waits = sum(c.waits for c in plugin)
    # "script aborted" is SystemExit (PythonInvoker.cpp:346), which Dex Hub's
    # skin widgets raise on purpose (skinui/serve.py widget()); not a failure
    failed = [c for c in plugin if c.outcome == "failure in script"]
    unfinished = [c for c in calls if not c.service and c.end is None]
    print()
    print("Calls that waited on threads: %d  |  failed: %d  |  never finished: %d"
          % (waits, len(failed), len(unfinished)))
    for k, v in sorted(notes.items()):
        print("%s: %d" % (k, v))
    if timed:
        print()
        print("Other Dex Hub timings (service and workers):")
        for ts, where, msg in timed:
            if where != "call":
                print("  %s  %-13s %s" % (ts.strftime("%H:%M:%S.%f")[:-3], where, msg[:150]))


if __name__ == "__main__":
    main()
