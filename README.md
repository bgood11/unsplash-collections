# unsplash-collections

Organises the photos on unsplash.com/@bgoodpic into public collections.

- `plan.json` lists each collection and the photo IDs that go in it, built locally from the
  photo descriptions (themes) and from the source files on the Photos SSD (film stocks).
- `runner.py` does one batch: it creates any missing collections and adds up to 45 photos, then
  progress is committed to `state.json`. The Unsplash demo tier allows 50 requests per hour.
- The workflow is a self-sustaining chain: each run does up to 6 batches about 61 minutes apart,
  then starts the next run itself. A six-hourly schedule restarts the chain if it ever breaks.
  The full plan takes about 2.5 days.
- Re-running is safe: completed steps are recorded and never repeated.

## Controls

- Start: delete `PAUSED`.
- Pause: create a file called `PAUSED` (the running job stops before its next batch).
- Run now: Actions tab, "Add photos to collections", "Run workflow".
- Finished: the job writes `DONE` and stops itself.

The Unsplash OAuth token is stored as the repository secret `UNSPLASH_TOKEN`.

## Daily sweep of new uploads

`sweep.py` runs at the start of every batch. If you have uploaded photos since the last sweep it
reads each one's description and EXIF, asks Claude Haiku which collections it belongs in, and appends
the placements to `plan.json` for `runner.py` to add. When nothing is new it costs one API call.

- Theme: chosen from the 19 theme collections using the rules in `data/themes.json`.
- Film stock: from ISO and camera (250 Vision3 250D, 400 Ultramax, 500/640 Vision3 500T, 200 Gold 200,
  or Fujicolor 200 when shot on the Olympus mju). ISO 200 is a guess and is flagged in `SWEEP_LOG.md`.
- A photo that fits no theme waits in `unsorted.json`. Once 8 or more share a clear theme, the job
  creates a new public collection for them (and adds its rule to `data/themes.json`).
- Needs the repository secret `ANTHROPIC_API_KEY`. Without it the sweep is skipped.
- Every sweep appends what it filed where to `SWEEP_LOG.md`.
- Existing photos are not re-sorted when a new collection is created; only new uploads.
