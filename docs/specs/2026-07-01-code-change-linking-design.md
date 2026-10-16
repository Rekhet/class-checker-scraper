# Code-Change Linking — Design

**Date:** 2026-07-01
**Status:** Design (approved decisions from brainstorming locked below)
**Depends on:** `2026-07-01-cross-semester-explore-core-design.md` (Spec 1). This spec fills the "이전/이후 과목코드" slot Spec 1 reserved on the code page, and adds `prev`/`next` arrays to each `codes[]` object in `explore-index.json`.

---

## Goal

When a course's **`sbjt_cd` changes over time** (same course, new code — e.g. a curriculum renumbering), link the two per-code pages so a user reading the history of the new code can jump to the old code's page and vice-versa.

The user's request: *"If class code had been changed, then the page should of course provide link to the previous class code page."*

## The fundamental constraint: there is NO source data for this

Verified during exploration:
- `scraper/changelog.py` diffs offerings keyed by `sbjt_cd + lt_no`. A `sbjt_cd` change is therefore recorded as an unrelated **DEL** (old code disappears) + **NEW** (new code appears) — it carries **no "renamed from" pointer.**
- No field anywhere in the payload, DB, or term JSON records a predecessor/successor code.

So predecessor↔successor links must be **manufactured**, not read. Two mechanisms, combined:

## Non-negotiable principle: identity is the CODE, not the name

Different classes can share a name. Therefore **an offering belongs to its `sbjt_cd`, full stop** — Spec 1's per-code pages are the source of truth and never group by name. Everything in this spec is a **non-authoritative cross-reference layer**: it may *suggest* that code A became code B, but it **never** moves offerings between codes, **never** merges code pages, **never** hides a code. Because names collide, a name+dept match is weak evidence on its own — so inference stays conservative, every inferred link is labeled **추정**, and the curated map is the authority. A user always reads a class strictly by its code page; the links are just optional jumps between code pages.

## Locked decision (from brainstorming)

The user asked whether a code map was already configured. It is **not** (confirmed above). Therefore this is its own explicit spec, and it must **build** the mapping via (1) inference + (2) a curated override map, and surface links on the code page.

---

## Mechanism 1 — Inference (automatic candidates)

Group codes that look like the same course under different numbers, using signals already in `explore-index.json`:

**Signal:** two codes A and B are candidate predecessor→successor when
1. they share a **normalized name** (same course title after normalization), **and**
2. they share a **department**, **and**
3. their term ranges are **non-overlapping or barely overlapping** and **adjacent** — A's offerings stop around when B's start (A last-seen term ≤ B first-seen term, within a small gap), **and**
4. (optional strengthening) same credits / same college.

Direction: the code whose offerings are **older** is `prev`; the newer is `next`.

