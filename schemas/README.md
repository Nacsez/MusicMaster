# Schemas

`job-config-v1.schema.json` is the editor- and automation-facing contract for
configuration files. The Python dataclasses remain the runtime authority and
perform additional cross-field checks that JSON Schema cannot express cleanly.

The schema currently enforces:

- at least one output and one reference;
- at most 32 requested references;
- independently positive total level and frequency contribution;
- exactly one reference for the default/explicit upstream engine;
- upstream weights fixed at `1.0`; and
- native structural support for one through 32 independently weighted
  references.

When `execution` or `execution.engine` is omitted, the default upstream
constraint applies.

Advanced properties such as partial match amount, EBU R128, true peak, dither,
metadata copy, and an external limiter remain representable so future-control
specimens can be loaded and rejected with actionable capability diagnostics.
Schema presence does not mean an engine implements an option.

Validate in layers:

```powershell
.\.venv\Scripts\python.exe -m json.tool `
  .\schemas\job-config-v1.schema.json > $null
mmt show-config .\configs\weighted-references.json
mmt validate .\configs\weighted-references.json --no-input-check --no-audio-probe
```

The first command checks JSON syntax only. Editors/automation may apply the
declared JSON Schema Draft 2020-12 contract. `mmt show-config` exercises strict
runtime JSON/dataclass parsing and path resolution. `mmt validate` additionally
checks engine capabilities and cross-field DSP rules; omit the two skip flags
for a real job so files are decoded and inspected.
