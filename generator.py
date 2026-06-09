from groq import Groq
from config import GROQ_API_KEY, LLM_MODEL

_client = Groq(api_key=GROQ_API_KEY)

# Exact fallback message (see generate-response-spec.md > Fallback behavior).
# Used both for the empty-chunks guard and as the reply the model is told to
# return verbatim when the rules don't contain the answer.
FALLBACK_MESSAGE = (
    "I couldn't find an answer to that in the loaded rule books. I can only "
    "answer from the rules I have for: Catan, Clue, Codenames, Monopoly, "
    "Pandemic, Risk, Ticket to Ride, and Uno. Try rephrasing your question, "
    "or make sure you're asking about one of those games."
)

# Static system prompt = the "rules of engagement" (grounding + citation +
# fallback). Contains no rule text and no user question — those go in the user
# message — so this stays identical on every call.
SYSTEM_PROMPT = (
    "You are RulesBot, a board-game rules assistant. Answer the user's question "
    "using ONLY the rule text provided in the RULES CONTEXT block of the user "
    "message. Treat that text as your single source of truth.\n\n"
    "- Do NOT use any outside or prior knowledge about board games, even if you "
    "are confident you know the answer.\n"
    "- Do NOT infer, assume, or fill in details that are not explicitly stated "
    "in the provided text.\n"
    "- If the answer is not fully contained in the provided rules, reply with "
    "exactly the following message and nothing else:\n"
    f'    "{FALLBACK_MESSAGE}"\n\n'
    "When you can answer, begin your response by naming the game from the source "
    "you used (e.g. 'In Catan, ...'). Use only the game label shown in the "
    "[Source N — <game>] header of the rule text you relied on; do not invent or "
    "guess a game name. If the relevant sources span more than one game, name "
    "each game alongside the part of the answer it supports.\n\n"
    "A correct \"that isn't in the loaded rules\" is always better than a "
    "confident answer drawn from outside the provided text."
)


def generate_response(query, retrieved_chunks):
    """
    Generate a grounded answer from retrieved rule chunks.

    TODO — Milestone 3:

    `retrieved_chunks` is the list returned by retrieve(). Each item is a dict:
      - "text"     : the chunk text
      - "game"     : the game name
      - "distance" : similarity score (you can use this to filter weak matches)

    Before writing code, talk through these with your group:
      - How will you format the chunks into a context block for the prompt?
      - What instructions will stop the model from answering beyond what the
        rules say? (Grounding is the whole point — a confident wrong answer
        is worse than an honest "I don't know.")
      - How will you surface which game each answer comes from?

    Your response should:
      1. Answer using only the retrieved context — not the model's general knowledge
      2. Make clear which game the answer comes from
      3. Say so clearly when the answer isn't in the loaded rules

    Return the response as a plain string.
    """
    if not retrieved_chunks:
        return FALLBACK_MESSAGE

    # Build the RULES CONTEXT block: one labeled, "---"-delimited source per
    # chunk, kept in retrieve()'s ranking order (strongest match = Source 1).
    # No distance scores go into the prompt — they're retrieval-internal.
    blocks = [
        f"[Source {i} — {chunk['game']}]\n{chunk['text']}"
        for i, chunk in enumerate(retrieved_chunks, start=1)
    ]
    context = "\n\n---\n\n".join(blocks)

    # The question follows the context so the model reads the sources first,
    # then answers "given these rules, answer this".
    user_message = f"RULES CONTEXT:\n\n{context}\n\nQUESTION: {query}"

    response = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content
