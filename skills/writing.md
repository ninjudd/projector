# Writing guide

Every Projector skill that writes prose a person reads, such as a plan, a
review, or a pull request summary, writes it in the Google developer
documentation style, the style Projector's own documentation uses. A skill
names what its own output adds to this guide. Cite a section of this file
by its name and number, as `writing.md § 1`.

## 1. Write for someone new to the subject

Write for a reader who knows the language and general engineering practice,
but may be seeing this code, this part of the system, and the words its
authors use among themselves for the first time. What you write should make
sense to them without opening another document. A reader who knows the code
loses nothing by it, and one who does not can follow at all.

- **Start from what the reader already knows.** Say what a part of the
  system does and how it behaved before a change, then what changes, then
  why. Describe behavior a user or an operator would recognize before you
  name the code that implements it.
- **Define every term where the reader meets it.** That covers product and
  vendor names, acronyms, and the project's own words. Give a code
  identifier its plain meaning beside it: "`Decide`, which turns the two
  check results into allow or block". A reader who jumps into the middle
  meets a term there, so define it again, briefly, in each part that uses
  it.
- **Use the code's words once they are defined.** When the project's word
  is also a name in the code, define it and then keep using it, so the
  prose and the code say the same thing. A paraphrase the code never uses
  leaves the reader to match two vocabularies.
- **Name code only when it helps.** An identifier points into the code; it
  does not explain anything. Prefer a sentence about what happens over a
  list of the functions that make it happen. Keep field numbers and commit
  hashes out of the prose unless the reader needs them. A value the reader
  will meet in the code, such as a status or a mode, is one of the code's
  words: define it and then use it.
- **Leave out references the reader cannot follow.** "Decision 4.19",
  "§ 8", and "as the other one does" point at things the reader has not
  seen. State the decision, the section, or the comparison itself; a
  citation may follow it, never replace it.
- **Keep each note self-contained.** A note that asks the reader to check
  something says in plain words what should be true and why it matters
  before it names the code to trace. A note about a risk says what could go
  wrong for a user or for the data, not only which line looks suspect. A
  note defines a term again only when nothing above it on the page does.
- **Let context win over brevity.** Explaining what you used to assume
  makes the text longer. Cut repetition and detail nobody would act on,
  never the context or a note that carries a finding.

## 2. Give the prose structure

Let the shape of the content choose its form, instead of writing everything
as paragraphs.

- **Lead with what matters.** Open each part with one or two sentences that
  say what it covers and why, so a reader who stops there has the gist. Put
  the detail after them.
- **Use a numbered list for a sequence**, such as the steps a request goes
  through, in order.
- **Use a bulleted list for parallel items**, such as the checks a change
  adds, the outcomes it can reach, or the settings that control it. Open
  each item with a short bold lead-in that names the thing, then explain it
  in full sentences.
- **Use a table for anything with two dimensions**, such as which
  combinations of results allow or block a request, or each setting's
  default and effect.
- **Keep a paragraph to a few sentences about one idea.** A longer
  paragraph usually holds a list or a table.
- **Use a heading to split a long part.** A heading such as "Before",
  "What changes", or "Why" helps a reader find their place in anything that
  runs past three paragraphs.
- **Bold only a list item's lead-in.** Bold anywhere else competes with the
  lead-ins and stops meaning anything. A glossary may give each entry as a
  term and its definition rather than as a sentence.

## 3. Use the Google style's voice

- **Address the reader as "you"** when you tell them what to do or check.
  An instruction in the imperative, such as "Check that the record is
  cleared", already addresses them.
- **Write in the present tense and the active voice.** Say "the engine
  refuses the card", not "the card will be refused".
- **Write headings in sentence case.**
- **Put exact identifiers, paths, values, and commands in code font.**
- **Show a command or an example** when it communicates more directly than
  prose.
- **Write whole sentences.** No slash-separated lists standing in for
  prose, no telegraphic fragments, no shorthand the reader has to decode,
  and no hedging where you know the answer.

## 4. Make every sentence easy on the first read

Write for a reader who is tired, busy, or reading in their second language.
Each sentence should make sense the first time through.

- **Give each sentence one idea.** Split a sentence past about 25 words,
  or past about 20 when it tells the reader what to do.
- **Put the condition before the action** and the common case before the
  exceptions: "When the check is off, the engine records nothing", not "The
  engine records nothing when the check is off, unless…".
- **Repeat the noun rather than leave a pronoun unclear.** "It" and "this"
  should point at one thing the reader can name.
- **Put "only", "not", and "just" beside what they limit.**
- **Break a stack of nouns into a clause.** "The record the engine writes
  when a check fails" reads faster than "the failed-check engine record".
- **Link with words that say where the link goes**, never "click here" or
  "this link".
- **Prefer the short, everyday word.** Use "use" over "utilize" and
  "leverage", "start" over "commence", and "to" over "in order to".

## 5. Cut what reads as filler

Some patterns make text sound generated and hide what it says. Rewrite a
sentence that leans on one.

- **Inflated words**, such as "delve", "pivotal", "robust", "seamless", and
  "landscape", and abstract nouns used as metaphors, such as "substrate",
  "vector", and "flywheel". Say the concrete thing.
- **A weak verb standing in for "is" or "has"**, such as "serves as",
  "stands as", or "boasts".
- **Empty framing**, such as "not just X but Y", a list of three written
  because three sounds complete, or a trailing "-ing" clause that claims an
  effect: "…, ensuring the card is safe".
- **Rotating synonyms for one thing.** Once a thing has a name, keep it.
- **Unsupported claims and vague sources**, such as "experts agree" or
  "this is widely used". Name the source or cut the claim.
- **Hedges and softeners** where you know the answer, such as "should",
  "simply", "easily", "basically", and "please".
- **Pleasantries and closing summaries** that repeat what the text already
  said, such as "I hope this helps" or "In summary, …".
- **Decoration**: em dashes in place of commas or periods, emoji, and
  headings in title case.

## 6. Say how sure you are

Keep what you found apart from what you infer. Say what the code or a test
shows as fact, and mark an inference as one: "The commit message suggests
this was a workaround, but no test covers it." When you looked for
something and did not find it, say so rather than leaving the reader to
assume you did not look.
