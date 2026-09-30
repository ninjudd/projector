# Writing guide

Projector's skills write prose for people to read: plans, reviews, pull
request summaries, and commit messages. Each of those skills writes to this
guide.

The guide follows the Google developer documentation style, which
Projector's own documentation also uses. A skill says what its own output
adds to the guide. Where a skill requires a format, such as the headline a
review finding opens with, that format wins. A repository's own
documentation rules outrank this guide where the two disagree, so a plan
written into a repository with its own style follows that style. Cite a
section by this file and its section number, as `writing.md § 2`.

This guide adapts many ideas from
[pstack](https://github.com/cursor/plugins/tree/main/pstack), a plugin by
Lauren Tan. The ideas come from its `technical-writing` and `unslop`
skills, which draw on four sources:

- The Google developer documentation style guide.
- The Diátaxis framework.
- Simplified Technical English, the ASD-STE100 specification.
- John Kohl's *The Global English Style Guide*.

## 1. Follow three rules above the rest

1. **Cut the words that do no work.** "In order to" is "to". "It is
   important to note that" is nothing at all.
2. **Prefer the everyday word.** Write "use", not "utilize" or "leverage".
   A longer word has to earn its place by being more exact.
3. **Put the reader first.** When a rule below makes a sentence harder to
   read, fix the sentence another way, or break the rule.

For example, do not write this:

> The site can be previewed locally, and it's worth noting that summaries
> need to have been published beforehand in order for them to show up.

Write this:

> To preview the site, run `project site serve`. It shows each summary that
> `project summary publish` commits to `refs/projector/summaries`, about a
> second after the publish.

## 2. Write for someone new to the subject

Your reader knows the language and general engineering practice. They may
not know this code, this part of the system, or the words its authors use
among themselves. Write so that they can follow without opening another
document. A reader who knows the code loses nothing, and one who does not
can follow at all.

- **Start from what the reader already knows.** Say what a part of the
  system does, and how it behaved before a change. Then say what changes,
  and why. Describe the behavior a user or an operator would see before you
  name the code behind it.
- **Define each term where the reader meets it.** Terms include product
  and vendor names, acronyms, and the project's own words. Give a code
  identifier its plain meaning beside it: "`Decide`, which turns the two
  check results into allow or block". A reader who jumps into the middle
  meets a term there. So define the term again, briefly, in each part that
  uses it.
- **Use the code's words, not synonyms for them.** Name the real symbol,
  path, or command. Some of the project's words are also names in the code.
  Define such a word once, then keep using it, so that the prose and the
  code say the same thing. Invent no jargon of your own. Prefer the words
  that an engineer on the team would say out loud.
- **Name code only when it helps.** An identifier points into the code, but
  it explains nothing. Prefer a sentence about what happens over a list of
  the functions that make it happen. Keep field numbers and commit hashes
  out of the prose unless the reader needs them. A value that the reader
  will see in the code, such as a status or a mode, is one of the code's
  words. Define it, then use it.
- **Leave out references the reader cannot follow.** "Decision 4.19",
  "§ 8", and "as the other one does" point at things the reader has not
  seen. State the decision, the section, or the comparison itself. A
  citation can follow the statement, but it cannot replace it.
- **Keep each note self-contained.** A note that asks the reader to check
  something first says what should be true, and why it matters. Then it
  names the code to trace. A note about a risk says what could go wrong for
  a user or for the data, not only which line looks suspect. A note defines
  a term again only when nothing above it on the page defines it.
- **Let context win over brevity.** Context makes the text longer. Cut
  repetition, and cut detail that nobody would act on. Never cut the
  context, or a note that carries a finding.

## 3. Know what kind of document you are writing

The Diátaxis framework sorts documents into four kinds by the job they do.
Decide which job your text does before you write. Keep each part to one
job, and give a second job its own section.

| Kind | Job | How it reads |
| --- | --- | --- |
| Tutorial | Teach by doing | Steps a learner follows, each with a result they can see |
| How-to | Get a known task done | Steps for a reader who knows the basics, with no teaching |
| Reference | Look something up | A complete, exact, and neutral description, with no instructions or opinions |
| Explanation | Understand why | One bounded topic, with its design decisions, history, constraints, and alternatives |

In a plan, the implementation sequence is a how-to, and the decisions are
an explanation. In a summary, the group intros explain, and the notes tell
the reader what to check. A review reports what it found.

## 4. Give the prose structure

Let the shape of the content choose its form. Not everything is a
paragraph.

- **Lead with what matters.** Open each part with one or two sentences that
  say what it covers and why. A reader who stops there has the gist. Put
  the detail after those sentences.
- **Use a numbered list for a sequence**, such as the steps of a request,
  in order.
- **Use a bulleted list for parallel items**, such as the checks that a
  change adds, or the settings that control them. Open each item with a
  short lead-in in bold that names the item. Then explain it in full
  sentences.
- **Use a table for anything with two dimensions**, such as which results
  allow a request and which block it. A setting's default and its effect
  also belong in a table.
- **Keep a paragraph to a few sentences about one idea.** A longer
  paragraph usually holds a list or a table.
- **Split a long part with headings that make a point.** "Held cards wait
  for the network's answer" tells the reader more than "Holding". Write
  headings in sentence case.
- **Use bold only for a list item's lead-in and the name of something on
  screen.** Bold in other places competes with the lead-ins, and it soon
  means nothing. A glossary can give each entry as a term and a definition,
  not as a sentence.

## 5. Write to the reader

- **Address the reader as "you"** when you tell them what to do or check.
  An instruction in the imperative, such as "Check that the record is
  cleared", already addresses them.
- **Write in the present tense.** Keep "will" for events that really happen
  later.
- **Name the actor.** Write "the engine refuses the card", not "the card is
  refused". Use the passive voice only when the actor does not matter or is
  unknown.
- **Write each instruction as a command.** Write "Run `project check`", not
  "You may want to run `project check`".
- **Put the condition or the goal before the instruction.** "To preview a
  summary, run `project site serve`."
- **Put the common case first**, and the exceptions after it.
- **Sound like a colleague who knows the system.** Do not announce what
  comes next ("This section describes…"). Start your sentences in
  different ways.
- **Put exact identifiers, paths, values, and commands in code font.**
- **Show a command or an example** when it communicates more directly than
  prose.
- **Make link text say where the link goes.** Never write "click here" or
  "this link".
- **Use the serial comma**: "the spec, the diff, and the page".

## 6. Make every sentence easy on the first read

Simplified Technical English is a controlled form of English for
maintenance manuals, whose readers cannot stop to ask what a sentence
means. These rules take its approach. Write for a reader who is tired or
busy, or who reads English as a second language. Each sentence should make
sense the first time through.

- **Give each sentence one idea**, and each instruction its own sentence.
  Split an instruction of more than about 20 words, and any other sentence
  of more than about 25.
- **Vary the rhythm.** A short sentence makes a point land. A longer
  sentence carries a fact with its condition or its result.
- **Be specific.** "A renamed column fails the build" says more than
  "schema changes can cause issues".
- **Give a warning before the step that it guards**, not after the step.
- **Give each word one meaning, and each action one verb.** If you
  "publish" a spec in one paragraph, do not "push" it in the next, unless
  you mean a different action.
- **Keep the small words that show a sentence's structure**, such as "the",
  "a", and "that". When you drop them to save space, a sentence can have
  two meanings.
- **Use few words that end in "-ing".** A word such as "checking" can be a
  noun, a verb, or an adjective. The reader has to guess which.

## 7. Leave no sentence open to two readings

*The Global English Style Guide* teaches writing that readers all over the
world can understand, in English or in translation. These rules come from
it.

- **Put "only", "not", and "just" next to the word they limit.**
- **Change a stack of nouns into a clause.** "The record that the engine
  writes when a check fails" is faster to read than "the failed-check
  engine record".
- **Make each pronoun point at one thing.** When "it" or "this" can refer
  to two things, repeat the noun.
- **Give each part of a parallel structure its own verb.** Write "the
  engine checks the name and records the result", not "checks the name and
  the result".
- **Use pairs of words to show what goes together**, such as "both … and",
  "either … or", and "if … then".
- **Start a new sentence instead of using a semicolon or a dash.** Two
  short sentences are clearer than one long sentence.
- **Make a parenthetical a complete thought**, or change it into a
  sentence.
- **Write the alternatives instead of using a slash.** Write "a card, a
  bank account, or both", not "card/bank account".
- **Use one name for each thing.** When a thing has a name, keep that name.

## 8. Cut what reads as filler

Some patterns make text sound machine-written, and they hide what the text
says. Rewrite a sentence that depends on one of these patterns.

- **Inflated words**, such as "delve", "pivotal", "robust", "seamless", and
  "landscape". Abstract nouns used as metaphors, such as "substrate",
  "vector", and "flywheel", are also inflated. Say the concrete thing.
- **A weak verb instead of "is" or "has"**, such as "serves as", "stands
  as", or "boasts".
- **Empty framing**, such as "not just X but Y". A list of three items
  because three sounds complete is also empty framing. So is a clause at
  the end of a sentence that claims an effect: "…, ensuring the card is
  safe".
- **Different words for the same thing.**
- **Claims without a source**, such as "experts agree" or "this is widely
  used". Name the source, or cut the claim.
- **Hedges and softeners** when you know the answer, such as "should",
  "simply", "easily", "basically", and "please".
- **Pleasantries and closing summaries** that repeat what the text already
  said, such as "I hope this helps" or "In summary, …".
- **Decoration**, such as emoji and headings in title case.

## 9. Say how sure you are

Keep what you found separate from what you infer. State as fact what the
code or a test shows. Mark an inference as an inference: "The commit
message suggests a workaround, but no test covers it." When you looked for
something and did not find it, say so. If you do not, the reader assumes
that you did not look.

## 10. Treat pull requests and commit messages as writing

All the sections above apply to them, except § 3, because a pull request
body has one job.

- **Brief the reader in about one minute.** In prose, say why the change
  exists and what it does. A squash merge makes the body the commit
  message, so write the body to last.
- **Leave out logs and lists of commits.** Summarize what they show.
- **Keep each path, symbol, and command true** at the commit that lands.

## 11. Leave history to the commit log

Write what is true now, and why. How the code or the text came to be is
history, and history belongs in commit messages and pull request bodies.
There, `git log` and `git blame` keep it next to the change that made it.

- **Leave out what used to be true.** Phrases such as "was renamed from",
  "no longer", "used to", and "previously" send the reader to a past they
  never saw. Describe the current behavior instead.
- **Give a reason as a reason, not as a story.** Write "The fetch runs in
  its own session, so it cannot ask for a passphrase", not "After a review
  found that the fetch could prompt, it moved into its own session."
- **Keep history only when it changes what the reader does.** A reader may
  still meet an old name in old records, or have data that an older version
  wrote. Say what they meet and what to do, in as few words as that takes.
- **Know that a change's before and after is not history.** A pull request
  summary or a review explains what a change does, so the old behavior is
  part of its content. A plan's rejected alternatives are not history
  either. They explain its decisions.