**Normalization** (shared helper, reused from Spec 2's name normalization where possible): trim, collapse whitespace, optionally strip trailing level/roman-numeral markers only if we find that helps — start **conservative** (exact normalized-name match) to avoid false links, loosen only with evidence.

**Confidence:** each inferred link carries a confidence tier (e.g. `high` = same name+dept+credits, clean adjacency; `low` = same name+dept but overlap or gap). **Name+dept alone — with no corroborating credits/adjacency — never exceeds `low`**, because different classes share names. Every inferred link is shown as clearly-labeled **추정 (inferred)**; only the curated map produces unlabeled, authoritative links.

**Why conservative:** a wrong link is low-harm (it's a display-only cross-reference, never hides or blocks anything) but still misleading. So: infer generously enough to catch real renumberings, label everything inferred as 추정, and let the curated map (Mechanism 2) both **add** confirmed links and **suppress** false ones.

## Mechanism 2 — Curated override map (human truth)

A hand-authored working-tree file `scraper/code_links.json`:
```jsonc
{
  "links": [
    { "prev": "M3502.019800", "next": "M3502.020100", "note": "renumbered 2024 curriculum reform" }
  ],
  "suppress": [
    { "a": "M1234.000100", "b": "M1234.000200", "note": "same name, genuinely different courses" }
  ]
}
```
- `links`: confirmed prev→next pairs (confidence `confirmed`, never labeled 추정). Added even when inference missed them.
- `suppress`: pairs that inference proposes but a human rejects — removed from output.

`export_json.py` merges: **inferred candidates − suppressed + confirmed**, then writes `prev`/`next` onto each code.

---

## Data model — extend `explore-index.json`

Add to each `codes[]` object (from Spec 1):
```jsonc
{
  "c": "M3502.020100",
  "names": [ ... ],
  "o": [ ... ],
  "prev": [ { "c": "M3502.019800", "conf": "confirmed" } ],   // older code(s)
  "next": [ { "c": "M3502.021000", "conf": "low" } ]          // newer code(s)
}
```
- Arrays (not scalars): a code can chain (A→B→C) and rarely fan out; arrays handle both. Empty/omitted when none.
- `conf` ∈ `confirmed | high | low` drives the UI label (`confirmed`/`high` → plain link; `low` → link + "추정" badge). (`high`/`low` are inference tiers; `confirmed` comes from the curated map.)

**Build order:** links are computed **after** all codes + offerings are grouped (needs every code's name set + term range), so this is a post-pass at the end of the `explore-index.json` build in `export_json.py`, reading `code_links.json` if present.

---

## UI — fill Spec 1's reserved slot on the code page

On the `#code/<sbjt_cd>` detail page, in the slot Spec 1 left empty:
- **이전 과목코드:** render each `prev[]` as a link to `#code/<c>`, with the code's current name; append a small **추정** badge when `conf === "low"`.
- **이후 과목코드:** same for `next[]`.
- If both empty: render nothing (no empty header).

No router change (Spec 1's `#code/<id>` already handles navigation between linked codes). Clicking a link just re-renders the detail view for the other code — which itself shows *its* prev/next, so a user can walk the whole chain.

---

## Optional — management review (ties to Spec 2's manager page)

If/when the Spec 2 management surface exists, add a **code-link review** panel in the same **dev-only** shell (`index-dev.html` / `partials/dev.html`), talking to `scraper/server.py` via an admin-gated `POST /api/code-link` endpoint (`ADMIN_TOKEN`, like Spec 2's `/api/prof-merge`) that writes `scraper/code_links.json`. The panel lists inferred candidates (esp. `low`) with their evidence (shared name, depts, term ranges) and lets a human **confirm** (→ `code_links.json.links`) or **reject** (→ `code_links.json.suppress`) each. Deploy stays static (consumes only exported `explore-index.json`; no backend ships). This panel is **optional/v2** — Mechanisms 1+2 (with `code_links.json` hand-edited) fully deliver the feature without it.

---

## Edge cases

- **Same normalized name+dept but genuinely different courses** (false positive): shown as 추정 until a human adds a `suppress` entry. Never blocks anything.
- **Chained renumbering (A→B→C):** arrays + per-page walk handle it; each page shows immediate neighbors, user traverses.
- **Overlapping term ranges** (both codes offered same term): weakens confidence to `low` (could be a transition year, could be two courses). Keep as 추정.
- **Cross-department move** (course moved to another dept and renumbered): inference requires same dept, so it will **miss** this — that's what the curated `links` map is for.
- **Name reused for an unrelated new course years later:** large term gap → either `low` or excluded by an adjacency window; curated `suppress` cleans up stragglers.

## CARDINAL-RULE note (safety)

Links are **additive and display-only** — they never hide, merge, or block an offering or a code page. A false link mildly misleads; a missing link just means no cross-reference. Both are strictly safer than dropping data. Label inferred links **추정** so users can judge; keep authoritative links (curated) unlabeled.

---

## Testing

- **Inference sanity:** unit-test the grouping on a small fixture — two codes, same normalized name+dept, adjacent term ranges → one prev→next link, correct direction.
- **Suppression:** add a `suppress` pair; assert it's absent from output. Add a `links` pair inference didn't find; assert it's present as `confirmed`.
- **Direction:** assert the older code is `prev` on the newer code and the newer is `next` on the older (symmetry).
- **JSON validity:** `python3 -m json.tool web/data/explore-index.json`; assert every `prev`/`next` target `c` exists in `codes`.
- **UI:** `preview_eval` navigate to a code known to have a link; assert the 이전/이후 links render and route to the right `#code/...`; assert `low`-confidence links show the 추정 badge and `confirmed` links don't.
- **No-link code:** assert the slot renders nothing (no stray header) for a code with no prev/next.

Verification per standing directive: `preview_eval` + `python3 -m json.tool` (no screenshots).

---

## Open questions to resolve in the plan

1. **Adjacency window:** how large a term gap still counts as a renumbering vs. an unrelated reuse? Start strict (immediate/next-year), widen only if real cases are missed.
2. **Normalization aggressiveness:** exact normalized-name only, or strip level suffixes? Start exact; measure false negatives before loosening.
3. **Does inference reuse Spec 2's normalization helper**, or its own? Prefer one shared helper to avoid drift (flagged so the plan sequences Spec 2's helper first if both land together).
