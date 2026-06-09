# Spec: `generate_response()`

**File:** `generator.py`
**Status:** Spec incomplete — fill in all blank fields before implementing

---

## Purpose

Given a user query and a list of retrieved rule chunks, generate a response that directly answers the question using only the retrieved text as context. The response must be grounded — it should not draw on the model's general knowledge of board games, only on what was retrieved.

---

## Input / Output Contract

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `str` | The user's original question |
| `retrieved_chunks` | `list[dict]` | Ranked list of chunks from `retrieve()`, each with `"text"`, `"game"`, and `"distance"` |

**Output:** `str`

A plain string containing the response to show the user. The response should:
- Answer the question using only the retrieved rule text
- Identify which game the answer comes from
- Acknowledge clearly when the answer is not found in the loaded rules

Returns a fallback string (not an error) when `retrieved_chunks` is empty.

---

## Design Decisions

*Complete the fields below before writing any code. Use your AI tool in Plan or Ask mode to help you reason through what belongs here — but the decisions are yours.*

---

### Context formatting

*How will you format the retrieved chunks before passing them to the LLM? Describe the structure — not the code. Consider: will you label chunks by game? Include distance scores? Separate chunks with delimiters?*

```
I build a single "context block" string from retrieved_chunks (already ranked
best-first by retrieve()), then drop it into the user message.

Per chunk, I include:
  - a GAME LABEL — yes. Each chunk is prefixed with its game name so the model
    can both ground its answer in the right book and cite the game. This is the
    only metadata the model needs to do its job.
  - the chunk TEXT — the raw rule text, verbatim.
  - NO distance score. Distance is a retrieval-internal signal; exposing a
    number like 0.42 to the LLM is noise it can't interpret and might leak into
    the answer. I use distance for filtering (see "low-relevance" section), not
    as content.

Delimiter used: each chunk is its own block headed by a bracketed label line
"[Source N — <game>]" on its own line, and consecutive blocks are separated by
a line containing exactly "---" with a blank line on each side. So the literal
separator between two chunks is:

    \n\n---\n\n

Putting it together, the context block looks like:

    RULES CONTEXT:

    [Source 1 — Catan]
    When a 7 is rolled, no resources are produced. Every player with more...

    ---

    [Source 2 — Catan]
    At the start of your turn, roll both dice. Every settlement adjacent...

Ordering follows retrieve()'s ranking (lowest distance first), so the strongest
match is Source 1. The whole block is labeled "RULES CONTEXT:" so the prompt can
refer to it unambiguously when telling the model to answer "only from the
sources above."

Why this shape: the "---" line is a visually unambiguous boundary the model
won't confuse with rule text, and the "[Source N — game]" headers make grounding
and citation easy (the model can say "According to the Catan rules...") while
keeping multiple chunks — possibly from different games — from bleeding together.
```

---

### System prompt — grounding instruction

*Write the exact system prompt instruction you will use to prevent the model from answering beyond the retrieved text. This is the most important design decision in this function.*

```
Strong grounding instruction (the exact text I will use):

  "You are RulesBot, a board-game rules assistant. Answer the user's question
   using ONLY the rule text provided in the RULES CONTEXT block below. Treat
   that text as your single source of truth.

   - Do NOT use any outside or prior knowledge about board games, even if you
     are confident you know the answer.
   - Do NOT infer, assume, or fill in details that are not explicitly stated in
     the provided text.
   - If the answer is not fully contained in the provided rules, say so
     explicitly using the fallback message rather than guessing.

   A correct 'that isn't in the loaded rules' is always better than a confident
   answer drawn from outside the provided text."
```

---

### System prompt — citation instruction

*Write the exact instruction you will use to tell the model to identify which game its answer comes from.*

```
Exact instruction (added to the system prompt):

  "Every answer must state which game it comes from. Begin your response by
   naming the game from the source you used (e.g. 'In Catan, ...'). Use only
   the game label shown in the [Source N — <game>] header of the rule text you
   relied on — do not invent or guess a game name.

   If the relevant sources come from more than one game, name each game
   alongside the part of the answer it supports. If no source contains the
   answer, do not name a game — return the fallback message instead."
```

---

### Fallback behavior

