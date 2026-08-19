# Zabbix agent config guard

Reads the Zabbix agent's own configuration file and alerts when `ServerActive` is malformed.

## Why this exists

`ServerActive` separates **independent servers** with a comma and **cluster members** with a
semicolon:

```
ServerActive=mon2.example.com;mon3.example.com   # one cluster, agent uses one node, fails over
ServerActive=mon2.example.com,mon3.example.com   # two independent servers, agent sends to both
```

Writing the comma where the semicolon was meant is **not fatal and therefore silent**. The agent
still reaches a reachable address, data keeps arriving, every dashboard stays green — and the
failover the configuration was supposed to provide does not exist. Nothing in Zabbix reports it.

It cannot be detected from the server side. Both forms produce an identical stream of active checks
as far as the receiving node is concerned. And no agent key returns the running configuration —
`agent.hostname` is the only config parameter an agent will disclose, by accident of the protocol.
The file is the only surface there is.

## What it monitors

| Item | Key | Notes |
|---|---|---|
| Agent config file | `vfs.file.contents[{$AGENT.CONF.PATH}]` | Master item, `history: 0` — nothing is stored |
| Effective ServerActive | `agent.conf.serveractive` | Dependent, takes the **last** matching line |

The last-line rule is not pedantry. A duplicated `ServerActive=` after a hand edit is applied by the
agent in file order, so a first-match read — which is all `vfs.file.regexp` can do — reports the
stale line and shows green on a broken host.

## Triggers

```
Zabbix agent: configuration not collected   AVERAGE   (guards the check)
   └─ Zabbix agent: ServerActive is malformed   AVERAGE
```

Severity is **AVERAGE** rather than WARNING for one operational reason: many estates route
notifications by severity, and a WARNING-only trigger reaches the dashboard but no one's phone. Lower
it to WARNING where a config-hygiene check should stay dashboard-only.

The root trigger is the non-obvious one. `find(...,"regexp",...)` contains no date/time function, so
it is recalculated **only when a new value arrives**. The moment collection stops, the malformed
trigger freezes in whatever state it held last — almost always OK — and the check silently stops
being a check. `nodata()` on the same item catches that, and the master item's error text names the
cause: wrong path, no read permission, or an agent that has stopped doing active checks at all.

That last case matters more than it looks. The stock *Active checks are not available* trigger fires
on host availability **Unavailable**; a host whose availability never leaves **Unknown** is never
covered by it. This guard does not care about the distinction.

## Verified behaviour

Every row below was executed against a live Zabbix 7.0 server, not reasoned about — the value was
pushed through the real master item, the real JavaScript preprocessing and the real trigger.

| Config line | Stored value | Trigger |
|---|---|---|
| `ServerActive=mon.example.com` | `mon.example.com` | ok |
| `ServerActive=a.example;b.example` | `a.example;b.example` | ok |
| `ServerActive=mon.example.com:10051` | `mon.example.com:10051` | ok |
| `ServerActive=[::1]:10051` | `[::1]:10051` | ok |
| `ServerActive=mon.example.com   ` | `mon.example.com` | ok — trailing space stripped |
| CRLF file, valid value | `mon.example.com` | ok — CR stripped |
| `ServerActive=a.example,b.example` | `a.example,b.example` | **PROBLEM** |
| `ServerActive=a.example, b.example` | `a.example, b.example` | **PROBLEM** |
| `ServerActive=a.example; b.example` | `a.example; b.example` | **PROBLEM** |
| `ServerActive=a.example<TAB>b.example` | as written | **PROBLEM** |
| `ServerActive=mon.example.com,` | `mon.example.com,` | **PROBLEM** |
| `ServerActive=` | `` | **PROBLEM** |
| parameter absent | `` | **PROBLEM** |
| `#ServerActive=mon.example.com` | `` | **PROBLEM** |
| `  ServerActive=mon.example.com` | `` | **PROBLEM** |
| `ServerActive = mon.example.com` | `` | **PROBLEM** |
| `serveractive=mon.example.com` | `` | **PROBLEM** |
| duplicate lines, malformed then valid | `mon.example.com` | ok |
| duplicate lines, valid then malformed | the malformed one | **PROBLEM** |

