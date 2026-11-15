# Humanizer: English

Sources: Wikipedia "Signs of AI writing", blader/humanizer, stop-slop, ASD-STE100 (condensed), plus measurements from the checker corpus.

## What machine prose does

Modern models avoid the old giveaway words. They still leave these:

- **Even rhythm.** Sentences of similar length, paragraphs of similar size, every paragraph opened by a topic sentence.
- **Generic content.** Few names, numbers, dates, places; no "I", no questions.
- **Hedging.** can, may, often, helps to, in half the sentences.
- **Triads.** Lists of exactly three, adjectives in threes.
- **Frames.** "Not just X, but Y"; "It's not X, it's Y"; ask-and-answer setups.
- **Riders.** A closing "-ing" clause: ", highlighting the need for...".
- **Wrappers.** Scene-setting opener ("In today's fast-paced world"), recap closer ("Overall...", "Ultimately...").
- **Connectors.** Additionally, Moreover, Furthermore at sentence starts.
- **Decoration.** Bold phrases, emoji, Title Case headings, "**Label:** text" bullets, em dashes.

## Rewrite in four passes

1. **Content.** Open with the main claim. Delete the opener that announces the topic and the closer that repeats it. Keep every fact. Put names, numbers and examples from the source where the text is vague. If there are none, cut the vague sentence.
2. **Structure.** Make paragraph sizes uneven: merge two, give one idea a one-sentence paragraph, let one run long. Turn "three benefits" lists into the number the source supports. Remove headings the text does not need.
3. **Sentences.** Mix lengths on purpose: next to a 25-word sentence, put one of 4 to 8 words. Change the subject at the start of consecutive sentences. Replace "-ing" riders and "not X but Y" frames with the claim itself. Use is, are and has instead of serves as, boasts, features. State known things as fact; keep a hedge only where the source is unsure.
4. **Words.** Use the table below. Straight quotes, sentence-case headings, no decorative bold, no emoji.
   Genuine triads from the source stay; write them as "X and Y, plus Z" or give the third its own sentence.

## Plain words

| Avoid | Use | Avoid | Use |
|---|---|---|---|
| utilize, leverage, harness | use | delve into | look at |
| facilitate | help, let | commence, initiate | start |
| enhance, elevate | improve, raise | crucial, vital, pivotal | important, needed |
| showcase, demonstrate | show | underscore, highlight | show, stress |
| foster, cultivate | build, encourage | ensure | make sure |
| numerous, myriad, plethora | many | a wide range of | many, or name them |
| robust, seamless | strong, smooth | comprehensive | full |
| landscape, realm, tapestry | field, area; or cut | in order to | to |
| due to the fact that | because | prior to / subsequent to | before / after |
| a number of | some, several | has the ability to | can |
| in the event that | if | with regard to | about |
| it is important to note that | (delete) | plays a key role in | (say what it does) |

## Plain-writing rules (ASD-STE100, condensed)

Always useful; mandatory in the `technical` profile.

1. One word, one meaning. Pick a term and repeat it; do not rotate synonyms.
2. Use the simple verb: "decide", not "make a decision".
3. Sentences: at most 25 words (20 for procedure steps).
4. One instruction per sentence. Write steps as commands: "Remove the cover."
5. Paragraphs: at most 6 sentences, one topic, topic sentence first.
6. Active voice. Name the actor. Use passive only when the actor is unknown or unimportant.
7. Keep articles (a, the, this). Do not drop words to save space.
8. Repeat the noun when a pronoun could point to two things.
9. Noun strings: at most 3 nouns together; break longer ones with "of" or "for".
10. Simple tenses. Avoid -ing forms where a simple verb works.
11. Use "must" for required, "can" for possible. Avoid should, might, could, may.
12. Put warnings first, in their own sentence.
13. Numerals with units, formatted the same way each time.
14. No idioms, slang or figurative language. No contractions or semicolons in technical text.

`prose` and `formal` profiles keep rules 1 to 8 and relax 9 to 14: contractions are fine in prose.

## Voice

If the user gives a sample of their own writing, match its sentence length, person (I, we, you), contraction use and punctuation. Without a sample, write in the register of the profile: plain and direct for prose, neutral and precise for formal. Never invent personality.

## Example (prose)

Before:
> In today's fast-paced digital world, social media has fundamentally transformed how people consume news. It serves as a powerful platform that enhances access, fosters engagement, and amplifies diverse voices. Ultimately, understanding these dynamics is crucial for navigating the modern information landscape.

After:
> Social media changed how people get the news. Access is faster, and more voices reach readers. Anyone who reads news this way should know how a feed decides what to show.

The after-text keeps the source's claims (news now arrives through social media, access improved, more voices, readers need to understand the mechanism) and adds no outside fact. Texts this short only exercise the lexical signals; judge rhythm on full documents.
