"""French AI-fingerprint lexicon for humanize_check.py.

French LLM prose differs from English: heavy connector use (En effet, Ainsi,
Par ailleurs), elegant-variation synonym rotation, Title Case headings copied
from English, the "non seulement ... mais aussi" frame, and the "Plongeons /
Explorons" inclusive imperative. Regexes run case-insensitively on text where
curly apostrophes are normalised to a straight apostrophe.
"""

VOCAB = [
    (r"plong(?:eons|er|ez|e|ent) (?:dans|au c)", 1, "entrez dans le sujet directement"),
    (r"explorons|décortiquons|démystifions|dévoilons|découvrons ensemble", 1, "commencez par le contenu"),
    (r"incontournables?", 1, "important / courant"),
    (r"foisonn\w+", 1, "nombreux"),
    (r"tapisseries?", 1, "ensemble / histoire"),
    (r"kaléidoscope|mosaïque|symphonie|palette de", 1, "dites le fait concret"),
    (r"pléthore|myriade", 1, "beaucoup de"),
    (r"panoplie", 1, "série / ensemble"),
    (r"crucial(?:e|es|aux|ement)?", 1, "important / nécessaire"),
    (r"pierres? angulaires?", 1, "base / cœur"),
    (r"synerg\w+", 1, "cut"),
    (r"holisti\w+", 1, "global"),
    (r"paradigmes?", 1, "modèle / approche"),
    (r"révolutionn\w+", 1, "changer (dites ce qui change)"),
    (r"transformateurs?|transformatrices?", 1, "dites ce qui change"),
    (r"mett(?:re|ent|ons|ant) en lumière|mis(?:e|es)? en lumière|met en lumière", 1, "montrer"),
    (r"témoign(?:e|ent|er|ant|age)s? de", 1, "montrer / prouver"),
    (r"inestimables?|inégalé\w*|inouï\w*", 1, "cut"),
    (r"captivant\w*|époustouflant\w*|à couper le souffle|fascinant\w*", 1, "cut"),
    (r"multifacett\w+|multidimensionnel\w*", 1, "complexe (nommez les facettes)"),
    (r"façonn(?:er|e|ent|ons|ant)", 1, "influencer / former"),
    (r"exploit(?:er|e|ons|ant) (?:tout )?le potentiel|libér(?:er|ez) le potentiel", 1, "cut"),
    (r"naviguer (?:dans|à travers|entre)", 1, "se repérer / gérer"),
    (r"en constante évolution|en perpétuelle (?:évolution|mutation)|en pleine mutation", 1, "cut"),
    (r"écosystèmes?", 2, "milieu / ensemble"),
    (r"leviers?", 2, "moyen"),
    (r"essentiel(?:le|les|s)?", 2, "nécessaire / important"),
    (r"primordial\w*", 2, "important"),
    (r"indispensables?", 2, "nécessaire"),
    (r"véritables?", 2, "cut"),
    (r"précieu(?:x|se|ses)", 2, "utile"),
    (r"remarquables?", 2, "cut / précisez"),
    (r"innovan(?:t|te|ts|tes)|novateurs?|novatrices?", 2, "nouveau"),
    (r"robustes?", 2, "solide / fiable"),
    (r"fluides?", 2, "simple"),
    (r"optimis(?:er|e|ons|ation)", 2, "améliorer"),
    (r"faciliter|facilite|facilitent|facilitant", 2, "aider / permettre"),
    (r"tir(?:er|e|ons|ant) (?:parti|profit)", 2, "utiliser"),
    (r"s'avér(?:er|e|ent|ant|é)", 2, "être / se révéler"),
    (r"souligne(?:r|nt|z|ons)?|soulignant", 2, "montrer / dire"),
    (r"mett(?:re|ent|ons|ant) en avant|met en avant", 2, "montrer"),
    (r"renforc(?:er|e|ent|ons|ant)", 2, "renforcer (précisez comment)"),
    (r"enjeux?", 2, "ce qui est en jeu / risque"),
    (r"paysages?", 2, "domaine / situation"),
    (r"facettes?", 2, "aspect"),
    (r"nuanc(?:é|ée|és|ées|er|e|es)", 2, "précisez"),
    (r"intrinsèques?|inhérent\w*|sous-jacent\w*", 2, "propre à / de fond"),
    (r"incarn(?:er|e|ent|ons)", 2, "être / représenter"),
    (r"s'inscri(?:t|vent|re|vant)", 2, "faire partie de"),
    (r"au c(?:œ|oe)ur de", 2, "dans / au centre de"),
    (r"jalons?", 2, "étape"),
    (r"richesses?", 2, "cut / précisez"),
    (r"notamment", 2, "par exemple / cut"),
    (r"d'ailleurs", 2, "cut"),
    (r"dynamiques?", 2, "changeant"),
    (r"cultiv(?:er|e|ons|ant)", 2, "développer"),
    (r"approfond(?:ir|issons|issez|issement)", 2, "creuser / détailler"),
    (r"en son sein", 2, "dedans"),
]

