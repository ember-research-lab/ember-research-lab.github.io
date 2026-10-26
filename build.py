#!/usr/bin/env python3
"""build.py - generate the Ember Research Lab site into docs/ and run the gates.

Usage: uv run build.py [--out docs] [--skip-external-links]
Gates (any failure exits non-zero):
  1. claims ledger: every row's source quote exists and its tier matches the source
  2. staleness: every page reviewed within max_review_age_days
  3. link check: internal links/anchors resolve; external links answer 2xx/3xx
  4. private-repo-name check: no private org repo name appears in the output
  5. personal-identifier grep: the blocklist lives OUTSIDE the repo
     ($EMBER_SITE_BLOCKLIST or ~/.config/ember-site/blocklist.txt); missing file = failure.
     Matches are reported by file and blocklist line number only, never echoed.
"""
from __future__ import annotations

import argparse, datetime as dt, html.parser, json, os, re, shutil, subprocess, sys
import urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jinja2, yaml

ROOT = Path(__file__).resolve().parent
LEAN_API = "https://api.github.com/repos/ember-research-lab/{repo}"
ALLOWED_TIERS = {"T0", "T1", "T2", "T3", "CLOSED", "CONDITIONAL", "OPEN", "STOPPED", "DEGENERATE", "NO"}
TAG_RE = re.compile(r"Tier[- ]([0-3])|\bT([0-3])\b|\b(CLOSED|CONDITIONAL|OPEN|STOPPED|DEGENERATE|NO)\b")
GENERIC_NAMES = {"workspace", "marketplace"}
errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def http_get(url: str, accept: str | None = None) -> str:
    h = {"User-Agent": "ember-site-build"}
    if accept:
        h["Accept"] = accept
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok and "github" in url:
        h["Authorization"] = f"Bearer {tok}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return r.read().decode("utf-8")


def fetch_lean(site: dict) -> dict:
    """Pull counts, monograph version and source revision from the public Lean README."""
    repo = site["lean_repo"]
    readme = http_get(f"https://raw.githubusercontent.com/ember-research-lab/{repo}/main/README.md")
    m = re.search(r"\*\*(\d+) Lean files \| (\d+) top-level modules \| (\d+) sorry sites \| (\d+) axiom declarations\*\*", readme)
    v = re.search(r"`papers/spectral_physics/spectral-physics-latest\.tex`, v(\d+(?:\.\d+)*)", readme)
    b = re.search(r"at last full pass, (\d{4}-\d\d-\d\d)\)", readme)
    if not (m and v and b):
        raise SystemExit("FATAL: Lean README format changed; cannot extract counts/version/date")
    commits = json.loads(http_get(LEAN_API.format(repo=repo) + "/commits?path=README.md&per_page=1"))
    sha, date = commits[0]["sha"][:9], commits[0]["commit"]["committer"]["date"][:10]
    return dict(text=readme, files=m[1], modules=m[2], sorry=m[3], axioms=m[4], version="v" + v[1],
                build_date=b[1], rev=sha, rev_date=date,
                url=f"https://github.com/ember-research-lab/{repo}/blob/main/README.md")


def tags_in(line: str) -> set[str]:
    out = set()
    for a, b, c in TAG_RE.findall(line):
        out.add(f"T{a}" if a else f"T{b}" if b else c)
    return out


