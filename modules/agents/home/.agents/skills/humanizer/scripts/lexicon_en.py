"""English AI-fingerprint lexicon for humanize_check.py.

Sources: Wikipedia "Signs of AI writing" (vocabulary by era, structural tells),
blader/humanizer pattern catalogue, hardikpandya/stop-slop rubric, own corpus
calibration. Every regex is matched case-insensitively on text where curly
apostrophes are already normalised to a straight apostrophe.
"""

# (regex, tier, plain replacement or None)
# tier 1 = strong tell. tier 2 = common word that only counts in volume/clusters.
VOCAB = [
    (r"delv(?:e|es|ed|ing)", 1, "look at / dig into"),
    (r"tapestr(?:y|ies)", 1, "mix / history"),
    (r"testaments?", 1, "proof / sign"),
    (r"pivotal", 1, "important / key"),
    (r"underscor(?:e|es|ed|ing)", 1, "show"),
    (r"intricac(?:y|ies)", 1, "detail"),
    (r"intricate", 1, "complex / detailed"),
    (r"interplay", 1, "relationship"),
    (r"meticulous(?:ly)?", 1, "careful(ly)"),
    (r"showcas(?:e|es|ed|ing)", 1, "show"),
    (r"vibrant", 1, "lively / busy (or cut)"),
    (r"bolster(?:s|ed|ing)?", 1, "strengthen / back"),
    (r"garner(?:s|ed|ing)?", 1, "get / win"),
    (r"multifaceted", 1, "complex (or name the facets)"),
    (r"myriad", 1, "many"),
    (r"plethora", 1, "many"),
    (r"realms?", 1, "area / field"),
    (r"ever-(?:evolving|changing)", 1, "cut"),
    (r"foster(?:s|ed|ing)?", 1, "encourage / build"),
    (r"leverag(?:e|es|ed|ing)", 1, "use"),
    (r"harness(?:es|ed|ing)?", 1, "use"),
    (r"elevat(?:e|es|ed|ing)", 1, "raise / improve"),
    (r"seamless(?:ly)?", 1, "smooth(ly) (or cut)"),
    (r"holistic(?:ally)?", 1, "whole / overall"),
    (r"paradigms?", 1, "model / approach"),
    (r"synerg(?:y|ies|istic)", 1, "cut"),
    (r"cutting-edge", 1, "new / latest"),
    (r"game-?chang(?:er|ers|ing)", 1, "say what changed"),
    (r"groundbreaking", 1, "new (or say what is new)"),
    (r"transformative", 1, "say what changed"),
    (r"crucial(?:ly)?", 1, "important / needed"),
    (r"enhanc(?:e|es|ed|ing|ement|ements)", 1, "improve"),
    (r"streamlin(?:e|es|ed|ing)", 1, "simplify"),
    (r"utili[sz](?:e|es|ed|ing|ation)", 1, "use"),
    (r"facilitat(?:e|es|ed|ing)", 1, "help / allow"),
    (r"commenc(?:e|es|ed|ing)", 1, "start"),
    (r"endeavou?rs?", 1, "try / effort"),
    (r"nestled", 1, "is / sits"),
    (r"boasts?", 1, "has"),
    (r"renowned", 1, "known (or say for what)"),
    (r"breathtaking", 1, "cut"),
    (r"stunning", 1, "cut"),
    (r"indelible", 1, "lasting (or cut)"),
    (r"enduring", 1, "lasting"),
    (r"resonat(?:e|es|ed|ing)", 1, "matter to / appeal to"),
    (r"beacons?", 1, "example / guide"),
    (r"cornerstones?", 1, "base / core"),
    (r"linchpins?", 1, "key part"),
    (r"navigat(?:e|es|ed|ing) the", 1, "handle / deal with"),
    (r"embark(?:s|ed|ing)?", 1, "start"),
    (r"unlock(?:s|ed|ing)? (?:the )?(?:full )?(?:potential|power|secrets?)", 1, "cut"),
    (r"valuable insights?", 1, "say what was learned"),
    (r"encompass(?:es|ed|ing)", 1, "include / cover"),
    (r"garnering", 1, "getting"),
    (r"ensuring", 2, "so that / making sure"),
    (r"comprehensive(?:ly)?", 2, "full / complete"),
    (r"robust", 2, "strong / reliable"),
    (r"nuanced?", 2, "subtle (or say how)"),
    (r"essential(?:ly)?", 2, "needed"),
    (r"vital(?:ly)?", 2, "needed / important"),
    (r"key", 2, "main"),
    (r"landscape", 2, "field / market / situation"),
    (r"journey", 2, "process / path"),
    (r"valuable", 2, "useful"),
    (r"significant(?:ly)?", 2, "big / large (or give the number)"),
    (r"overall", 2, "cut"),
    (r"additionally", 2, "also / cut"),
    (r"furthermore", 2, "also / cut"),
    (r"moreover", 2, "also / cut"),
    (r"notably", 2, "cut"),
    (r"prioriti[sz](?:e|es|ed|ing)", 2, "put first"),
    (r"dependable", 2, "reliable"),
    (r"optimal(?:ly)?", 2, "best"),
    (r"innovative", 2, "new"),
    (r"dynamic", 2, "changing"),
    (r"approach(?:es)? to", 2, "way to"),
    (r"fundamental(?:ly)?", 2, "basic"),
    (r"emphasi[sz](?:e|es|ed|ing)", 2, "stress / show"),
    (r"highlight(?:s|ed|ing)", 2, "show / point out"),
    (r"demonstrat(?:e|es|ed|ing)", 2, "show"),
    (r"ensure[sd]?", 2, "make sure"),
    (r"individuals", 2, "people"),
    (r"numerous", 2, "many"),
    (r"diverse", 2, "varied (or name the variety)"),
    (r"profound(?:ly)?", 2, "deep / big"),
    (r"cultivat(?:e|es|ed|ing)", 2, "grow / build"),
]

