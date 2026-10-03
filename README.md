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
