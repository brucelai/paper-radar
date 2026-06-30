# DESIGN-PRPM — Personal Research Preference Model (v2)

The canonical spec for the paper-radar recommendation engine rewrite. The math and
the reasons behind each constant live here; the code intentionally stays thin. Read
this file before touching any ranking/training code.

## 0. Framing

paper-radar is not a sorter. It is a **Personal Research Preference Model (PRPM)**;
the daily paper ranking is merely its most visible output. Every Like / Neutral /
Dislike / 🔬 / 📚 / 📎 / seen event should sharpen the model's answers to:

1. What does the user like? (topic AND methodology AND setting)
2. What does the user dislike?
3. Which features actually drive decisions?
4. Which preferences are drifting over time?

v1 failed all four beyond coarse topic tags: one feature axis (hand-written
keywords), vote-only learning, manual 3-step training loop that never fired,
deterministic score sort → winner-takes-all feedback loop.

## 1. Architecture (layers)

```
Event store   D1 `actions` (current state) + D1 `action_log` (append-only history)
Feature store SQLite papers.{tags, facets, embedding}  (host = canonical)
Model         model_state.json — decayed Beta pseudo-counts per feature + profile embedding
Serving       rank.py — Thompson-sampled score, MMR diversity, explore slots, why-breakdown
Introspection site/profile.json + profile.html dashboard
```

