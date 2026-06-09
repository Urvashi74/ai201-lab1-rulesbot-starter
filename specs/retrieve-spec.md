# Spec: `retrieve()`

**File:** `retriever.py`
**Status:** Spec incomplete — fill in all blank fields before implementing

---

## Purpose

Given a user's natural language query, find the most relevant chunks from the vector store using semantic similarity search. Return them ranked by relevance so that `generate_response()` can use them as context.

---

## Input / Output Contract

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `str` | The user's natural language question |
| `n_results` | `int` | Maximum number of chunks to return (default: `N_RESULTS` from `config.py`) |

**Output:** `list[dict]`

Each dict in the returned list must contain exactly these keys:

| Key | Type | Description |
|-----|------|-------------|
| `"text"` | `str` | The chunk text |
| `"game"` | `str` | The game name this chunk came from |
| `"distance"` | `float` | Cosine distance score — lower means more similar to the query |

Results should be ordered from most to least relevant (lowest to highest distance). Returns an empty list `[]` if the collection contains no documents.

---

## Design Decisions

*Complete the fields below before writing any code. Use your AI tool in Plan or Ask mode to help you reason through what belongs here — but the decisions are yours.*

---

### Query approach

*Describe how you will use `_collection.query()` to find relevant chunks. What arguments will you pass, and why?*

_collection.query() will use these arguments:

* query_texts = [query] — a one-element list. Passing the raw text (not a
  vector) lets Chroma embed the query with the SAME model used at ingestion,
  so query and chunks share one vector space and distances are comparable.

* n_results — int, taken from the input param (defaults to N_RESULTS in
  config). Controls how many chunks flow to the generator: higher = more
  context but more noise, lower = tighter but may miss a relevant rule.

* include = ["documents", "metadatas", "distances"] — the minimum needed to
  build each return dict: documents → "text", metadatas → "game",
  distances → "distance". I don't include "embeddings" (not needed).

* I do NOT use a `where` metadata filter. The user doesn't state the game in
  their question, so I let semantic similarity route to the right game rather
  than filtering up front.

---

### Return structure

*Sketch out what one item in your return list looks like as a concrete example. Where does each field come from in the query results?*

```
One item in the returned list (for the query "What happens if I roll a 7 in Catan?"):

{
    "text":     "When a 7 is rolled, no resources are produced. Every player
                 with more than 7 resource cards must discard half...",
    "game":     "Catan",
    "distance": 0.21,
}

Where each field comes from in results = _collection.query(...):
  - "text"     <- results["documents"][0][i]          (the raw chunk text)
  - "game"     <- results["metadatas"][0][i]["game"]  (the metadata dict stored
                                                        in embed_and_store)
  - "distance" <- results["distances"][0][i]          (cosine distance, lower = closer)

The [0] is the single-query index (one query in, one result-set out).
The [i] walks the parallel lists in rank order, so I zip the three lists
together to build one dict per hit, ordered lowest distance first.
```

---

### Handling the nested result structure

*`_collection.query()` returns nested lists. Describe what index you need to access to get the actual list of results for a single query, and why the nesting exists.*

```
query() is built to handle BATCH queries — you can pass many query strings at
once (query_texts=[q1, q2, q3]) and get one result-set back per query. So every
field comes back as a LIST OF LISTS: the outer list has one entry per query,
the inner list holds that query's ranked hits.

results["documents"] looks like:  [ [hit0, hit1, hit2] ]   <- one inner list, b/c one query
                                     ^outer = per-query

I pass exactly one query (query_texts=[query]), so all my results live at the
outer index [0]:

    docs  = results["documents"][0]   # list of chunk texts
    metas = results["metadatas"][0]   # list of {"game": ...} dicts
    dists = results["distances"][0]   # list of distances

The nesting exists so the batch API has a consistent shape whether you send 1
query or 100 — Chroma never special-cases the single-query case. The cost is I
must remember to unwrap with [0] before zipping the three parallel lists.
```

---

### Relevance threshold

*Will you filter out results above a certain distance score, or return all `n_results` regardless of how relevant they are? What are the tradeoffs of each approach?*

```
Decision: return all n_results, NO distance threshold in retrieve().

Why: I keep retrieval simple and dumb — its job is "give me the n closest
chunks." Deciding whether those chunks actually answer the question is the
generator's job (it's grounded to say "that's not in the rules" when the
context doesn't support an answer). Splitting responsibilities this way means
I don't have to hand-tune a magic distance number.

Tradeoffs:

  Threshold (filter out distance > X):
    + cleaner context — junk chunks never reach the generator
    + can short-circuit to "I don't know" when nothing matches
    - the cutoff is a magic number; cosine distances aren't intuitive and
      vary by query, so a wrong X silently drops good hits or keeps bad ones
    - risk of returning [] even when a usable answer exists -> bot looks broken

  No threshold (return all n_results):
    + simple, predictable, nothing to tune
    + generator always has something to reason over and stays in charge of
      the "is this relevant?" call
    - a few weak/off-topic chunks may ride along in the context
    - relies on the generator being well-grounded to ignore them

I still RETURN the distance on every result, so a threshold can be layered on
later (in retrieve or in the generator) without changing the contract.
```

---

### Edge cases

*How does your implementation behave when: (a) the collection is empty, (b) the query matches no chunks well, (c) the query matches chunks from multiple games?*

```
(a) Empty collection:
    Guarded up front — `if _collection.count() == 0: return []`. I never call
    query() on an empty store, so the caller gets a clean empty list and the
    generator can say it has no rules loaded. (Shouldn't happen in normal use
    because ingestion runs on startup, but it's cheap insurance.)

(b) Query matches nothing well:
    Because I use NO distance threshold, query() still returns the n closest
    chunks — they just carry HIGH distances (e.g. 0.9+). retrieve() hands them
    over as-is; it's the generator's job to notice the context doesn't support
    an answer and reply "that's not in the rules." The distance field is there
    if I later want to flag low-confidence results.

(c) Query spans multiple games:
    Fine, and expected. I don't filter by game (no `where` clause), so a vague
    query like "how do I win?" can return a mix — e.g. a Catan chunk AND a Risk
    chunk. Results are still globally ranked by distance, so the closest match
    leads regardless of game. The generator sees each chunk's "game" field, so
    it can attribute or disambiguate ("In Catan you win by..., in Risk by...").
    The risk is cross-game bleed on ambiguous queries, accepted as a tradeoff
    of not forcing the user to name the game.
```

---

## Implementation Notes

*Fill this in after implementing, before moving to Milestone 3.*

**Test query and top result returned:**

```
Query: What happens if you roll a 7 in Catan?
Top result game: Catan
Distance score: 0.402
Does it make sense? [yes / no / explain] ; yes, there is some overlap in the chunk but it does contain the bit we need:

[Catan] (dist: 0.402) ces that turn, regardless of the number rolled.

ROLLING A 7
When a 7 is rolled,...

These also show up in the output, since n_results = 3 :
[Catan] (dist: 0.503) UCTION
At the start of your turn, roll both dice. Every settlement adjacent to a...
[Monopoly] (dist: 0.534) your three turns in Jail. If you have not rolled doubles after three turns, pay ...
```

**One thing about the query results that surprised you:**

```
[your answer here]
```
