# Canon — Template Discovery

A single-script tool that reads one or more Canon sitemaps, groups URLs by path structure, and produces a tabbed HTML report estimating content migration effort per URL group.

No page crawling — all analysis is done from the sitemap URL list only.

---

## How it works

```
sites.csv
    │
    ▼
analyze.py  ──►  groups.html
(fetch sitemaps,    (tabbed HTML report,
 group by path)      one tab per site)
```

`analyze.py` fetches each sitemap through a stealth browser (to bypass WAF/bot detection), parses all page URLs, strips any configured path prefix or locale segment, then groups URLs by their first meaningful path segment. Groups with fewer than 5 pages are collapsed into a single `/other/` row. The output is a single self-contained HTML file with one tab per site.

---

## Setup

Requires Python 3.11+ and a system installation of Google Chrome.

```bash
pip install -r requirements.txt
playwright install chromium
```

**Dependencies** (`requirements.txt`):

| Package | Purpose |
|---|---|
| `playwright` | Headless browser (system Chrome) used to fetch sitemaps behind WAFs |
| `playwright-stealth` | Patches browser fingerprints to avoid bot detection |
| `beautifulsoup4` | XML/HTML parsing for sitemap content |
| `lxml` | Fast XML parser backend for BeautifulSoup |

---

## Configuration — `sites.csv`

Sites are defined in `sites.csv`. Each row is one site to analyze.

```
name,sitemap_url,root_path,locale
CUSA,https://www.usa.canon.com/sitemap.xml,,
CSAI,https://www.csai.canon.com/sitemap.xml,,
CVI,https://www.cvi.canon.com/sitemap.xml,/content/canon/cvi/cvi-homepage,
CCI,https://shop.canon.ca/sitemap.xml,,en_ca
```

| Column | Description |
|---|---|
| `name` | Short label shown as the tab name in the report |
| `sitemap_url` | Full URL to the sitemap or sitemap index XML |
| `root_path` | Optional AEM path prefix to strip before grouping (e.g. CVI's deep content path) |
| `locale` | Optional locale segment to filter to and strip (e.g. `en_ca` keeps only English URLs and removes the locale prefix before grouping) |

When `locale` is set, only URLs whose path starts with `/<locale>/` are included, and that segment is removed before grouping. This means `fr_ca` URLs are excluded entirely.

When `root_path` is set, that prefix is stripped from every URL path before the first path segment is extracted for grouping.

---

## Usage

```bash
# Run all sites defined in sites.csv
python3 analyze.py

# Run a single site by name
python3 analyze.py --site CUSA

# Use a different input file
python3 analyze.py --sites-file my-sites.csv

# Specify output file and sort order
python3 analyze.py --out results.html --sort pages
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--sites-file` | `sites.csv` | Path to the site configuration CSV |
| `--site` | *(all sites)* | Run only this site (matches the `name` column) |
| `--out` | `groups.html` | Output HTML filename |
| `--sort` | `alpha` | Sort groups: `alpha` (alphabetical) or `pages` (descending page count) |

---

## Output — `groups.html`

A single self-contained HTML file. Open in any browser — no server needed. Each site gets its own tab.

### Report columns

| Column | Description |
|---|---|
| **URL Group** | First path segment after stripping root/locale prefix. Clicking opens that path on the live site. Sub-paths (L2/L3 examples) are shown as indented rows. |
| **Pages** | Total number of URLs in the sitemap under this group |
| **Max Depth** | Deepest path nesting found in the group |
| **L2 Sub-paths** | Number of distinct second-level path segments |
| **L3 Sub-paths** | Number of distinct third-level path segments |
| **Effort** | Migration effort tier based on page count (see below) |

### Effort tiers

| Label | Page count |
|---|---|
| LOW | ≤ 15 pages |
| MED | ≤ 75 pages |
| HIGH | ≤ 500 pages |
| VERY HIGH | > 500 pages |

### `/other/` group

URL groups with fewer than 5 pages are not shown individually. They are collapsed into a single `/other/` row, which is always pinned to the bottom of each tab regardless of sort order.

---

## Bot detection

Canon's sites use Akamai Bot Manager, which blocks standard headless browsers. Two measures are used:

1. **System Chrome** (`channel="chrome"`) — uses the user's installed Google Chrome rather than Playwright's bundled Chromium. Chrome has different fingerprints that Akamai does not flag.
2. **`playwright-stealth`** — patches JavaScript properties that headless browsers expose (e.g. `navigator.webdriver`, WebGL vendor strings) to match a real browser profile.

This only affects the sitemap fetch. No individual content pages are visited.
