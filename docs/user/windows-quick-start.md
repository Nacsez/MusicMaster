# Windows quick start

Music Mastering Tools runs on **Windows 10/11 Intel/AMD x64**. The executable
includes Python and the mastering runtime. You need a browser and your own
audio files; no Python installation or administrator rights are required.
The supplied bundle also includes these essentials in `START-HERE.txt` beside
the executable.

## Start and master

1. Extract the supplied Windows ZIP and open its `windows` folder. Double-click
   `MusicMasteringTools.exe`; the workbench opens in your default browser.
2. In **Library**, select **Add audio…** and choose your mixes and reference
   tracks. Audio stays at the selected locations.
3. In **Master**, choose the mixes and references, then choose a delivery
   folder. **Remember** keeps that destination for future launches.
4. Use **Check setup**, **Test run**, then start the final render. Each mix gets
   a new run-named output folder. A single reference uses the compatibility
   engine; several references use the experimental weighted profile engine.
5. Open **Review masters** to audition originals against completed versions,
   select preferred versions, or export a batch.

The browser's codec support determines which tracks can be auditioned. WAV
mastering is covered by the executable smoke test; some other input formats
need an independently installed FFmpeg decoder.

## Exit and return

Close the **last workbench tab** to exit. The desktop application allows a short
grace period for refresh/reconnection, and finishes any active mastering
operation before shutting down. Refreshing a tab, minimizing the browser, or
closing one of several workbench tabs preserves the session.

Your library and remembered folders persist under:

```text
%LOCALAPPDATA%\MusicMasteringTools\workspace
```

Moving or replacing the EXE preserves this library. Another Windows account
gets its own library. Double-clicking again reopens an existing session when
one is still running. **Shut down portal** in the application menu provides
immediate shutdown when idle.

For a persistent manual session, launch with `--keep-running`.
`--no-browser` also keeps the portal running until explicit shutdown.

## Find files or recover a library

Folder pickers reuse the selected location and successful previous choices.
**Show in folder** selects the exact file in Explorer; a directory action opens
its contents. If audio has moved, verify/relink it in Library. The catalog
stores references to files, not copies of the audio.

An empty first EXE library is normal. Earlier source-workbench tracks remain in
the source checkout's `private-workspace/`, which is separate from the EXE's
per-user default. Follow
[continue an existing checkout library](../deployment/windows-release.md#continue-an-existing-checkout-library)
to open that workspace without deleting or republishing its contents.

For startup failures, use **Activity → System → Session Logs** when available,
or inspect the workspace's `logs/` folder. Keep these files private; they can
contain local paths and track information.

Source and build instructions: [Nacsez/MusicMaster](https://github.com/Nacsez/MusicMaster)
and the [Windows release guide](../deployment/windows-release.md). The current
candidate is unsigned; independent clean-PC acceptance and collecting all
dependency sources for a public binary release remain separate release work.
