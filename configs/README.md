# Configuration profiles

Paths in a job file are resolved relative to that job file, not the current
shell directory.

- `upstream-baseline.json` is the runnable Matchering 2.0.6 compatibility
  profile for exactly one reference with both weights fixed at `1`. It asks for
  limited, normalized/no-limiter, raw-float, and paired preview artifacts.
- `weighted-references.json` is the runnable native two-reference profile. It
  demonstrates independent level/frequency weights, a guarded EQ ceiling, all
  three output branches, and paired previews. Add or remove reference objects
  as needed; the native limit is 32 requested entries.
- `native-target.json` deliberately asks for future controls outside the
  runnable native subset: partial match amount, EBU R128, true peak, metadata
  copy, and dither. It is a capability-rejection specimen, not a starting
  profile for a real render.

Copy a profile before changing it. Put source audio in `audio/` or update the
relative paths, then run:

```powershell
mmt show-config <profile>
mmt validate <profile>
mmt run <profile> --dry-run
mmt run <profile>
```

The runnable templates leave `job_id`, `manifest_path`, and `event_log_path`
null so each CLI invocation owns a fresh audit directory. This permits a dry
run followed by a real run without overwriting evidence. Render output paths
remain explicit and protected; choose new output paths before repeating a real
render.

The workbench chooses the upstream engine for one selected reference and the
native engine for two through 32. For hand-authored JSON, select the engine
explicitly. Both engines reject unsupported controls instead of downgrading
them; `mmt capabilities` is the runtime authority.

Native level and frequency weights are finite, non-negative, and normalized
independently. Each dimension needs at least one positive contribution.
Duplicate content is retained in the manifest and coalesced by SHA-256 for
effective analysis. See the
[workbench/catalog guide](../docs/user/workbench-and-catalog.md#how-several-references-become-one-profile)
for the exact math and edge cases.
