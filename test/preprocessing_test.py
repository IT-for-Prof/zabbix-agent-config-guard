#!/usr/bin/env python3
"""Regression check for the template's JavaScript preprocessing and its dependency wiring.

The two preprocessing scripts are the only real logic in this template, and their failure mode
is silent: a wrong substring offset or an altered regexp still produces a plausible value every
hour, and no nodata or unsupported-item trigger notices. The wrong ServerActive only shows up
during a failover that does not happen.

The scripts are read out of the template YAML rather than copied here, so this check cannot drift
from what actually ships. Duktape is ES5.1-ish, so the scripts avoid ES6; Node runs them faithfully
enough for these assertions. Every case below is a row of the README's "Verified behaviour" tables,
each of which was also executed against a live Zabbix 7.0 server.

Usage:  python3 test/preprocessing_test.py     (needs python3 + node + PyYAML, no other packages)
"""
import json
import pathlib
import subprocess
import sys

import yaml

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "zabbix-agent-config-guard.yaml"

# Parents that live in another template, copied from the stock "Windows by Zabbix agent active"
# export, vendor version 7.0-2. Zabbix matches a dependency by the parent's expression, so a stock
# rewording breaks linking on every host - loudly, at import. Refresh these from the export rather
# than editing the template to match a stale copy.
EXTERNAL_PARENTS = {
    ("Windows: Active checks are not available",
     "min(/Windows by Zabbix agent active/zabbix[host,active_agent,available],{$AGENT.TIMEOUT})=2"),
    ("Windows: Zabbix agent is not available",
     "nodata(/Windows by Zabbix agent active/agent.ping,{$AGENT.NODATA_TIMEOUT})=1"),
}

Z = yaml.safe_load(TEMPLATE.read_text())["zabbix_export"]
TEMPLATES = {t["template"]: t for t in Z["templates"]}
LINUX = "Zabbix agent config guard Linux by Zabbix agent active"
WINDOWS = "Zabbix agent config guard Windows by Zabbix agent active"

failures = []


def check(label, got, want):
    if got == want:
        print(f"PASS  {label}")
    else:
        failures.append(label)
        print(f"FAIL  {label}\n        got  {got!r}\n        want {want!r}")


def script(template, key):
    for item in TEMPLATES[template]["items"]:
        if item["key"] == key:
            for step in item.get("preprocessing", []):
                if step["type"] == "JAVASCRIPT":
                    return step["parameters"][0]
    raise SystemExit(f"no JAVASCRIPT preprocessing for {key!r} in {template!r}")


def run(body, value):
    """Execute one preprocessing script the way Zabbix does: value in, string out."""
    program = (f"var value = {json.dumps(value)};\n"
               f"var out = (function () {{\n{body}\n}})();\n"
               "process.stdout.write(String(out));")
    proc = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


BASE = "Server=127.0.0.1\nHostname=test\n"

# ---------------------------------------------------------------- ServerActive
sa = script(LINUX, "agent.conf.serveractive")
CASES_SA = [
    ("single address",                    "ServerActive=mon.example.com\n",          "mon.example.com"),
    ("cluster, semicolon",                "ServerActive=a.example;b.example\n",      "a.example;b.example"),
    ("port kept",                         "ServerActive=mon.example.com:10051\n",    "mon.example.com:10051"),
    ("IPv6 in brackets",                  "ServerActive=[::1]:10051\n",              "[::1]:10051"),
    ("trailing whitespace stripped",      "ServerActive=mon.example.com   \n",       "mon.example.com"),
    ("CR stripped",                       "ServerActive=mon.example.com\r\n",        "mon.example.com"),
    ("comma survives to fail the regexp", "ServerActive=a.example,b.example\n",      "a.example,b.example"),
    ("space survives to fail the regexp", "ServerActive=a.example, b.example\n",     "a.example, b.example"),
    ("absent reads empty",                "",                                        ""),
    ("commented out reads empty",         "#ServerActive=mon.example.com\n",         ""),
    ("indented reads empty",              "  ServerActive=mon.example.com\n",        ""),
    ("spaces around = read empty",        "ServerActive = mon.example.com\n",        ""),
    ("wrong case reads empty",            "serveractive=mon.example.com\n",          ""),
    ("last line wins",                    "ServerActive=a,b\nServerActive=good\n",   "good"),
    ("last line wins even when broken",   "ServerActive=good\nServerActive=a,b\n",   "a,b"),
]
for label, body, want in CASES_SA:
    check(f"ServerActive: {label}", run(sa, BASE + body), want)