def check_claims(claims: list[dict], lean: dict) -> None:
    rigor_path = Path(os.environ.get("RIGOROUS_WORKFLOW", Path.home() / "ember-research-lab/RIGOROUS_WORKFLOW.md"))
    rigor = rigor_path.read_text() if rigor_path.exists() else None
    seen = set()
    for c in claims:
        cid = c.get("id", "?")
        if cid in seen:
            fail(f"claims: duplicate id {cid}")
        seen.add(cid)
        src = c.get("source") or {}
        if not src.get("quote") or src.get("ref") not in ("lean_readme", "rigor"):
            fail(f"claims[{cid}]: no valid source (no source -> no row)")
            continue
        if c.get("tier") not in ALLOWED_TIERS:
            fail(f"claims[{cid}]: tier {c.get('tier')!r} not allowed")
            continue
        if src["ref"] == "rigor" and rigor is None:
            print(f"WARN claims[{cid}]: RIGOROUS_WORKFLOW.md not present here; row NOT re-verified", file=sys.stderr)
            continue
        text = lean["text"] if src["ref"] == "lean_readme" else rigor
        pos = text.find(src["quote"])
        if pos < 0:
            fail(f"claims[{cid}]: quote not found in {src['ref']}")
            continue
        ls = text.rfind("\n", 0, pos) + 1
        le = text.find("\n", pos)
        line = text[ls:le if le >= 0 else len(text)]
        if "tag_quote" in src:
            if src["tag_quote"] not in line:
                fail(f"claims[{cid}]: tag_quote not in the quoted line")
            elif c["tier"] not in tags_in(src["tag_quote"]):
                fail(f"claims[{cid}]: tier {c['tier']} not in tag_quote")
            continue
        found = tags_in(line)
        if not found:  # fall back to nearest Tier heading above
            for h in reversed(re.findall(r"^#+ .*Tier (\d).*$", text[:pos], re.M)):
                found = {f"T{h}"}
                break
        if c["tier"] not in found:
            fail(f"claims[{cid}]: tier {c['tier']} not supported by source line (found {sorted(found)})")


def render(site: dict, claims: list[dict], lean: dict, out: Path) -> None:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "templates"), autoescape=True,
                             undefined=jinja2.StrictUndefined)
    built = dt.date.today().isoformat()
    descs = {"": "Independent research on spectral physics, with a tiered claims ledger.",
             "research": "Spectral physics: monograph, Lean formalisation counts, tiers, claims ledger.",
             "alignment": "A conjectural proposal on alignment; not yet operationalised.",
             "software": "Public software repositories of the lab.",
             "concepts": "Glossary of the lab's terms with status.",
             "about": "The lab, contact, how to cite, changelog."}
    jsonld = json.dumps({"@context": "https://schema.org", "@type": "ResearchOrganization",
                         "name": site["org"], "url": site["base_url"]})
    for p in site["pages"]:
        page = {**p, "reviewed": str(p["reviewed"])}
        html_out = env.get_template(p["file"]).render(
            site=site, page=page, pages=site["pages"], claims=claims, lean=lean, built=built,
            description=descs[p["slug"]], jsonld=jsonld, claim_tier={c["id"]: c["tier"] for c in claims})
        d = out / p["slug"] if p["slug"] else out
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(html_out)
    shutil.copytree(ROOT / "static", out, dirs_exist_ok=True)
    (out / "style.css").write_text((ROOT / "static/style.css").read_text())
    urls = "".join(f"<url><loc>{site['base_url']}/{p['slug']}{'/' if p['slug'] else ''}</loc><lastmod>{p['reviewed']}</lastmod></url>"
                   for p in site["pages"])
    (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>'
                                     f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
    (out / ".nojekyll").write_text("")


def check_staleness(site: dict) -> None:
    today = dt.date.today()
    for p in site["pages"]:
        age = (today - p["reviewed"]).days
        if age > site["max_review_age_days"]:
            fail(f"stale: page '{p['slug'] or 'home'}' last reviewed {age} days ago")


def text_of(path: Path) -> str:
    if path.suffix == ".pdf":
        r = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"cannot extract text from {path.name} (pdftotext missing?)")
            return ""
        return r.stdout + path.read_bytes().decode("latin-1")
    return path.read_text(errors="replace")


def scannable(out: Path):
    return [f for f in sorted(out.rglob("*")) if f.is_file() and f.suffix in {".html", ".tex", ".pdf", ".xml", ".txt", ".css"}]


