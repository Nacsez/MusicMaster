# Deterministic audio fixtures

Binary fixtures are generated locally and are not committed. This keeps the
repository reviewable while making every edge-case input reproducible.

Generate and then verify them with:

```powershell
python .\scripts\generate_wav_fixtures.py
python .\scripts\generate_wav_fixtures.py --check
```

The PowerShell smoke wrapper writes its copy under
`artifacts/smoke/fixtures/`:

```powershell
.\scripts\Invoke-SmokeTest.ps1 -FixturesOnly
```

The generator uses only the Python standard library and emits canonical
16-bit PCM RIFF/WAVE data plus `manifest.json`. The manifest records channel
count, sample rate, frame count, byte count, purpose, and SHA-256 for each
fixture.

The fixture set covers:

- a nominal six-second stereo target/reference pair with different spectra;
- exact silence and one-LSB near-silence;
- mono input;
- 48 kHz input requiring resampling;
- sparse impulses;
- repeated hard-limiter plateaus;
- full-scale clipped plateaus; and
- input shorter than Matchering's default FFT minimum.

Safety is intentional. Output paths must remain inside this repository.
Matching existing files are reused; differing existing files cause an error
and are never overwritten. If the fixture specification changes, preserve or
move any evidence you need and remove the generated directory manually before
regenerating it.
