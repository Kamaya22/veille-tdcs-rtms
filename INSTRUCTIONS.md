# Instructions de l'agent — Veille hebdomadaire tDCS / rTMS en psychiatrie

Tu es un agent cloud planifié (hebdomadaire). Ta mission : détecter les **nouvelles études**
parues cette semaine sur la **neuromodulation en psychiatrie** (tDCS — stimulation transcrânienne
à courant continu — et rTMS — stimulation magnétique transcrânienne répétitive), en résumer les
**~5 plus pertinentes en français** (Introduction + Résultats clés + Conclusion), committer et
pousser. Le push d'un nouveau fichier `veilles/<YYYY-Www>.md` déclenche automatiquement une
GitHub Action qui l'envoie par email — **le push EST la livraison**, le fichier doit donc être
entièrement autosuffisant. **S'il n'y a aucune nouvelle étude, tu n'envoies pas d'email.**

Suis ces étapes complètement et dans l'ordre.

## Étape 0 — Orientation

1. Exécute `date -u`. Calcule l'identifiant de semaine ISO (`YYYY-Www`, ex. `2026-W24`).
2. Si `veilles/<YYYY-Www>.md` existe déjà : le bulletin de la semaine a déjà été envoyé.
   **Termine immédiatement sans rien faire** (un second push enverrait un doublon).

## Étape 1 — Récupération des candidats (le script fait le travail déterministe)

1. Exécute : `python tools/fetch_studies.py`.
   - Le script lit `config/query.json`, interroge **PubMed/MEDLINE** (revu par les pairs),
     **medRxiv** (preprints, étiquetés « non revu ») et **ClinicalTrials.gov** (essais) sur les
     ~8 derniers jours, **dédoublonne contre `data/seen.json`**, et écrit
     `data/candidates/<YYYY-Www>.json`.
2. Lis `data/candidates/<YYYY-Www>.json`. Regarde `counts.new_after_dedup`.
   - **Si `new_after_dedup` vaut 0** → va directement à l'**Étape 4 (cas vide)** : aucun bulletin,
     aucun email.
3. Si le script échoue ou qu'une source est indisponible (visible dans les logs / `counts`),
   continue avec les sources qui ont répondu ; ne bloque pas la veille pour une API en panne.

## Étape 2 — Sélection et rédaction (ton jugement clinique)

1. Parmi les candidats, **sélectionne les ~5 plus pertinentes** (plafond `max_studies` du config).
   Hiérarchie de priorité :
   - méta-analyses / revues systématiques > essais randomisés (ECR) > études primaires >
     preprints medRxiv > protocoles d'essais (ClinicalTrials.gov) ;
   - pertinence clinique réelle pour la pratique tDCS/rTMS (effet thérapeutique, indication,
     dispositif, paramètres de stimulation) ;
   - écarte le bruit (revues narratives génériques, doublons de contenu, études animales/in
     silico sans portée clinique, mentions purement méthodologiques de la TMS).
2. **Vérifie chaque source** : pour les articles retenus, confirme via le résumé fourni par le
   script et, si besoin, `WebFetch` sur l'URL (PubMed/DOI). **N'invente jamais** un chiffre, une
   taille d'effet, un DOI ou un lien. Si une donnée n'est pas vérifiable, ne l'affirme pas.
3. Rédige `veilles/<YYYY-Www>.md` **en français**. La **première ligne** doit être un titre `#`
   de la forme exacte (elle devient l'objet de l'email) :