# (regex, weight, hint). weight 1.0 strong, 0.5 weak.
PHRASES = [
    (r"\bit(?: is|'s) (?:important|worth|crucial|essential|vital|critical|key) to (?:note|remember|mention|understand|recognize|recognise|keep in mind|consider)", 1.0, "delete the opener; state the fact"),
    (r"\bit(?: is|'s) worth (?:noting|mentioning|pointing out)", 1.0, "delete the opener; state the fact"),
    (r"\bin today(?:'s)? (?:fast-paced|digital|modern|ever-changing|rapidly evolving|competitive|interconnected|connected)? ?(?:world|landscape|era|age|environment|society)", 1.0, "cut the scene-setting opener"),
    (r"\bin (?:the|an) (?:ever-)?(?:evolving|changing|dynamic|digital|modern) (?:world|landscape|era|age|realm) of", 1.0, "name the topic directly"),
    (r"\b(?:let's|let us) (?:dive|delve|explore|unpack|break|take a (?:closer )?look)", 1.0, "start with the content"),
    (r"\bdive (?:deep|deeper|right) (?:in|into)", 1.0, "start with the content"),
    (r"\bhere(?:'s| is) (?:the thing|what you need to know|the kicker|the catch|why|how|what)\b", 0.5, "cut the run-up"),
    (r"\blet me know if (?:you|there)\b", 0.5, "assistant-style sign-off: cut or make it specific"),
    (r"\bfeel free to (?:ask|reach|let|contact|modify|adjust|tweak)\b", 0.5, "assistant-style sign-off: cut or make it specific"),
    (r"\bat the end of the day", 1.0, "cut"),
    (r"\bwhen it comes to\b", 0.5, "about / for"),
    (r"\bplays? an? (?:crucial|vital|key|pivotal|significant|important|critical|central|major) role", 1.0, "say what it does"),
    (r"\ba testament to\b", 1.0, "shows / proves"),
    (r"\b(?:serves|stands) as an? (?:reminder|testament|beacon|example|symbol|cornerstone)", 1.0, "is"),
    (r"\bnot only\b[^.!?]{3,80}\bbut also\b", 1.0, "state both points plainly"),
    (r"\bwhether you(?:'re| are) (?:a |an )?[^.!?]{2,60}\bor\b", 1.0, "cut the audience sweep"),
    (r"\blook no further\b", 1.0, "cut"),
    (r"\btake (?:your|it|this)[^.!?]{0,30}to the next level", 1.0, "cut"),
    (r"\bnavigat(?:e|ing) the (?:complex|complexit|landscape|challenges|world|ever)", 1.0, "handle / deal with"),
    (r"\ba (?:wide|broad|diverse|vast|rich) (?:range|array|variety|spectrum|selection) of\b", 1.0, "many / name them"),
    (r"\b(?:has|have) (?:become|emerged as) (?:increasingly|a (?:crucial|vital|key|major|powerful))", 1.0, "say when and how"),
    (r"\bcontinues? to (?:evolve|grow|shape|play|thrive)", 0.5, "cut or give evidence"),
    (r"\bpav(?:e|es|ed|ing) the way\b", 1.0, "led to / allowed"),
    (r"\bshed(?:s|ding)? light on\b", 1.0, "explain / show"),
    (r"\b(?:rich|vibrant) (?:tapestry|history|culture|heritage|cultural)", 1.0, "give one concrete fact"),
    (r"\bdeeply rooted\b", 1.0, "cut"),
    (r"\bthe (?:power|magic|beauty|art) of\b", 0.5, "cut"),
    (r"\bin the realm of\b", 1.0, "in"),
    (r"\bthe intersection of\b", 1.0, "between"),
    (r"\b(?:striking|find(?:ing)?|maintain(?:ing)?) (?:a|the) (?:delicate|careful|right|healthy|fine) balance", 1.0, "say what trades against what"),
    (r"\bmore than (?:just|merely|simply) (?:a|an|the)\b", 1.0, "state what it is"),
    (r"\bit(?:'s| is) not (?:just|only|merely|simply)\b", 1.0, "state the point; drop the strawman"),
    (r"\bthis (?:isn't|is not|doesn't|does not) (?:just|only|merely|simply|about)\b", 1.0, "state the point; drop the strawman"),
    (r"\b(?:the )?key takeaways?\b", 1.0, "cut or say the point"),
    (r"\bmoving forward\b|\bgoing forward\b", 0.5, "cut"),
    (r"\bit goes without saying\b|\bneedless to say\b", 1.0, "cut"),
    (r"\bonly time will tell\b|\bremains to be seen\b", 1.0, "cut"),
    (r"\bthe future (?:looks|is|remains) (?:bright|promising)", 1.0, "end on the last fact"),
    (r"\bexciting (?:times|possibilities|opportunities|developments)", 1.0, "cut"),
    (r"\bdespite (?:these|its|the) (?:challenges|obstacles|limitations|setbacks)", 1.0, "name the challenge or cut"),
    (r"\bin (?:this|the following) (?:article|post|guide|section|blog),? we(?: will|'ll)?", 1.0, "start with the content"),
    (r"\bthis (?:article|guide|post|section) (?:will|aims to|explores|examines|provides|offers)", 1.0, "start with the content"),
    (r"\bdue to the fact that\b", 0.5, "because"),
    (r"\bin order to\b", 0.5, "to"),
    (r"\bprior to\b", 0.5, "before"),
    (r"\ba number of\b", 0.5, "some / several"),
    (r"\bhas the ability to\b|\bis able to\b", 0.5, "can"),
    (r"\bat this point in time\b", 1.0, "now"),
    (r"\bwith regard to\b|\bin terms of\b", 0.5, "about / for"),
    (r"\bcould potentially\b|\bmight potentially\b|\bcan potentially\b|\bmay potentially\b", 1.0, "pick one hedge"),
    (r"\b(?:can|could|may|might) (?:help|serve|play|contribute)\b", 0.5, "say what it does"),
    (r"\bthe (?:world|realm|landscape) of\b", 0.5, "cut"),
    (r"\bone of the (?:most|key|main|leading|best)\b", 0.5, "name why"),
    (r"\bfrom [^.!?]{2,40} to [^.!?]{2,40}, (?:and everything in between|from)", 1.0, "cut the false range"),
    (r"\band everything in between\b", 1.0, "cut"),
    (r"\b(?:ultimately|in essence|at its core|at the heart of)\b", 0.5, "cut"),
    (r"\bthe real (?:question|challenge|problem|magic|win|story)\b", 0.5, "state the point"),
    (r"\b(?:genuinely|truly|really|incredibly|remarkably|undeniably|undoubtedly)\b", 0.25, "cut the intensifier"),
]