# ---------------------------------------------------------------- HeartbeatFrequency
hb = script(LINUX, "agent.conf.heartbeat")
CASES_HB = [
    ("absent means the agent default", "",                            "60"),
    ("explicit 60",                    "HeartbeatFrequency=60\n",     "60"),
    ("explicit 0 - the alerting case", "HeartbeatFrequency=0\n",      "0"),
    ("top of the documented range",    "HeartbeatFrequency=3600\n",   "3600"),
    ("out of range stored as written", "HeartbeatFrequency=3601\n",   "3601"),
    ("far out of range, still as-is",  "HeartbeatFrequency=99999\n",  "99999"),
    ("last line wins, 0 then 60",      "HeartbeatFrequency=0\nHeartbeatFrequency=60\n", "60"),
    ("last line wins, 60 then 0",      "HeartbeatFrequency=60\nHeartbeatFrequency=0\n", "0"),
    ("commented out",                  "#HeartbeatFrequency=0\n",     "60"),
    ("indented",                       "  HeartbeatFrequency=0\n",    "60"),
    ("spaces around =",                "HeartbeatFrequency = 0\n",    "60"),
    ("leading space in the value",     "HeartbeatFrequency= 0\n",     "60"),
    ("wrong case",                     "heartbeatfrequency=0\n",      "60"),
    ("non-numeric",                    "HeartbeatFrequency=abc\n",    "60"),
    ("empty value",                    "HeartbeatFrequency=\n",       "60"),
    ("negative",                       "HeartbeatFrequency=-1\n",     "60"),
    ("trailing whitespace stripped",   "HeartbeatFrequency=0   \n",   "0"),
    ("no final newline",               "HeartbeatFrequency=0",        "0"),
]
for label, body, want in CASES_HB:
    check(f"HeartbeatFrequency: {label}", run(hb, BASE + body), want)

# ---------------------------------------------------------------- structure
# The two templates are deliberate copies - Zabbix refuses two templates defining the same item key
# on one host - so the copy has to stay a copy. Only the OS-specific config path may differ.
for key in ("agent.conf.serveractive", "agent.conf.heartbeat"):
    check(f"{key}: both templates run the identical script", script(LINUX, key), script(WINDOWS, key))

# Zabbix matches a dependency by the parent's expression, not its name, so editing a parent's
# expression silently orphans every dependency pointing at it and the whole import is rejected.
known = {(t["name"], t["expression"]) for t in Z["triggers"]} | EXTERNAL_PARENTS
orphans = [(t["name"], dep["name"]) for t in Z["triggers"] for dep in t.get("dependencies", [])
           if (dep["name"], dep["expression"]) not in known]
check("every trigger dependency still matches its parent's expression", orphans, [])

# The asymmetry is deliberate and load-bearing: two pfSense hosts cannot carry the stock Linux
# template, so a dependency there would make this template unlinkable on them. Pin it, or a
# well-meaning edit "for symmetry" silently drops the guard from those hosts.
def deps_of(template, name):
    want = f"/{template}/"
    for t in Z["triggers"]:
        if t["name"] == name and want in t["expression"]:
            return {d["name"] for d in t.get("dependencies", [])}
    raise SystemExit(f"no trigger {name!r} in {template!r}")

check("Windows collection trigger depends on both stock availability triggers",
      deps_of(WINDOWS, "Zabbix agent: configuration not collected"), {n for n, _ in EXTERNAL_PARENTS})
check("Linux collection trigger stays self-contained",
      deps_of(LINUX, "Zabbix agent: configuration not collected"), set())
for tpl in (LINUX, WINDOWS):
    for name in ("Zabbix agent: ServerActive is malformed", "Zabbix agent: active-check heartbeat is disabled"):
        os_name = "Windows" if tpl is WINDOWS else "Linux"
        check(f"{os_name}: {name.split(': ')[1]} hangs off the collection trigger",
              deps_of(tpl, name), {"Zabbix agent: configuration not collected"})

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all preprocessing and wiring checks passed")
