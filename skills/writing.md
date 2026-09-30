# Writing guide

Every Projector skill that writes prose a person reads, such as a plan, a
review, a pull request summary, or a commit message, writes it to this
guide. It follows the Google developer documentation style, the style
Projector's own documentation uses. A skill names what its own output adds
to the guide. Cite a section by its name and number, as `writing.md § 2`.

Much of this guide adapts ideas from the `technical-writing` and `unslop`
skills in [pstack](https://github.com/cursor/plugins/tree/main/pstack), by
Lauren Tan, which draws on the Google developer documentation style guide,
the Diátaxis framework, Simplified Technical English (ASD-STE100), and
Kohl's *The Global English Style Guide*.

## 1. Follow three rules above the rest

1. **Cut the words that do no work.** "In order to" is "to". "It is
   important to note that" is nothing at all.
2. **Prefer the everyday word.** Write "use", not "utilize" or "leverage".
   A longer word has to earn its place by being more exact.
3. **Put the reader first.** When a rule below makes a sentence harder to
   read, fix the sentence another way, or break the rule.

## 2. Write for someone new to the subject

Write for a reader who knows the language and general engineering practice,
but may be seeing this code, this part of the system, and the words its
authors use among themselves for the first time. What you write should make
sense to them without opening another document. A reader who knows the code
loses nothing by it, and one who does not can follow at all.

- **Start from what the reader already knows.** Say what a part of the
  system does and how it behaved before a change. Then say what changes,
  and why. Describe behavior a user or an operator would recognize before
  you name the code that implements it.
- **Define every term where the reader meets it.** That covers product and
  vendor names, acronyms, and the project's own words. Give a code
  identifier its plain meaning beside it: "`Decide`, which turns the two
  check results into allow or block". A reader who jumps into the middle
  meets a term there, so define it again, briefly, in each part that uses
  it.
- **Use the code's words, not synonyms for them.** Name the real symbol,
  path, or command. When the project's word is also a name in the code,
  define it and then keep using it, so the prose and the code say the same
  thing. Invent no jargon of your own, and prefer the words an engineer on
  the team would say out loud.
- **Name code only when it helps.** An identifier points into the code. It
  does not explain anything. Prefer a sentence about what happens over a
  list of the functions that make it happen. Keep field numbers and commit
  hashes out of the prose unless the reader needs them. A value the reader
  will meet in the code, such as a status or a mode, is one of the code's
  words, so define it and then use it.
- **Leave out references the reader cannot follow.** "Decision 4.19",
  "§ 8", and "as the other one does" point at things the reader has not
  seen. State the decision, the section, or the comparison itself. A
  citation may follow it but never replaces it.
- **Keep each note self-contained.** A note that asks the reader to check
  something says in plain words what should be true and why it matters,
  before it names the code to trace. A note about a risk says what could go
  wrong for a user or for the data, not only which line looks suspect. A
  note defines a term again only when nothing above it on the page does.
- **Let context win over brevity.** Explaining what you used to assume
  makes the text longer. Cut repetition and detail nobody would act on, but
  never the context or a note that carries a finding.

## 3. Know what kind of document you are writing

A document does one of four jobs. Decide which before you write, and keep
each part to one job. When one piece of writing needs two jobs, give each
its own section.

| Kind | What it is for | How it reads |
| --- | --- | --- |
| Tutorial | Teaching by doing | Steps a learner follows, each with a result they can see |
| How-to | Getting a known task done | Steps for a reader who already knows the basics, with no teaching |
| Reference | Looking something up | Complete, exact, and neutral description, with no instructions or opinions |
| Explanation | Understanding why | One bounded topic, with the design decisions, history, constraints, and alternatives behind it |

A plan's implementation sequence is a how-to, and its decisions are an
explanation. A summary's group intros explain, and its file and line notes
point the reader at what to check. A review reports what it found.

## 4. Give the prose structure

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
- **Split a long part with headings that make a point.** "Held cards wait
  for the network's answer" tells the reader more than "Holding". Write
  headings in sentence case.
- **Bold only a list item's lead-in and the name of something on screen.**
  Bold anywhere else competes with the lead-ins and stops meaning anything.
  A glossary may give each entry as a term and its definition rather than
  as a sentence.

## 5. Write to the reader

- **Address the reader as "you"** when you tell them what to do or check.
  An instruction in the imperative, such as "Check that the record is
  cleared", already addresses them.
- **Write in the present tense.** Keep "will" for something that really
  happens later.
- **Name who acts.** Write "the engine refuses the card", not "the card is
  refused". Use the passive voice only when the actor does not matter or is
  unknown.
- **Write an instruction as a command.** "Run `project check`", not "You
  may want to run `project check`".
- **Put the condition or the goal before the instruction.** "To preview a
  summary, run `project site serve`."
- **Put the common case first** and the exceptions after it.
- **Sound like a colleague who knows the system.** Skip the announcement
  that something is coming ("This section describes…"), and vary how your
  sentences start.
- **Put exact identifiers, paths, values, and commands in code font.**
- **Show a command or an example** when it communicates more directly than
  prose.
- **Link with words that say where the link goes**, never "click here" or
  "this link".
- **Use the serial comma**: "the spec, the diff, and the page".

## 6. Make every sentence easy on the first read

Write for a reader who is tired, busy, or reading in their second language.
Each sentence should make sense the first time through.

- **Give each sentence one idea**, and each instruction a sentence of its
  own. Split an instruction past about 20 words and any other sentence past
  about 25.
- **Vary the rhythm.** A short sentence makes a point land. A longer one
  carries a fact with its condition or its consequence.
- **Be specific.** "Renaming a column fails the build" says more than
  "schema changes can cause issues".
- **Warn before the step the warning guards**, not after it.
- **Give each word one meaning, and each action one verb.** If you "publish"
  a spec in one paragraph, do not "push" it in the next unless you mean a
  different thing.
- **Keep the small words that show a sentence's structure**, such as "the",
  "a", and "that". Dropping them to save space makes a sentence readable
  two ways.
- **Use few words that end in "-ing".** A word like "checking" can be a
  noun, a verb, or an adjective, and the reader has to guess which.

## 7. Leave no sentence open to two readings

- **Put "only", "not", and "just" beside what they limit.**
- **Break a stack of nouns into a clause.** "The record the engine writes
  when a check fails" reads faster than "the failed-check engine record".
- **Make every pronoun point at one thing.** When "it" or "this" could mean
  two things, repeat the noun.
- **Give every part of a parallel structure its own verb**: "the engine
  checks the name and records the result", not "checks the name and the
  result".
- **Use paired words to show what goes together**, such as "both … and",
  "either … or", and "if … then".
- **End the sentence instead of reaching for a semicolon or a dash.** Two
  short sentences are clearer than one joined long one.
- **Make a parenthetical a complete thought**, or turn it into its own
  sentence.
- **Write out alternatives instead of using a slash.** Write "a card, a
  bank account, or both", not "card/bank account".
- **Call each thing by one name throughout.** Once a thing has a name, keep
  it.

## 8. Cut what reads as filler

Some patterns make text sound generated and hide what it says. Rewrite a
sentence that leans on one.

- **Inflated words**, such as "delve", "pivotal", "robust", "seamless", and
  "landscape", and abstract nouns used as metaphors, such as "substrate",
  "vector", and "flywheel". Say the concrete thing.
- **A weak verb standing in for "is" or "has"**, such as "serves as",
  "stands as", or "boasts".
- **Empty framing**, such as "not just X but Y", a list of three written
  because three sounds complete, or a trailing clause that claims an
  effect: "…, ensuring the card is safe".
- **Rotating synonyms for one thing.**
- **Unsupported claims and vague sources**, such as "experts agree" or
  "this is widely used". Name the source or cut the claim.
- **Hedges and softeners** where you know the answer, such as "should",
  "simply", "easily", "basically", and "please".
- **Pleasantries and closing summaries** that repeat what the text already
  said, such as "I hope this helps" or "In summary, …".
- **Decoration**, such as emoji and headings in title case.

## 9. Say how sure you are

Keep what you found apart from what you infer. Say what the code or a test
shows as fact, and mark an inference as one: "The commit message suggests
this was a workaround, but no test covers it." When you looked for
something and did not find it, say so. Otherwise the reader assumes you did
not look.

## 10. Treat pull requests and commit messages as writing

Every section above applies to them except § 3, since a pull request body
has one job.

- **Brief the reader in about a minute.** Say why the change exists and
  what it does, in prose. A squash merge makes the body the commit message,
  so write it to last.
- **Leave out logs and lists of commits.** Summarize what they show.
- **Keep every path, symbol, and command true** at the commit that lands.

For example, instead of:

> The site can be previewed locally, and it's worth noting that summaries
> need to have been published beforehand in order for them to show up.

write:

> To preview the site, run `project site serve`. It shows each summary that
> `project summary publish` commits to `refs/projector/summaries`, about a
> second after the publish.
