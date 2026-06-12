# Content Grouping Rationale

## Background

The SOW references **30 templates** as the scope of migration work. This document explains how we arrived at our groupings, why we are not treating these as templates in the traditional sense, and why the current count does not require a change order or renegotiation.

---

## The Challenge with "Templates" on Canon Sites

Canon's sites do not expose any public identifier for the AEM templates behind their pages. There is no `data-template`, no URL parameter, no naming convention that maps a page to its underlying template definition.

To identify template types, we explored several structural signals from the page source:

- **JSON-LD / Schema.org markup** — e.g. `Product`, `Article`, `FAQPage` types embedded in `<script type="application/ld+json">` blocks
- **CSS component classes** — e.g. `product-grid`, `pdp`, `hero`, `carousel` present in the DOM
- **DOM structure** — e.g. presence of `<article>`, `<aside>`, `<table>`, breadcrumb nav elements

All of these approaches share the same problem: the signals are inconsistent and unreliable across pages. CSS classes bleed from shared navigation and footer components into every page, producing false positives. Schema.org markup is sparse and incomplete on many pages. DOM structure alone is not enough to distinguish meaningfully different content types.

---

## Why URL Patterns Work

The most reliable signal available — without crawling every page — is the **URL structure**.

Canon organizes its content hierarchically. Pages that share a first-level path segment (e.g. `/products/`, `/support/`, `/pro/`) consistently share:

- Similar content purpose and audience
- Similar component composition
- Similar editorial workflow and data model

This is not a coincidence. It reflects how the site was built: content authors and developers organized pages into sections that map to a content type or business function. Siblings under a common parent will, in practice, require the same migration approach.

Grouping by the top-level URL segment gives us a migration unit that is:
- **Verifiable** — grounded in actual URL data from the sitemap, not inferred from page signals
- **Consistent** — every URL in a group was organized there intentionally
- **Actionable** — a single migration script can be written once and applied to all pages in a group

---

## Grouping Methodology

We fetch each site's full sitemap, collect all published URLs, and group them by the first meaningful path segment. Groups with fewer than 5 pages are too small to script independently and are consolidated into a single **Other** group.

**Effort tiers** are assigned based on page count within a group:

| Label | Page count |
|---|---|
| LOW | ≤ 15 |
| MED | ≤ 75 |
| HIGH | ≤ 500 |
| VERY HIGH | > 500 |

---

## Current Counts

From the four primary sites analyzed (CUSA, CSAI, CVI, CCI), the report identifies **35 groups** with 5 or more pages each. These are the groups that will each receive a dedicated migration script.

LATAM and Brazil sites are also in scope but contain a total of 10–20 pages each. At that scale there is no meaningful grouping to do — all pages are treated as a single **Other** group per site, adding approximately **4 additional groups** across both regions. These are straightforward migrations with minimal complexity.

---

## Why We Are Not Requesting Additional Templates

The SOW figure of 30 templates was an estimate made before full sitemap analysis was possible. Now that we have the actual URL data, we have 35 primary groups plus roughly 4 from LATAM/Brazil.

We are not treating this as a scope increase for the following reasons:

**1. "Template" is not a well-defined unit here.**
Because Canon does not expose template identifiers, any grouping we produce is an approximation. Adding or subtracting groups based on a target number would be arbitrary.

**2. The cost per group is roughly equal regardless of page count.**
Each group requires:
- Sampling a subset of representative pages
- Writing a migration script for that content structure
- Running the script against all sibling pages in the group
- Iterating on errors and edge cases as they arise

A group with 10 pages costs approximately the same to script as a group with 200 pages. The variable that drives time is **validation**, not the scripting itself. More pages means more validation passes, but a LOW-effort group still needs a complete script, and a VERY HIGH group is often more uniform than a small one.

**3. Asking for more templates would not reduce scope.**
The 35 groups represent the real shape of the content. Negotiating a lower number would mean merging distinct content types into one group and writing messier, less reliable migration scripts — it would cost more in rework, not less.

**4. A potential Canon objection: HIGH-effort groups cover the 30, LOW-effort groups should cost less.**
A Canon could reasonably argue that the HIGH and VERY HIGH effort groups account for the original 30 templates in the SOW, and that the LOW-effort groups should therefore be priced at a reduced rate since they represent smaller bodies of work. This framing sounds logical but misrepresents where the cost actually lives. Every group — regardless of page count — requires the same foundational work: analyzing a representative sample, building a migration script, handling edge cases, and running validation passes. A LOW-effort group with 8 pages still requires a complete, tested script. The page count affects how many validation cycles are needed, not whether the scripting work happens. Accepting this framing would mean absorbing the cost of 15–20 fully independent migration scripts at a discounted rate, which does not reflect the actual effort involved. The SOW's template count was an estimate; the per-group effort cost is the more accurate unit, and it does not scale down proportionally with page count.

The honest scope is: one migration script per meaningful URL group, validated against the actual page population. That is what the current grouping delivers.