The last four "no match" rows return an **empty string** rather than throwing. Throwing looks
tidier and is wrong: it puts the item into an unsupported state, which puts the trigger into
UNKNOWN, and the heaviest case of all — `ServerActive` missing entirely — then waits three hours for
the `nodata` guard instead of alerting at once. An empty value fails the regexp and alerts
immediately.

The regexp default was chosen the same way:

| `{$AGENT.SERVERACTIVE.MATCHES}` | `a;b` | `a; b` | `a<TAB>b` |
|---|---|---|---|
| `^[^,\s]+$` (default) | ok | fires | fires |
| `^[^,[:space:]]+$` | ok | fires | fires |
| `^[^,]+$` | ok | **misses** | **misses** |
| `^[^,; ]+$` | **false alarm** | fires | **misses** |

`nodata` was measured too: with a 5 m window the trigger fires 5 m 19 s after the last value,
identically on the master and on the dependent item.

## What it deliberately does NOT do

This template reads **written intent**, not running state, and says so rather than implying more:

- **An edited file is not an applied file.** The agent re-reads its configuration only on restart. A
  fixed file clears the alert while the old value is still in effect, and the reverse.
- **`Include` files are invisible.** A `ServerActive` set in an included file overrides the main
  config and is not read here. On the estate this was built for, the include directory holds only
  `zabbix-agent2-plugin-*` package configs, which never carry connection settings — measured, not
  assumed. Re-check that assumption before relying on it elsewhere.
- **Semantics are out of reach.** `a;b` where `a` and `b` are genuinely different servers is wrong
  and passes. So does a perfectly formed address pointing at the wrong proxy for the host's
  assignment — that would need to be compared against the host's `monitored_by`, which no trigger
  expression can reach.
- **Passive-only hosts are out of scope.** `ServerActive` is never used there, so a stale value is
  dormant rather than operational. Link the template when such a host is converted to active checks.

## Requirements

- Zabbix server **7.0 or later**, Zabbix agent 2 (or agent 1) **with active checks enabled**.
- The agent must be able to read its own config file. It is `0644 root:root` by default; hardening it
  to `0600` breaks the check, which the collection trigger reports rather than hides.
- The config file must be under **64 KB** — the `vfs.file.contents` limit. A stock `zabbix_agent2.conf`
  is around 26 KB.
- Link **one** of the two templates per host. Both define the same keys and Zabbix refuses the second.

## Macros

| Macro | Default | Meaning |
|---|---|---|
| `{$AGENT.CONF.PATH}` | `/etc/zabbix/zabbix_agent2.conf` (Windows: `C:\Program Files\Zabbix Agent 2\zabbix_agent2.conf`) | Absolute path of the agent config file |
| `{$AGENT.SERVERACTIVE.MATCHES}` | `^[^,\s]+$` | Regexp every legitimate value must match |
| `{$AGENT.CONF.NODATA}` | `3h` | Silence of the check itself before alerting (~3× the item interval) |

The default regexp encodes a policy — *this estate has one Zabbix cluster, therefore no commas* —
rather than a hostname, so it survives estates with several correct values. Tighten it to the exact
expected string where there is only one.

## Cost

One 1-hour active check per host, transferring the config file; the master stores nothing and the
dependent stores one short string. On 61 hosts that is roughly 1.5 MB/hour of traffic and a few
kilobytes a day of history.

## Install

Import `zabbix-agent-config-guard.yaml`, link the Linux or Windows template to hosts that use active
checks. Nothing else — the macros carry working defaults.

Two notes on timing and tooling:

- A newly linked active item reaches the agent in a few minutes directly, and up to about 15 minutes
  through a proxy. Changed preprocessing needs one `CacheUpdateFrequency` cycle (60 s by default)
  before the server applies it — a check run inside that window reports the old behaviour.
- Import through the frontend or `zabbix_utils`. The `mcp__zabbix` wrapper fails on `triggers:`
  sections and on block scalars; import the items and create the triggers via `trigger.create` if
  that is the only path available.

## License

MIT — see [LICENSE](LICENSE).

Author: Konstantin Tyutyunnik / Константин Тютюнник · <https://itforprof.com>