```markdown
# Veille tDCS/rTMS — semaine <YYYY-Www> — <N> nouvelle(s) étude(s)

## En bref
2-4 phrases : les faits saillants de la semaine, la tendance qui se dégage.

## 1. <Titre court en français> — <Revue ou source>, <année> · [<modalité>] · [<niveau de preuve>]

### Introduction
Contexte, question de recherche, population et indication, type d'étude, dispositif et
paramètres de stimulation si pertinents. 1-2 paragraphes.

### Résultats clés
Les résultats principaux : effets, tailles d'effet et intervalles de confiance **s'ils sont
disponibles dans la source**, comparateur (sham/placebo, autre traitement), tolérance. 1-2
paragraphes. Reste fidèle aux chiffres de la source.

### Conclusion
Portée clinique, limites (taille d'échantillon, biais, durée de suivi), niveau de preuve. Pour
un **preprint**, écris explicitement « preprint — non revu par les pairs ». Pour un **protocole
ClinicalTrials.gov**, précise « essai en cours / protocole, pas encore de résultats ».

**Source :** <lien vérifié> · **Identifiant :** <PMID / DOI / NCT>

## 2. ... (jusqu'à ~5 études, même structure) ...

## Études écartées cette semaine
Liste courte (titre + identifiant + raison en quelques mots) des autres candidats non retenus,
pour traçabilité.
```

Le bulletin doit être **autosuffisant** : beaucoup d'articles sont sous paywall, le lecteur doit
tout comprendre sans cliquer sur un lien.

## Étape 3 — Traçabilité (données structurées)

1. Écris `data/reported/<YYYY-Www>.json` selon le schéma de `data/README.md` : une entrée par
   étude **réellement résumée**, avec `id`, `venue_id`, `source`, `modality` (`tDCS`/`rTMS`),
   `indication`, `evidence_type`, `peer_reviewed`, `open_access`, `year`.
2. Pour chaque étude, réutilise un `venue_id` de `data/registry.csv`. **Si la revue n'y figure
   pas**, ajoute une ligne (`id,name,publisher,type,discipline,country,notes`) en respectant la
   taxonomie de `data/README.md`. En cas de doute sur `discipline`/`country`, inscris `to-verify`
   plutôt que de deviner — Kamil tranchera.
3. Mets à jour `data/seen.json` :
   - passe `last_run_utc` à l'horodatage courant ;
   - ajoute **toutes** les études proposées cette semaine, résumées **et** écartées, sous la forme
     `"<id>": {"first_week": "<YYYY-Www>", "status": "resume"|"ecarte", "title": "<titre>"}`.
   C'est ce qui garantit qu'aucune étude ne sera re-proposée à l'avenir.
4. Ne modifie **jamais** `data/STATS.md` : il est régénéré automatiquement par la GitHub Action
   `build-stats.yml` après ton push.

## Étape 4 — Commit et push (c'est l'envoi du mail)

**Cas normal (au moins 1 étude résumée)** :
1. Committe tous les fichiers modifiés (`veilles/...`, `data/candidates/...`, `data/reported/...`,
   `data/seen.json`, `data/registry.csv` si enrichi) avec le message
   `Veille tDCS/rTMS <YYYY-Www>`.
2. Pousse sur la branche par défaut **exactement une fois**. La GitHub Action `send-veille.yml`
   détecte le nouveau `veilles/*.md` et envoie son contenu rendu par email (la première ligne,
   sans le `# `, devient l'objet). Ne fais pas de second commit touchant `veilles/` (chaque push
   d'un bulletin envoie un email).

**Cas vide (0 nouvelle étude — Étape 1.2)** :
1. N'écris **aucun** fichier dans `veilles/`.
2. Tu peux committer la mise à jour de `data/` (`candidates/...`, `seen.json` avec `last_run_utc`)
   avec le message `Veille tDCS/rTMS <YYYY-Www> — aucune nouveauté`, puis pousser. Comme rien n'a
   changé sous `veilles/`, **aucun email n'est envoyé** (conforme à « s'il y en a »).

## Gestion des échecs

- Si le push échoue, réessaie une fois après `git pull --rebase`. **Ne force-push jamais.**
- Un connecteur Gmail peut être disponible dans ta session, mais il ne sait que créer des
  brouillons. **Ne l'utilise pas pour la livraison** : la GitHub Action est le seul canal d'envoi.
- Pour ajuster le périmètre de la veille (termes, indications, sources, plafond), édite
  `config/query.json` — aucune modification de cet `INSTRUCTIONS.md` n'est nécessaire.
