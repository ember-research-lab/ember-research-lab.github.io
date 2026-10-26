# ember-research-lab.github.io

Generated site for Ember Research Lab. Source: `content/` (site data, claims ledger), `templates/`, `static/`.
Output: `docs/` (Pages must serve from the `/docs` folder of `main`).

    uv run build.py

Gates (fail the build): claims ledger sources/tiers, page staleness (90 days), link check, private-repo names,
personal-identifier grep. The identifier blocklist is read from `~/.config/ember-site/blocklist.txt`
(or `$EMBER_SITE_BLOCKLIST`) and is deliberately not in this repository. Counts and the monograph version
are read from the public Lean repository README at build time.