PHRASES = [
    (r"\bil (?:est|sera) (?:important|essentiel|crucial|primordial|capital|nécessaire|intéressant|utile) (?:de|d')\s?(?:noter|souligner|rappeler|mentionner|préciser|retenir|garder en tête|comprendre|reconnaître)", 1.0, "supprimez l'amorce; dites le fait"),
    (r"\bil (?:convient|importe|faut) (?:de |d')?(?:noter|souligner|rappeler|mentionner|préciser|retenir|garder en tête)", 1.0, "supprimez l'amorce; dites le fait"),
    (r"\bil est à (?:noter|souligner|rappeler|préciser)", 1.0, "supprimez l'amorce; dites le fait"),
    (r"\bforce est de constater", 1.0, "constatez directement"),
    (r"\bil va sans dire", 1.0, "supprimez"),
    (r"\bn'hésitez pas à\b", 0.5, "formule d'assistant : supprimez ou précisez"),
    (r"\bà bien des égards", 1.0, "supprimez ou précisez lesquels"),
    (r"\bdans (?:le|un) monde (?:actuel|moderne|numérique|d'aujourd'hui|en constante|en perpétuel|où|de plus en plus)", 1.0, "supprimez l'ouverture"),
    (r"\bà l'ère (?:du|de la|de l'|des|numérique|moderne|actuelle)", 1.0, "supprimez l'ouverture"),
    (r"\bà l'heure (?:où|actuelle|du numérique)", 1.0, "supprimez l'ouverture"),
    (r"\bdans un (?:contexte|paysage|environnement|monde) (?:en constante|en perpétuelle|en pleine|de plus en plus)", 1.0, "supprimez l'ouverture"),
    (r"\bde nos jours\b", 0.5, "aujourd'hui / supprimez"),
    (r"\bjou(?:e|ent|er|ant|era|eront) un rôle (?:\w+ )?(?:clé|crucial|essentiel|central|majeur|fondamental|déterminant|important|primordial|capital)", 1.0, "dites ce que ça fait"),
    (r"\bun rôle (?:clé|crucial|essentiel|central|majeur|fondamental|déterminant|primordial)", 1.0, "dites ce que ça fait"),
    (r"\bn'est pas (?:seulement|uniquement|simplement|juste|qu')[^.!?]{2,80}?(?:mais|c'est)", 1.0, "dites le point directement"),
    (r"\bnon seulement\b[^.!?]{3,100}?\bmais (?:aussi|également|encore)", 1.0, "dites les deux points simplement"),
    (r"\bce n'est pas [^.;:!?]{2,60}[,;:] c'est", 1.0, "dites le point; supprimez l'homme de paille"),
    (r"\b(?:bien )?plus qu'(?:un|une) (?:simple|seul)", 1.0, "dites ce que c'est"),
    (r"\bque vous soyez [^.!?]{2,60}\bou\b", 1.0, "supprimez le balayage de public"),
    (r"\bjette(?:r|nt|z)? (?:un )?(?:nouvel )?(?:éclairage|jour)|\bjette de la lumière", 1.0, "expliquer / montrer"),
    (r"\bouvr(?:e|ent|ant|ir) la voie", 1.0, "permettre / mener à"),
    (r"\bun équilibre (?:délicat|subtil|fragile)", 1.0, "dites ce qui s'oppose à quoi"),
    (r"\bà la croisée (?:des chemins|de)", 1.0, "entre"),
    (r"\bun véritable (?:tournant|catalyseur|trésor|joyau|atout|moteur|casse-tête)", 1.0, "dites ce que c'est"),
    (r"\b(?:un|une) (?:riche|vaste|large|grand) (?:éventail|gamme|panoplie|palette|diversité|choix) (?:de|d')", 1.0, "nommez-les"),
    (r"\bune (?:multitude|myriade|pléthore) (?:de|d')", 1.0, "beaucoup de / nommez-les"),
    (r"\bl'avenir (?:s'annonce|est prometteur|semble prometteur|reste à écrire)|promet d'être", 1.0, "finissez sur le dernier fait"),
    (r"\bseul l'avenir|reste à voir", 1.0, "supprimez"),
    (r"\bmalgré ces (?:défis|obstacles|difficultés|limites)|\bface à ces (?:défis|enjeux)", 1.0, "nommez la difficulté ou supprimez"),
    (r"\bdans cet article,? nous|\bnous allons (?:explorer|découvrir|voir|examiner|plonger|décortiquer)", 1.0, "commencez par le contenu"),
    (r"\ben fin de compte\b|\bau final\b", 0.5, "supprimez"),
    (r"\bs'imposer comme|\bse positionner comme", 0.5, "devenir / être"),
    (r"\bau fil (?:des|du|de) ", 0.5, "avec le temps / supprimez"),
    (r"\bau service de\b|\ben quête de\b", 0.5, "pour / cherche"),
    (r"\btout au long de\b", 0.25, "pendant"),
    (r"\b(?:afin de|en vue de|dans le but de)\b", 0.5, "pour"),
    (r"\ben ce qui concerne\b|\bau niveau de\b|\bdans le cadre de\b", 0.5, "pour / sur / dans"),
    (r"\bpar le biais de\b|\bgrâce à la mise en œuvre", 0.5, "par / avec"),
    (r"\bpeu(?:t|vent) (?:potentiellement|éventuellement)\b|\bpourrait potentiellement\b", 1.0, "un seul adoucisseur"),
    (r"\b(?:véritablement|réellement|véritable|absolument|incroyablement|extrêmement|profondément)\b", 0.25, "supprimez l'intensif"),
    (r"\bla clé (?:de|pour|du)\b", 0.5, "dites le moyen précis"),
    (r"\bun(?:e)? (?:des|l'un des|l'une des) (?:plus|principaux|principales|meilleur)", 0.5, "dites pourquoi"),
]