Nightly host cron (`run.sh`):
```
fetch_and_score → enrich → enrich --recheck
→ sync_actions.py      (D1 → actions_cache.json; tolerant of missing action_log)
→ extract_facets.py    (LLM facets for exported papers lacking them; skip if no key)
→ embed_papers.py      (fastembed bge-small-en-v1.5 → papers.embedding BLOB)
→ train_model.py       (STATELESS: recompute posteriors from full history each night)
→ rank.py              (writes papers.json + site/profile.json + rank_log)
→ wrangler pages deploy
```
Every new stage is `|| true`-guarded: any failure degrades to the previous day's
behavior (fetch_and_score's own papers.json export is the fallback ranking).

**Statelessness is a design invariant.** train_model.py reads ALL events from D1
every night and recomputes counts from scratch (decay applied by event age).
No incremental state → idempotent, no drift, restorable from D1 + db alone.
(Same philosophy as v1 train_interest.py's base_weight design.)

## 2. Signals

Per item (from D1 `actions` current state; `action_log` adds context only):

| event                                   | s      |
|-----------------------------------------|--------|
| engaged: deepread OR content OR pdf_key | +2.0   |
| vote up                                 | +1.0 (additive) |
| vote neutral                            | −0.3   |
| vote down                               | −1.5   |
| seen-only (seen, nothing else)          | −0.1   |
| impression-only (see below)             | −0.05  |

s clipped to [−2, +3]. Engagement (paying time) outranks stated preference (a tap).
Neutral is defined as **weak negative** ("saw it, opened it, didn't care").

**Impression-only**: item appeared in rank_log at rank ≤ 20 on ≥ 3 *active days*
(days on which the user performed any action — proxy for "user actually visited")
and has no actions row at all. Deliberately tiny; documents its own weakness.

**Time decay**: every contribution × 0.5^(age_days/90) (half-life 90 d, age from
`actions.updated`). This is what lets preferences drift (question 4).

## 3. Features per paper

| class    | example                  | prior p0                    | n0 | scale S |
|----------|--------------------------|-----------------------------|----|---------|
| kw:      | kw:pain intervention     | 0.5 + 0.07·w  (w=hand weight)| 8  | 4       |
| neg:     | neg:basic science        | 0.5 + 0.07·w  (w negative)  | 8  | 4       |
| design:  | design:RCT               | 0.57                        | 6  | 2       |
| author:  | author:Chang, Ke-Vin     | 0.57                        | 6  | 1.5     |
| facet:   | facet:method:bayesian    | 0.5                         | 6  | 2.5     |
| src:     | src:rapm                 | 0.5                         | 10 | 1       |
| grp:     | grp:pain                 | 0.5                         | 10 | 0.5     |
| (embed)  | cos(profile, paper)      | —                           | —  | 3       |

- kw/neg/design/author come from interest_model.json exactly as v1 matched them
  (stored in papers.tags). interest_model.json = **feature definitions + priors**;
  it is no longer the score.
- p0 clamped to [0.2, 0.9]. α0 = p0·n0, β0 = (1−p0)·n0.
- src/grp get heavy shrinkage (n0=10, S small): journal-level preference should
  move slowly and never dominate content features.

### Facets (the answer to "tags are too few")

LLM-extracted once per exported paper, cached in papers.facets (JSON). Enum
whitelists — anything outside is dropped (guards hallucination):

- design: rct | systematic-review | meta-analysis | cohort | case-control |
  cross-sectional | case-series | case-report | narrative-review | guideline |
  qualitative | pilot | protocol | preclinical | other
- setting: icu | inpatient-rehab | outpatient | community | sports | telehealth | lab | other
- population: stroke | sci | tbi | pediatric | geriatric | msk | cancer | icu | healthy | mixed | other
- methods[]: bayesian | survival-analysis | machine-learning | deep-learning |
  explainable-ai | multimodal | nlp | imaging | emg | ultrasound | biomechanics |
  psychometrics | economic
- sample_size: small | medium | large | na   (<50 / 50–200 / >200)

Provider: Groq `openai/gpt-oss-20b` (a strong free JSON extractor in testing).
GROQ_API_KEY read from the project `.env`. No key → stage skips silently.
Abstracts are public literature — sending them to a cloud LLM is acceptable.

## 4. Model math

Per feature f: pos_f = Σ max(0,s_i)·decay_i, neg_f = Σ max(0,−s_i)·decay_i over
papers carrying f. Posterior θ_f ~ Beta(α0+pos_f, β0+neg_f).

- expected contribution  c_f = S_f · 2·(μ_f − 0.5),  μ = α/(α+β)
- **expected score** = Σ c_f + 3·cos(profile, emb)   → displayed, category-stable
- **sampled score**  = same with θ_f drawn once per feature per day
  (rng seeded `f"{day}:{feature}"`) → Thompson sampling = exploration is *inherent*:
  uncertain features get their day at the top; confident ones stabilize.

Profile embedding: v = normalize( Σ s_i·decay_i·emb_i ) over papers with |s| ≥ 0.3.
Captures the semantic long tail that no tag/facet encodes.

Category (recommend/candidate/skipped → export filter) still uses the v1 keyword
score from fetch_and_score — unchanged semantics, deliberate: the learned model
ranks, the hand prior gates what enters the pool at all.

## 5. Serving (rank.py)

1. Order exported papers by sampled score.
2. **MMR** on top 60: greedy pick argmax [0.75·score_norm − 0.25·max cos to
   already-picked] — breaks same-topic streaks.
3. **Explore slots**: positions 6, 12, 18, 24, 30, 36 (≈17%) filled from explore
   pool, priority: (a) cold papers — every feature has support < 3, (b) adjacent
   zone — cos(profile) ∈ [0.15, 0.5], (c) one low-scorer serendipity pick.
   Never-seen only. Marked `explore:true` + reason in `why` → frontend 🧭 badge,
   and the vote context records exploration=true so explore feedback is
   distinguishable from exploit feedback (the Spotify/YouTube trick).
4. rank_log (host SQLite): (day, item_id, rank, explore, sampled, expected)
   for top 60 — the impression record.
5. papers.json: array in final order; per paper adds `rank`, `explore`,
   `why` = top-6 [label, contribution] pairs, `score` = expected (1 dp),
   `kw_score` = v1 score.
6. site/profile.json for the dashboard: signal totals, top features, rising /
   falling (recent-30d mean vs overall), explore hit-rate.

## 6. Worker / D1

- `action_log` (append-only): id PK AUTOINCREMENT, item_id, kind, val, ctx(JSON:
  {explore, rank, badge}), ts. Worker dual-writes on /api/action and /api/upload,
  wrapped in try/catch → missing table (pre-migration) must never 500.
- Migration runs `schema.sql` (adds the `action_log` table) from a machine holding
  a D1:Edit token, e.g. `wrangler d1 execute paper-radar-db --file=schema.sql`.
  The nightly cron only needs a D1:Read token.
- `actions` stays the current-state table the sync step and the frontend consume.

## 7. Frontend

- Default sort = server order (`rank`); stored filter value 'score' migrates to 'rec'.
- 🧭 探索 badge on explore cards.
- Score chip click → why-breakdown popover (trust + lets the user debug his own model).
- persist() sends ctx {explore, rank}.
- papers.json fetched without cache-buster (ETag/304) — was a full reload every visit.
- profile.html = preference dashboard (reads profile.json, static).

## 8. Constants recap (tune here, nowhere else)

HALF_LIFE_DAYS=90 · CLIP=[−2,3] · TS_DAMP=0.5 (Thompson variance damping —
found necessary in first smoke test: undamped sampling let a 0.2-expected paper
take rank #1; raise toward 1.0 as vote volume grows) · MMR_λ=0.75 · MMR_POOL=60 ·
EXPLORE_POSITIONS=[6,12,18,24,30,36] · COLD_SUPPORT<3 · ADJ_ZONE=[0.15,0.5] ·
EMB_SCALE=3 · scales/priors table §3 · facet enums §3 · FACETS_PER_RUN=80

## 9. Known caveats / future work

- Impression-only signal is a weak proxy (no real page-view telemetry by design —
  site is static; we accept this).
- v1 train_interest.py is DEPRECATED for ranking (kept as a local manual inspector;
  do not run --apply anymore — rank.py ignores mutated weights except as priors, so
  harm is limited but confusing).
- Facet backfill is capped per run (80) → full coverage takes ~6 nights; fine.
- If fastembed install fails on an ARM host: fallback is CF Workers AI
  `@cf/baai/bge-small-en-v1.5` via REST (needs Workers AI token perm) — not built,
  documented intent only.
- The downstream sync tool still joins its own stale local paper_radar.db copy
  (TODO from v1 stands): consider copying the host db or reading papers.json.