*What should the response say when the answer isn't found in the loaded rule books? Write the exact fallback message.*

```
Exact fallback message:

  "I couldn't find an answer to that in the loaded rule books. I can only
   answer from the rules I have for: Catan, Clue, Codenames, Monopoly,
   Pandemic, Risk, Ticket to Ride, and Uno. Try rephrasing your question, or
   make sure you're asking about one of those games."

Two cases trigger it:
  1. retrieved_chunks is empty -> returned directly in code, before any LLM call
     (this is the existing guard in generator.py).
  2. Chunks exist but none actually contain the answer -> the system prompt
     instructs the model to reply with this exact message instead of guessing.
```

---

### Handling low-relevance chunks

*`retrieved_chunks` may include chunks with high distance scores (weak relevance). Will you filter these out before building context, pass them all in, or handle them another way? What are the tradeoffs?*

```
Decision: pass all retrieved chunks into the context, do NOT hard-filter by
distance in code. Grounding (the system prompt) decides what's actually usable.

Why not a fixed distance cutoff: my own test data showed legitimate matches
landing at ~0.50–0.64 and an irrelevant Risk chunk at 0.655 — they overlap. So
any single threshold either keeps the junk (cutoff too high) or drops good
answers (cutoff too low). Cosine distances aren't stable enough across queries
to pick one magic number safely.

How weak chunks are handled instead: the grounding instruction already forbids
using anything not actually in the sources, so a high-distance, off-topic chunk
just gets ignored by the model rather than filtered by me. If NO chunk supports
the answer, the model returns the fallback message. This keeps the "is this
relevant?" judgment in one place (the prompt) instead of split between a brittle
threshold and the model.

Tradeoffs:
  Filter in code (drop distance > X):
    + smaller, cleaner prompt; fewer tokens; junk never reaches the model
    - magic-number problem above; risk of dropping the only good chunk and
      forcing a false "not in the rules"
  Pass all, let grounding decide (my choice):
    + no threshold to tune; model sees full context and abstains when unsupported
    + robust to the distance overlap I actually observed
    - a few weak chunks ride along (slightly more tokens); relies on the
      grounding prompt being strong

Safety valve: I still have the distance on every chunk, so if testing shows the
model getting distracted by weak chunks, I can add a light cap later (e.g. keep
top result always, drop the rest only if distance > 0.8) without reworking the
contract.

```

---

### Message structure

*Describe how you will structure the messages list for the API call — what goes in the system message vs. the user message?*

```
Two-message structure: a system message + a single user message. No assistant
or few-shot messages.

SYSTEM message — the fixed "rules of engagement," identical on every call:
  - role/persona ("You are RulesBot...")
  - the grounding instruction (answer ONLY from the provided rules, no outside
    knowledge, no gap-filling)
  - the citation instruction (name the game from the [Source N — <game>] header)
  - the fallback rule (return the exact fallback message when unsupported)
  It contains NO rule text and NO user question — only behavior/policy. Keeping
  it static also makes it a clean candidate for prompt caching later.

USER message — the per-request payload, built fresh each call:
  - the RULES CONTEXT block (the labeled, "---"-delimited source chunks from
    the formatting decision above)
  - then the user's actual question, clearly marked, e.g.:

        RULES CONTEXT:
        [Source 1 — Catan]
        When a 7 is rolled, no resources are produced...
        ---
        [Source 2 — Catan]
        ...

        QUESTION: What happens if you roll a 7 in Catan?

Why context goes in the USER message, not the system message:
  - the context is request-specific (changes every query), so it belongs with
    the request, not in the static policy;
  - it keeps the system prompt stable/cacheable;
  - it mirrors the real-world framing — the user is "handing over" the source
    text along with their question, and the system message is the standing
    instruction for how to treat it.

Why the question goes AFTER the context: the model reads the sources first, then
the question, so it answers in the frame of "given these rules, answer this" —
which reinforces grounding over recall.

```

---

## Implementation Notes

*Fill this in after implementing and testing.*

**Test query and response:**

```
Query: [your test query]
Response: [abbreviated response]
Correctly grounded? [yes / no]
Cited the right game? [yes / no]
```

**One thing you changed from your original spec after seeing the actual output:**

```
[your answer here]
```
