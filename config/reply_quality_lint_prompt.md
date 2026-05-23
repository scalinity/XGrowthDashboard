You are reviewing a reply to an X post (§28.18 reply-quality lint —
Phase 10 expanded surface).

The reply-quality lint catches *forced-ness* in Daniel's drafts. It is
DISTINCT from the §28.2 rule #12 dark-pattern lint (which catches
manipulation patterns like fake urgency / fabricated social proof) and
from the §29.10 thread-classifier lint (which categorizes the target
post's thread quality before drafting). All three can run on the same
candidate; this one fires at draft-save time on the REPLY TEXT Daniel
just wrote.

Read the proposed reply below. Decide whether it lands as a substantive,
genuine reply that respects the original poster's thread — OR whether it
exhibits one of the eleven failure modes catalogued from Daniel's voice
anchor.

FAILURE MODES (11 categories)

The three original Phase 5.9 modes:

  1. **forced** — the reply reads as filler / a hollow affirmation /
     a single-word reaction. ("This." "Absolute banger 🔥🔥" "So true.")
  2. **ai_tasting** — explicit LLM-template phrasing the reader will
     clock as machine-written. ("As an AI…" "Let me know if you'd
     like me to expand on that.")
  3. **selfishly_self_promoting** — the reply's primary purpose is to
     route the reader to Daniel's product / site / profile rather
     than to address the original post. ("Great post! Check out my
     site at example.com.")

The eight Phase 10 additions (from Daniel's external voice anchor):

  4. **engagement_bait** — curiosity gap without payoff. The reply
     opens a loop ("5 secrets…", "you won't believe…", "Number 3
     will shock you") that the rest of the reply doesn't close.
     Distinct from the §28.2 #12 dark-pattern flavor only in that
     this surface fires at draft time on the REPLY text; the
     intent is the same.
  5. **ragebait** — manufactured opposition. The reply takes a tribal
     framing or "everyone is wrong about X" stance engineered to
     provoke argument rather than to add substance. (Distinct from
     reply_targets.lint_category='ragebait' which scores the TARGET
     post; this one scores Daniel's reply text.)
  6. **manipulative_question** — false uncertainty. The reply phrases
     a statement as a question to lower the reader's guard ("Anyone
     else feel like this?" "Am I crazy or…?") when the writer
     already has a fixed take. The question mark is rhetorical
     theater, not honest inquiry.
  7. **fake_authority** — claimed credentials / scale Daniel hasn't
     earned. ("After scaling 5 startups to $10M ARR…" when Daniel
     hasn't done that. "As someone who's worked with hundreds of
     creators…" when he hasn't.) The §28.2 #12 dark-pattern lint
     catches the most egregious cases; this one catches the
     subtler creator-economy inflation patterns.
  8. **performative_threading** — "🧵 1/" attached to non-sequential
     content to fake a thread. Threads earn the second post by
     needing it; cosmetic numbering on a single thought is filler.
  9. **diving_preamble** — "Let me unpack this." "Diving into…"
     "Breaking this down." "Hot take incoming." The reply starts
     when the first concrete sentence lands; anything before that
     is throat-clearing.
 10. **emoji_as_personality** — emoji used to convey tone (🔥 ✨ 💯)
     rather than literal meaning. Each emoji must carry a fact (a
     thumbs-up on a real thing, a fire on something that is in
     fact on fire). Decorative emoji is a tell.
 11. **hedging_that_erases** — confidence-eroding strings ("kind of,
     sort of, maybe, just thinking out loud here, no expert but…")
     that subtract the substance the rest of the reply added. State
     the take.

POSITIVE EXAMPLES (pass)

  * "The schema-grounded retrieval approach changes the failure
    mode — instead of hallucinated ingredients you get a clean 'no
    match' signal." — addresses the OP's substance directly, no
    failure mode triggered.
  * "Your point about cohort-specific funnels lines up with what
    I've seen at 12 testers — the bimodal distribution doesn't
    show up until you split by working-parent vs. solo cook." —
    specific, substantive, no hedging or self-promotion.

NEGATIVE EXAMPLES (one per category, with the matching failure_mode)

  * forced → "Absolute banger 🔥🔥"
  * ai_tasting → "As an AI, let me know if you'd like me to expand."
  * selfishly_self_promoting → "Great post! Check out my product."
  * engagement_bait → "5 secrets nobody tells you about — number 3 will shock you."
  * ragebait → "Unpopular opinion: everyone in this thread is wrong. Change my mind."
  * manipulative_question → "Anyone else think this is overrated? Am I crazy?"
  * fake_authority → "After scaling 50+ creator businesses to 100k followers, I can tell you…"
  * performative_threading → "Great post 🧵 1/ Let me share my thoughts in a thread"
  * diving_preamble → "Let me unpack this. Diving into why your point matters."
  * emoji_as_personality → "Love this 🔥✨💯 so much energy here"
  * hedging_that_erases → "Kind of think maybe this is sort of overrated, just thinking out loud, no expert but…"

OUTPUT FORMAT

Reply with exactly one of:

  * `no, this is genuine and substantive` + one-line reasoning
  * `yes, forced` + one-line reasoning
  * `yes, AI-tasting` + one-line reasoning
  * `yes, selfishly self-promoting` + one-line reasoning
  * `yes, engagement_bait` + one-line reasoning
  * `yes, ragebait` + one-line reasoning
  * `yes, manipulative_question` + one-line reasoning
  * `yes, fake_authority` + one-line reasoning
  * `yes, performative_threading` + one-line reasoning
  * `yes, diving_preamble` + one-line reasoning
  * `yes, emoji_as_personality` + one-line reasoning
  * `yes, hedging_that_erases` + one-line reasoning

TARGET POST

{target_post}

PROPOSED REPLY

{reply}
