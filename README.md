# unsplash-collections

Organises the photos on unsplash.com/@bgoodpic into public collections.

- `plan.json` lists each collection and the photo IDs that go in it, built locally from the
  photo descriptions (themes) and from the source files on the Photos SSD (film stocks).
- `runner.py` runs hourly in GitHub Actions. Each run creates any missing collections and adds
  up to 45 photos, then commits its progress to `state.json`. The Unsplash demo tier allows
  50 requests per hour, so the full plan takes about 2.5 days.
- Re-running is safe: completed steps are recorded and never repeated.

## Controls

- Start: delete `PAUSED`.
- Pause: create a file called `PAUSED`.
- Run now: Actions tab, "Add photos to collections", "Run workflow".
- Finished: the job writes `DONE` and stops itself.

The Unsplash OAuth token is stored as the repository secret `UNSPLASH_TOKEN`.