NEGATIVE_PARALLELISM = [
    r"\bce n'est pas (?:seulement |uniquement |simplement |juste )?[^.;:!?]{2,70}[,;:] ?c'est\b",
    r"\bne (?:s'agit|se limite|consiste) pas (?:seulement |uniquement |simplement |juste )?[^.;:!?]{2,70}[,;] mais\b",
    r"\bn'est pas (?:seulement |uniquement |simplement |juste )?[^.;:!?]{2,70}, (?:mais|c'est)\b",
    r"\bnon seulement [^.!?]{2,90}?\bmais (?:aussi|également|encore|surtout)\b",
    r"\bpas (?:seulement|uniquement|simplement) [^.!?]{2,80}?\bmais (?:aussi|également|encore|surtout)\b",
]

PARTICIPLE_RIDERS = (
    r",\s+(?:soulignant|mettant en (?:lumière|avant|évidence)|illustrant|reflétant|témoignant|"
    r"garantissant|assurant|favorisant|contribuant à|renforçant|permettant de|offrant|"
    r"incarnant|symbolisant|démontrant|façonnant|ouvrant la voie|consolidant|cimentant)\b"
)

COPULA_AVOIDANCE = (
    r"\b(?:sert|servent|servait) de\b|\b(?:s'impose|s'imposent|se présente|se révèle|s'érige|se dresse) comme\b|"
    r"\b(?:constitue|constituent|représente|représentent|incarne|incarnent) (?:un|une|le|la|l'|les)\b|"
    r"\b(?:peut|peuvent|permet|permettent) (?:ainsi )?de (?:mettre|faire|jouer)\b"
)

TRANSITIONS = (
    r"en effet|ainsi|par ailleurs|de plus|en outre|de surcroît|par conséquent|dès lors|d'autre part|"
    r"en définitive|en résumé|en somme|en conclusion|pour conclure|à cet égard|dans cette optique|"
    r"dans cette perspective|dans ce contexte|dans ce cadre|à cet effet|il convient de|il est à noter|"
    r"force est de|notamment|enfin|toutefois|néanmoins|cependant|globalement|finalement"
)