NEGATIVE_PARALLELISM = [
    r"\b(?:it|this|that)(?: is|'s) not (?:just |only |merely |simply )?(?:about )?[^.;:!?]{2,70}[,;:] ?(?:it|this|that)(?: is|'s)\b",
    r"\b(?:it|this|that)(?: is|'s) not (?:just |only |merely |simply )?(?:about )?[^.;:!?]{2,70}, but\b",
    r"\bnot (?:just|only|merely|simply) [^.!?]{2,80}?\bbut (?:also )?",
    r"\bnot (?:a|an|the) [^.;:!?]{2,40}[,;] but (?:a|an|the)\b",
    r"\bisn't (?:just |only |merely |simply )?[^.;:!?]{2,60}[,;] it(?:'s| is)\b",
]

# participial riders glued to the end of a sentence
PARTICIPLE_RIDERS = (
    r",\s+(?:highlighting|underscoring|reflecting|showcasing|ensuring|fostering|contributing to|"
    r"emphasi[sz]ing|symboli[sz]ing|cultivating|demonstrating|illustrating|encompassing|"
    r"shaping|paving|enabling|allowing|solidifying|cementing|reinforcing|making it)\b"
)

COPULA_AVOIDANCE = (
    r"\b(?:serves|served|serving|stands|stood|functions|operates|represents|acts) as\b|"
    r"\b(?:boasts|boasted|features|featuring|offers|maintains) (?:a|an|the|over|more|\d)"
)

