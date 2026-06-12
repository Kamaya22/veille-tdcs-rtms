# Veille tDCS / rTMS en psychiatrie — bulletin hebdomadaire automatisé

Veille personnelle pour le suivi des évolutions des traitements par **tDCS** (stimulation
transcrânienne à courant continu) et **rTMS** (stimulation magnétique transcrânienne répétitive).
Chaque semaine, un agent Claude planifié dans le cloud détecte les **nouvelles études** parues
sur le sujet dans des sources de recherche sérieuses, en rédige un **résumé en français**
(Introduction + Résultats clés + Conclusion) pour les ~5 plus pertinentes, et l'envoie par email.
**S'il n'y a aucune nouveauté la semaine, aucun email n'est envoyé.**

Même architecture que les dépôts `Automatisation_Recherche` et `Automatisation_news` : un script
déterministe récupère et dédoublonne, l'agent juge et résume, un push déclenche l'envoi de l'email.

## Fonctionnement

```
Routine cloud Claude (cron hebdomadaire)
  └─ clone ce repo, lit INSTRUCTIONS.md
       1a. python tools/fetch_studies.py --print-queries  ← URL à interroger (PubMed/medRxiv
       │      via Europe PMC, ClinicalTrials.gov), construites depuis config/query.json
       1b. l'agent WebFetch chaque URL → data/raw/<semaine>.json
       1c. python tools/fetch_studies.py  ← lit data/raw, dédoublonne contre data/seen.json,
       │      filtre le périmètre → data/candidates/<semaine>.json
       2. l'agent choisit ~5 études, rédige veilles/<semaine>.md (FR), met à jour data/
       3. commit + push
              └─ GitHub Action send-veille.yml détecte veilles/** → email SMTP Gmail
```

> **Pourquoi WebFetch ?** Le runner cloud bloque les appels réseau *directs* du script (egress 403),
> mais l'outil WebFetch de l'agent passe. Le script garde tout le déterminisme (dédoublonnage,
> filtre, tri) ; seul le GET HTTP est délégué à l'agent. En **local**, où le réseau fonctionne, le
> script bascule automatiquement en mode HTTP direct si `data/raw/<semaine>.json` est absent
> (pratique pour tester : `python tools/fetch_studies.py`).

- **Sources** : **Europe PMC** (indexe **PubMed/MEDLINE** revu par les pairs *et* les **preprints
  medRxiv**, en une API JSON ; expose le PMID) + **ClinicalTrials.gov** (API v2, essais en cours).
- **Détection du « nouveau »** : chaque étude a un identifiant stable (`pmid:` / `doi:` / `nct:`).
  `data/seen.json` mémorise toute étude déjà proposée (résumée **ou** écartée) : elle n'est jamais
  re-proposée.
- **Envoi de l'email** : la GitHub Action `.github/workflows/send-veille.yml` détecte chaque
  nouveau `veilles/*.md` poussé et l'envoie (SMTP Gmail) au destinataire `MAIL_TO` (à défaut
  `kamilmahmal22@gmail.com`). La première ligne `#` du fichier devient l'objet du mail.

## Piloter la veille

Éditer **`config/query.json`** (sur GitHub web, l'app mobile, ou en local) — la routine le relit à
chaque exécution, aucune modification de code n'est nécessaire :

- `modalities` / `indications` : ajouter ou retirer des termes de recherche.
- `sources` : passer `pubmed`, `medrxiv` ou `clinicaltrials` à `true`/`false`.
- `window_days` : fenêtre de recherche (défaut 8 jours, léger recouvrement pour ne rien manquer).
- `max_studies` : nombre maximum d'études résumées par semaine (défaut 5).

## Structure

```
INSTRUCTIONS.md            — instructions complètes de l'agent (étapes, format FR, traçabilité)
config/query.json          — paramètres de la veille (éditable à tout moment)
tools/fetch_studies.py     — collecte + dédoublonnage (stdlib, sans pip ; modes raw/HTTP)
tools/build_stats.py       — génère data/STATS.md à partir de data/reported/
veilles/<YYYY-Www>.md      — archive des bulletins (= corps des emails)
data/raw/<...>.json        — JSON brut des API récupéré par l'agent via WebFetch (mode cloud)
data/candidates/<...>.json — candidats normalisés + dédoublonnés par le script (audit)
data/reported/<...>.json   — études réellement résumées (étiquetées par l'agent)
data/seen.json             — registre anti-doublon (PMID / DOI / NCT déjà traités)
data/registry.csv          — registre des revues/venues (auditable par Kamil)
data/STATS.md              — statistiques déterministes (généré automatiquement)
data/README.md             — codebook : schémas et taxonomie
```

## Mise en service (à faire une fois)

1. **Repo GitHub** : `git init`, créer le dépôt, pousser ce dossier sur la branche `main`.
2. **Secrets & variable** (Settings → Secrets and variables → Actions) — identiques aux autres
   dépôts d'automatisation :
   - secret `MAIL_USERNAME` : l'adresse Gmail d'envoi ;
   - secret `MAIL_APP_PASSWORD` : un mot de passe d'application Google
     (https://myaccount.google.com/apppasswords). **Doit être placé dans un *Environment* nommé
     `MAIL_APP_PASSWORD`** (le workflow cible cet environnement) ;
   - variable `MAIL_TO` : destinataire (par défaut `kamilmahmal22@gmail.com`) ;
   - secret optionnel `NCBI_API_KEY` : non requis ici (Europe PMC ne l'exige pas).
3. **Routine cloud** : sur https://claude.ai/code/routines, créer une routine pointant sur ce
   repo, cron **hebdomadaire** (ex. lundi 06:00 Europe/Paris), avec pour consigne
   « lis et suis `INSTRUCTIONS.md` ».

## Tester en local

```bash
python tools/fetch_studies.py          # → data/candidates/<semaine>.json
python tools/build_stats.py            # → data/STATS.md (après au moins un data/reported/*.json)
```

Le connecteur Gmail de claude.ai ne sait que créer des brouillons : la livraison passe
**uniquement** par la GitHub Action.

## Gérer la routine

La routine se gère sur https://claude.ai/code/routines (activer/désactiver, supprimer, voir les
exécutions et les logs).