def check_private_repos(out: Path) -> None:
    r = subprocess.run(["gh", "repo", "list", "ember-research-lab", "--limit", "300", "--json", "name,isPrivate"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail("private-repo check: `gh repo list` failed (is gh authenticated?)")
        return
    repos = json.loads(r.stdout)
    private = {x["name"] for x in repos if x["isPrivate"]}
    public = {x["name"] for x in repos if not x["isPrivate"]}
    if not private:
        fail("private-repo check is vacuous: gh token sees zero private repos (needs org read access)")
    extra = Path(os.environ.get("EMBER_SITE_PRIVATE_REPOS", Path.home() / ".config/ember-site/private-repos.txt"))
    if extra.exists():
        private |= {l.strip() for l in extra.read_text().splitlines() if l.strip() and not l.startswith("#")}
    private -= public
    for f in scannable(out):
        t = text_of(f).lower()
        for n in sorted(private):
            pat = re.escape(n.lower())
            if n in GENERIC_NAMES:  # common English words: flag only as an org repo reference
                pat = r"ember-research-lab/" + pat
            if re.search(r"(?<![\w-])" + pat + r"(?![\w-])", t):
                fail(f"private repo name '{n}' appears in {f.relative_to(out)}")


def check_blocklist(out: Path) -> None:
    bl = Path(os.environ.get("EMBER_SITE_BLOCKLIST", Path.home() / ".config/ember-site/blocklist.txt"))
    if not bl.exists():
        fail("personal-identifier check: blocklist file missing (kept outside the repo)")
        return
    terms = [(i, l.strip().lower()) for i, l in enumerate(bl.read_text().splitlines(), 1)
             if l.strip() and not l.startswith("#")]
    if not terms:
        fail("personal-identifier check: blocklist is empty")
        return
    hits = 0
    for f in scannable(out):
        t = text_of(f).lower()
        for i, term in terms:
            if term in t:
                hits += 1
                fail(f"personal identifier: blocklist line {i} matches in {f.relative_to(out)}")
    print(f"personal-identifier grep: {hits} matches over {len(scannable(out))} files, {len(terms)} terms")


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and a.get("rel") == "canonical":
            return  # self-referential; resolves only after deploy
        if a.get("id"):
            self.ids.add(a["id"])
        for k in ("href", "src"):
            if a.get(k):
                self.links.append(a[k])


def check_links(out: Path, external: bool) -> None:
    pages, ext = {}, set()
    for f in out.rglob("*.html"):
        p = Links()
        p.feed(f.read_text())
        pages[f] = p
    for f, p in pages.items():
        for l in p.links:
            if l.startswith(("http://", "https://")):
                ext.add(l.split("#")[0])
            elif l.startswith(("mailto:", "data:")):
                continue
            else:
                path, _, frag = l.partition("#")
                tgt = (out / path.lstrip("/")) if path.startswith("/") else (f.parent / path) if path else f
                if tgt.is_dir():
                    tgt = tgt / "index.html"
                if not tgt.exists():
                    fail(f"broken internal link {l} in {f.relative_to(out)}")
                elif frag and tgt in pages and frag not in pages[tgt].ids:
                    fail(f"missing anchor {l} in {f.relative_to(out)}")
    if not external:
        print("WARN: external links NOT checked (--skip-external-links)", file=sys.stderr)
        return

    def probe(u: str):
        for m in ("HEAD", "GET"):
            try:
                req = urllib.request.Request(u, method=m, headers={"User-Agent": "ember-site-linkcheck"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return u, r.status
            except urllib.error.HTTPError as e:
                last = e.code
            except Exception as e:  # noqa: BLE001
                last = repr(e)
        return u, last

    with ThreadPoolExecutor(8) as ex:
        for u, st in ex.map(probe, sorted(ext)):
            if not (isinstance(st, int) and st < 400):
                fail(f"broken external link {u}: {st}")
    print(f"link check: {len(ext)} external urls, {len(pages)} pages")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs")
    ap.add_argument("--skip-external-links", action="store_true")
    a = ap.parse_args()
    out = (ROOT / a.out).resolve()
    site = yaml.safe_load((ROOT / "content/site.yaml").read_text())
    claims = yaml.safe_load((ROOT / "content/claims.yaml").read_text())
    lean = fetch_lean(site)
    check_claims(claims, lean)
    check_staleness(site)
    if out.exists():
        shutil.rmtree(out)
    render(site, claims, lean, out)
    check_links(out, not a.skip_external_links)
    check_private_repos(out)
    check_blocklist(out)
    if errors:
        print("\nBUILD FAILED:", file=sys.stderr)
        for e in errors:
            print(" -", e, file=sys.stderr)
        sys.exit(1)
    print(f"OK: built {len(site['pages'])} pages, {len(claims)} ledger rows -> {out}")


if __name__ == "__main__":
    main()
