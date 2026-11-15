# Humanizer : français

Sources : Wikipédia « Signs of AI writing » (transposé), blader/humanizer, stop-slop, ASD-STE100 adapté au langage clair / FALC, mesures sur le corpus du vérificateur. Le français IA a ses propres tics : il ne se traduit pas mot à mot depuis l'anglais.

## Ce que fait la prose machine

- **Rythme régulier.** Phrases de longueur voisine, paragraphes de même taille, chacun ouvert par une phrase-thèse.
- **Contenu générique.** Peu de noms, de chiffres, de dates, de lieux ; ni « je », ni questions.
- **Connecteurs en rafale.** En effet, Ainsi, Par ailleurs, De plus, En outre, Notamment, Enfin au début des phrases.
- **Ouvertures et clôtures de cérémonie.** « Dans un monde en constante évolution », « À l'ère du numérique » ; « En somme », « En conclusion », « L'avenir s'annonce prometteur ».
- **Amorces creuses.** « Il est important de noter que », « Il convient de souligner », « Force est de constater ».
- **Cadres d'opposition.** « Non seulement… mais aussi », « Ce n'est pas X, c'est Y », « Bien plus qu'un simple… ».
- **Triades.** Trois noms, trois adjectifs, trois verbes, partout.
- **Variation élégante.** Un synonyme différent à chaque reprise (« l'application », « l'outil », « la solution », « la plateforme »), réflexe scolaire que l'IA amplifie.
- **Adoucisseurs.** peut, permet de, souvent, généralement dans la moitié des phrases.
- **Décor.** Gras, emojis, titres en Title Case (calque de l'anglais ; en français, casse de phrase), puces « **Libellé :** texte ».
- **Appels à l'inclusion.** « Plongeons dans », « Explorons », « Décortiquons ».

## Réécrire en quatre passes

1. **Contenu.** Commencer par l'affirmation principale. Supprimer l'ouverture qui annonce le sujet et la clôture qui le répète. Garder tous les faits. Mettre les noms, chiffres, exemples de la source là où le texte est vague ; s'il n'y en a pas, couper la phrase vague.
2. **Structure.** Rendre les paragraphes inégaux : en fusionner deux, isoler une idée en une phrase, laisser un paragraphe s'étendre. Remplacer les « trois avantages » par le nombre réel. Retirer les intertitres inutiles.
3. **Phrases.** Alterner longueurs : à côté d'une phrase de 25 mots, une de 4 à 8. Varier le sujet de phrases consécutives. Remplacer les gérondifs et participes finaux (« …, soulignant ainsi… ») et les cadres « non seulement… mais » par l'affirmation elle-même. Écrire « est », « a », « sont » plutôt que « constitue », « représente », « se positionne comme ». Affirmer ce qui est établi ; garder une réserve seulement quand la source doute.
4. **Mots.** Voir le tableau. **Répéter le mot juste** plutôt que chercher un synonyme : en français technique ou courant, la répétition est correcte. Guillemets « » avec espaces insécables si le document les utilise déjà ; casse de phrase pour les titres.
   Les vraies triades de la source restent ; écrire « X et Y, ainsi que Z » ou donner le troisième élément à sa propre phrase, sans répéter la même amorce (« Il faut… ») d'une phrase à l'autre.

## Mots simples

| Éviter | Préférer | Éviter | Préférer |
|---|---|---|---|
| crucial, primordial, essentiel | important, nécessaire | mettre en lumière | montrer |
| témoigner de | montrer, prouver | faciliter | aider, permettre |
| optimiser | améliorer | révolutionner, bouleverser | changer (dire quoi) |
| exploiter le potentiel | utiliser | tirer parti de | utiliser |
| pierre angulaire | base | incontournable | courant, important |
| façonner | influencer, former | un éventail de, une panoplie de | plusieurs ; ou les nommer |
| une multitude de, une myriade de | beaucoup de | enjeux | risques, ce qui est en jeu |
| afin de, en vue de, dans le but de | pour | en ce qui concerne, au niveau de | pour, sur |
| par le biais de | par, avec | à l'heure actuelle | aujourd'hui |
| il est important de noter que | (supprimer) | joue un rôle clé dans | (dire ce que ça fait) |
| au cœur de, au service de | dans, pour | s'inscrit dans | fait partie de |

## Règles de langage clair (équivalent condensé d'ASD-STE100)

Toujours utiles ; obligatoires dans le profil `technical`.

1. Un mot, un sens. Garder le même terme dans tout le texte (voir variation élégante).
2. Verbe simple : « vérifier », pas « procéder à la vérification de » ; « décider », pas « prendre la décision de ».
3. Phrases : 25 mots au plus (20 pour une étape de procédure).
4. Une consigne par phrase. Étapes à l'impératif ou à l'infinitif : « Retirez le capot. »
5. Paragraphes : 6 phrases au plus, un seul sujet, idée principale en premier.
6. Voix active. Nommer l'acteur. Passif seulement si l'acteur est inconnu ou sans intérêt.
7. Répéter le nom quand un pronom (il, celui-ci, ce dernier) peut désigner deux choses.
8. Éviter les nominalisations en cascade et les chaînes « de… de… de… ».
9. Temps simples : présent, passé composé, futur. Éviter le subjonctif imparfait et le conditionnel de politesse.
10. « Doit » pour l'obligation, « peut » pour la possibilité. Éviter « devrait », « pourrait ».
11. Avertissements en premier, dans une phrase à part.
12. Chiffres avec unités, écrits toujours de la même façon (espace insécable avant l'unité).
13. Pas d'expression imagée, d'argot, de calque de l'anglais.
14. Pas de point-virgule dans un texte technique.

Les profils `prose` et `formal` gardent 1 à 7 et assouplissent le reste.

## Voix

Si l'utilisateur fournit un échantillon de son écriture, suivre sa longueur de phrase, sa personne (je, nous, vous), son choix tu/vous et sa ponctuation. Sinon, rester dans le registre du profil. Ne jamais inventer de personnalité. Garder le choix tu/vous du texte source.

## Exemple (prose)

Avant :
> Dans un monde en constante évolution, les réseaux sociaux ont profondément transformé notre manière de consommer l'actualité. Ils constituent une plateforme essentielle qui facilite l'accès, favorise l'engagement et met en lumière des voix diverses. En somme, comprendre ces dynamiques est crucial pour naviguer dans le paysage informationnel actuel.

Après :
> Les réseaux sociaux ont changé la façon dont on s'informe. L'accès est plus rapide, et davantage de voix atteignent les lecteurs. Qui lit l'actualité ainsi doit savoir comment un fil choisit ce qu'il montre.

Le texte réécrit garde les affirmations de la source et n'ajoute aucun fait extérieur. Un texte aussi court n'exerce que les signaux lexicaux ; juger le rythme sur des documents complets.