# sentence-initial additive/summarising transitions
TRANSITIONS = (
    r"additionally|moreover|furthermore|in addition|overall|in conclusion|in summary|to summari[sz]e|"
    r"ultimately|importantly|notably|consequently|as a result|in essence|essentially|"
    r"it is worth noting|it's worth noting|in today's|in the world of"
)

CLOSERS = (
    r"^\W*(?:in conclusion|in summary|to sum up|to summari[sz]e|overall|ultimately|in essence|"
    r"all in all|looking ahead|the future|as we (?:move|look)|by (?:understanding|embracing|following)|"
    r"whether you)\b"
)

# hard residue: never legitimate in finished prose
CHATBOT_RESIDUE = [
    r"\bI hope (?:this|that) (?:helps|email finds|is helpful)\b|\bhope that helps\b",
    r"(?:\A|\n\s*\n)\W*(?:great|good|excellent|fantastic) question[!.,]",
    r"(?:\A|\n\s*\n)\W*(?:certainly|absolutely|sure|of course)!",
    r"\bhere(?:'s| is) (?:a|an|the|your) (?:revised|updated|rewritten|improved|polished|draft|summary|breakdown|overview|version)\b",
    r"\bas an ai(?: language model)?\b",
    r"\bas of my (?:last|latest|knowledge)\b|\bmy (?:knowledge|training) cut-?off\b|\bup to my last (?:training )?update\b",
    r"\bI (?:don't|do not|cannot|can't) (?:browse|access (?:real-time|the internet|external|personal))",
    r"\bwould you like me to\b|\bshall I (?:also )?(?:expand|continue|elaborate|rewrite)\b",
    r"\bI can (?:also )?help (?:you )?(?:with|further)\b",
    r"\bdon't hesitate to\b",
    r"\boaicite\b|\bcontentReference\b|utm_source=(?:chatgpt|openai|copilot|claude)|:::writing|\bgrok_card\b|\battached_file\b|ppl-ai-file-upload",
    r"\[cite:? ?\d|turn\d+(?:search|view|news)\d+|||",
    r"\[(?:Your|Insert|Company|Recipient|Client|Date|Title|Name)[^\]\n]{0,40}\]",
]

ABBREVIATIONS = [
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "cf", "inc",
    "ltd", "co", "no", "fig", "eq", "approx", "dept", "est", "u.s", "u.k", "a.m", "p.m", "al",
]

TRIAD_CONJUNCTION = r"(?:and|or)"

# --- controlled-language ("plain") checks, ASD-STE100 condensed ---
PLAIN = {
    "passive": (
        r"\b(?:is|are|was|were|be|been|being)\s+(?:not\s+)?(?:\w+ly\s+)?"
        r"(?:\w+ed|made|done|shown|seen|given|known|taken|built|held|kept|put|run|set|sent|found|"
        r"written|chosen|driven|broken|stored|cut|left|lost|paid|read|told|thought|understood|"
        r"worn|torn|hidden|forgotten|gotten|begun)\b"
    ),
    "passive_stop": {"need", "needed", "red", "bed", "speed", "indeed", "hundred", "seed", "feed", "embed", "shed"},
    "contraction": r"\b(?:\w+n't|\w+'re|\w+'ve|\w+'ll|\w+'m|\w+'d|(?:it|that|there|here|what|he|she|let)'s)\b",
    "modal": r"\b(?:should|might|could|may|shall|ought)\b",
    "nominal": [
        (r"\b(?:make|take|give|perform|carry out|conduct|undertake) (?:a|an|the) (?:\w+ )?\w+(?:ion|ment|ance|ence|al|ing)\b",
         "use the verb itself (make a decision -> decide)"),
        (r"\b(?:is|are) (?:able|going|likely|used|designed) to\b", "use a direct verb (is able to -> can)"),
        (r"\b(?:in the event that|in the case that|on the condition that)\b", "if"),
        (r"\b(?:utili[sz]e|facilitate|commence|terminate|demonstrate|require|obtain|purchase|assist|"
         r"approximately|sufficient|additional|subsequent)\b", "use the simpler word"),
    ],
    "max_sentence_words": 25,
    "max_step_words": 20,
    "max_paragraph_sentences": 6,
}

TRIAD_RAMP = (0.08, 0.30)
HEDGES = r"\b(?:can|could|may|might|often|typically|generally|usually|tends? to|helps?|potentially|likely|perhaps|arguably)\b"
PERSON = r"\b(?:I|me|my|mine|we|our|ours|us|you|your|yours)\b|\bI'(?:m|ve|d|ll)\b"
CONTRACTION = r"\w'(?:t|s|re|ve|ll|d|m)\b"
