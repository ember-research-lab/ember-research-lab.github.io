# ember-research-lab.github.io

Generated front door for Ember Research Lab: Home, Work, About. Source: `content/`, `templates/`, `static/`.
Output: `docs/` (Pages must serve from the `/docs` folder of `main`).

    uv run build.py

Gates (fail the build): page staleness (90 days), link check, private-repo names, personal-identifier grep.
The identifier blocklist is read from `~/.config/ember-site/blocklist.txt` (or `$EMBER_SITE_BLOCKLIST`) and is
deliberately not in this repository.