CLOSERS = (
    r"^\W*(?:en conclusion|en résumé|en somme|pour conclure|pour résumer|en définitive|"
    r"somme toute|au final|en fin de compte|globalement|finalement|l'avenir|"
    r"que vous soyez|en comprenant|en adoptant)\b"
)

CHATBOT_RESIDUE = [
    r"\bj'espère que (?:cela|ça|cette réponse|ces informations) (?:vous )?(?:aide|vous aide|vous sera utile|vous est utile)",
    r"(?:\A|\n\s*\n)\W*(?:excellente|bonne|très bonne) question\s?[!.,]",
    r"(?:\A|\n\s*\n)\W*(?:bien sûr|absolument|certainement|avec plaisir|volontiers)\s?!",
    r"\bvoici (?:une|la|votre|ma) (?:version|réponse|reformulation|synthèse|proposition|réécriture)\b",
    r"\ben tant qu'(?:IA|intelligence artificielle|assistant|modèle)",
    r"\bsouhaitez-vous que je\b|\bvoulez-vous que je\b|\bje peux (?:aussi |également )?vous aider",
    r"\bmes (?:connaissances|données d'entraînement) (?:s'arrêtent|datent)|\bdate limite de (?:mes )?connaissances",
    r"\boaicite\b|\bcontentReference\b|utm_source=(?:chatgpt|openai|copilot|claude)|:::writing|\bgrok_card\b|\battached_file\b|ppl-ai-file-upload",
    r"\[cite:? ?\d|turn\d+(?:search|view|news)\d+|||",
    r"\[(?:Votre|Vos|Insérer|Entreprise|Destinataire|Client|Date|Titre|Nom)[^\]\n]{0,40}\]",
]

ABBREVIATIONS = [
    "m", "mme", "mmes", "mlle", "dr", "pr", "prof", "st", "ste", "cf", "etc", "p", "pp", "vol", "éd",
    "n°", "no", "av", "apr", "j-c", "env", "max", "min", "fig", "tel", "c.-à-d", "ex", "art", "al",
]

TRIAD_CONJUNCTION = r"(?:et|ou)"

# --- langage clair / FALC condensed (French analogue of ASD-STE100) ---
PLAIN = {
    "passive": r"\b(?:est|sont|était|étaient|a été|ont été|sera|seront|serait|seraient|fut|furent)\s+(?:\w+ment\s+)?\w+(?:é|ée|és|ées)\b",
    "passive_stop": {"été", "aimé", "armée", "année", "idée", "arrivée", "fée", "pensée", "journée", "entrée", "soirée", "durée", "assemblée"},
    "contraction": None,
    "modal": r"\b(?:devrait|devraient|pourrait|pourraient|saurait)\b",
    "nominal": [
        (r"\b(?:procéd(?:er|ons|e|ent|é)|effectu(?:er|e|ent|ons|é)|réalis(?:er|e|ent|ons|é)) (?:à )?(?:une?|la|le|l'|des|les|au|aux)\b",
         "utilisez le verbe simple (procéder à la vérification -> vérifier)"),
        (r"\b(?:est|sont) (?:en mesure|susceptibles?) (?:de|d')", "pouvoir / direct verb"),
        (r"\b(?:dans l'éventualité où|dans le cas où|au cas où|à condition que)\b", "si"),
        (r"\b(?:utiliser|employer) (?:afin|pour)\b|\bpréalablement à\b|\bpostérieurement à\b|\bantérieurement à\b", "avant / après / pour"),
    ],
    "max_sentence_words": 25,
    "max_step_words": 20,
    "max_paragraph_sentences": 6,
}

TRIAD_RAMP = (0.15, 0.40)
HEDGES = r"\b(?:peut|peuvent|pourrait|pourraient|souvent|généralement|habituellement|permet|permettent|tend à|tendent à|potentiellement|probablement)\b"
PERSON = r"\b(?:je|j'|me|m'|moi|mon|ma|mes|nous|notre|nos|vous|votre|vos|tu|ton|ta|tes)\b"
CONTRACTION = None
