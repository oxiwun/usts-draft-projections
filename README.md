# uSTS draft projections

Automated NFL Mock Draft Database consensus big-board feed for the uSTS Google Sheet.

- Runs daily in GitHub Actions with Playwright/Chromium.
- Keeps only projected picks 1-256 per draft class.
- Publishes `data/draft_projection.csv`.
- Preserves the last good snapshot if a board fetch fails.

Raw CSV:

`https://raw.githubusercontent.com/oxiwun/usts-draft-projections/main/data/draft_projection.csv`
