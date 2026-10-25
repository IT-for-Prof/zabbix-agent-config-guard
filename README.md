# Zabbix agent config guard

Checks `ServerActive` policy and active-check heartbeat settings in the Zabbix agent's configuration
file. Includes Linux/pfSense and Windows variants for Zabbix 7.0 and later.

## Why this exists

`ServerActive` separates independent servers with commas and members of one HA cluster or proxy group
with semicolons:

```ini
ServerActive=mon2.example.com;mon3.example.com
```

A comma in place of the semicolon keeps data flowing but changes the intended failover topology.
The default policy rejects commas, empty values and internal whitespace. Zabbix itself permits
independent servers and whitespace in address lists: this policy is not a complete syntax validator.
Override `{$AGENT.SERVERACTIVE.MATCHES}` where a different topology is intentional.

## What it monitors

| Item | Key | Collection and storage |
|---|---|---|
| Configuration file | `vfs.file.contents["{$AGENT.CONF.PATH}"]` | Active check every 1h; raw file history is disabled |
| Written ServerActive | `agent.conf.serveractive` | Dependent item; last matching declaration; 7d history |
| Written heartbeat interval | `agent.conf.heartbeat` | Dependent item; seconds; 7d history |

Both parsers accept indentation and whitespace around `=`. Comments and other parameter names are
ignored. `ServerActive` remains a CHAR item with a 255-character limit; longer values require a planned
move to TEXT that checks history compatibility.

The heartbeat parser accepts integer seconds, agent 2 time suffixes (`s`, `m`, `h`, `d`, `w`) and
classic-agent binary suffixes (`K`, `M`, `G`, `T`). Examples: `1m` becomes 60; `1K` becomes 1024;
`0`, `0s` and `0K` become 0. The absent parameter defaults to 60. An explicitly empty value becomes 0
according to agent 2 rules. Invalid or out-of-range intervals produce a preprocessing error.

This normalization does not prove that the installed agent accepts that spelling. For example,
classic agent 7.0.32 accepts `1K` but rejects `0s`, `+0` and an empty value; agent 2 accepts the latter
forms. Validate edited configuration with the installed agent before restarting it.

## Triggers

| Trigger | Severity | Meaning |
|---|---|---|
| Configuration not collected | Average | Either derived item received no valid data for `{$AGENT.CONF.NODATA}` |
| ServerActive does not match policy | Average | The extracted value fails `{$AGENT.SERVERACTIVE.MATCHES}` |
| Active-check heartbeat is disabled | Warning | The configured heartbeat interval is 0 |

Both configuration alarms depend on the collection alarm. The collection expression uses `nodata()`
on **both** derived items: it detects a stopped agent, an unreadable file, and a heartbeat parser error
while ServerActive continues updating. Do not add discard-unchanged preprocessing to this chain
without adjusting its missing-data window.

A silent active agent can leave item state Normal and an empty error. First check agent availability;
then inspect the master and dependent items for path, permission and preprocessing errors. The raw
file is transferred for processing even though it is not retained in item history.

### Availability dependencies

```text
Agent availability: heartbeat OR agent.ping nodata
└─ Configuration not collected
   ├─ ServerActive does not match policy
   └─ Active-check heartbeat is disabled
```

Use **both** availability parents where available. Heartbeat availability can remain Unknown (0),
so the fast trigger checking Unavailable (2) is not a substitute for `nodata(agent.ping)`.
Dependencies are recursive; an existing problem is not closed retroactively by adding a dependency.

- **Windows:** the export depends on both stock triggers from `Windows by Zabbix agent active`.
  Link that template to the same host. After linking or updating, check that the inherited dependencies
  reference availability triggers on that host, rather than the template's trigger IDs.
- **Linux:** the export stays self-contained. During deployment, add host-level dependencies from
  its collection trigger to both local stock Linux availability triggers.
- **pfSense:** keep the standalone Linux variant and use the existing local agent-ping missing-data
  trigger. Do not link the stock Linux OS template: it conflicts with pfSense item keys.

Host-level dependencies are deployment settings and are not contained in a template export.
Recheck them after importing, relinking or onboarding a host. Keep existing unrelated dependencies
when adding availability parents. Use a collection window longer than the availability windows.

## Macros

| Macro | Default | Meaning |
|---|---|---|
| `{$AGENT.CONF.PATH}` | `/etc/zabbix/zabbix_agent2.conf` | File to inspect; Windows default is `C:\Program Files\Zabbix Agent 2\zabbix_agent2.conf` |
| `{$AGENT.SERVERACTIVE.MATCHES}` | `^[^,\s]+$` | ServerActive deployment policy |
| `{$AGENT.CONF.NODATA}` | `3h` | Missing-data window for either derived item; three times the collection interval |

Use the agent 1 configuration path when applicable. The service account must be able to read the
selected file; the size limits of `vfs.file.contents` depend on the agent version. There is no universal
64 KB limit: agent 2 versions 7.0.0 and 7.0.32 both read a 70,001-byte test file.

## Applicability and limitations

- Active checks must be enabled. On a passive-only host, ServerActive is not used.
- Heartbeat-based availability needs agent version 6.2 or newer. An absent parameter on an older
  agent does not mean that agent supports heartbeats.
- Link only one variant to a host: they define the same item keys.
- This reads a selected file, not the running configuration. Restart the agent to apply connection changes.
- Include files are not read and can contain a different effective value.
- The template does not validate DNS, reachability or whether an address matches the assigned proxy.

## Install and update

Import `zabbix-agent-config-guard.yaml`, link the appropriate variant and set host macros as needed.
Preserve UUIDs and use `deleteMissing: false`. Keep the source file and live templates at the same version.

The published templates belong to `Templates/Modules`. Template vendor metadata identifies
`itforprof.com`; the `vendor: zabbix` tag identifies the monitored product. Items and triggers retain
`component: agent` and `scope: configuration`; `check` distinguishes collection, ServerActive and heartbeat.
Trigger links open this runbook, and operational data shows the configured path or extracted setting.

When using the local MCP wrapper, compare before import. Import template metadata and items separately
from trigger updates if the wrapper cannot handle the trigger sections. The comparison may omit
trigger changes: re-read and verify each trigger expression and dependency explicitly after deployment.
Do not replace an import with deleting and recreating templates or items.

Allow one server configuration-cache cycle (normally 60s) after changes. Agents and proxies may take
longer to receive active-check configuration; the 1h file interval remains unchanged.

## Validation

Run `python3 test/preprocessing_test.py` with Python 3, PyYAML and Node.js. It reads the JavaScript from
both exported variants and checks parsing, explicit failures, collection coverage and dependency wiring.

Agent configuration tests used official agent 2 packages 7.0.0 and 7.0.32 and classic agent 7.0.32 on
Ubuntu. Native Windows and pfSense execution is separate from those checks. Server-side validation
uses isolated trapper-fed copies of the real preprocessing and trigger expressions, with a shortened
missing-data window; it does not stop agents on production hosts.

See the [1.2.0 validation report](docs/validation/2026-10-25-release-1.2.0.md) for measured timings
and deployment verification.

## License

MIT — see [LICENSE](LICENSE).

Author: Konstantin Tyutyunnik / Константин Тютюнник · <https://itforprof.com>
Repository: <https://github.com/IT-for-Prof/zabbix-agent-config-guard>
