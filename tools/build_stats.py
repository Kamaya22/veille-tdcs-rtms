#!/usr/bin/env python3
"""Agrège les études réellement résumées (data/reported/*.json) en statistiques
déterministes, écrites dans data/STATS.md.

Bibliothèque standard uniquement — aucune dépendance pip.
Usage : python tools/build_stats.py

Donne à Kamil une vue de tendance de la neuromodulation en psychiatrie dans la durée :
répartition tDCS / rTMS, indications couvertes, niveaux de preuve, revues, accès ouvert,
sources (PubMed / medRxiv / ClinicalTrials.gov), et évolution semaine par semaine.
"""

import csv
import glob
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

# --- Seuils d'alerte (documentés) ----------------------------------------- #
VENUE_CONCENTRATION = 50.0   # % : une revue au-dessus est signalée
TOP_VENUES = 15

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
REGISTRY = os.path.join(DATA_DIR, "registry.csv")
REPORTED_GLOB = os.path.join(DATA_DIR, "reported", "*.json")
OUT = os.path.join(DATA_DIR, "STATS.md")

PEER_LABEL = {True: "revu par les pairs", False: "non revu par les pairs"}


def load_registry():
    reg = {}
    if not os.path.exists(REGISTRY):
        return reg
    with open(REGISTRY, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sid = (row.get("id") or "").strip()
            if sid:
                reg[sid] = {k: (v or "").strip() for k, v in row.items()}
    return reg


def load_reported():
    weeks = []
    for path in sorted(glob.glob(REPORTED_GLOB)):
        with open(path, encoding="utf-8") as f:
            weeks.append(json.load(f))
    return weeks


def pct(n, total):
    return (100.0 * n / total) if total else 0.0


def compute_stats(reg, weeks):
    total = 0
    by_modality = Counter()
    by_indication = Counter()
    by_evidence = Counter()
    by_source = Counter()
    by_open = Counter()
    by_peer = Counter()
    by_year = Counter()
    by_venue = Counter()        # (venue_id, name) -> count
    by_publisher = Counter()
    by_week = Counter()
    week_venues = defaultdict(set)
    off_registry = Counter()
    to_verify = []

    for wk in weeks:
        week = wk.get("week", "")
        by_week[week] += 0
        for e in wk.get("reported", []):
            total += 1
            vid = e.get("venue_id", "")
            meta = reg.get(vid)
            if meta is None:
                off_registry[vid] += 1
                name, publisher = vid or "(inconnu)", "(hors-registre)"
            else:
                name = meta.get("name", vid)
                publisher = meta.get("publisher", "")
                if meta.get("discipline") == "to-verify" or meta.get("country") == "to-verify":
                    to_verify.append(f"Venue à vérifier : {name} (`{vid}`)")
            ev = e.get("evidence_type", "?")
            if ev == "to-verify":
                to_verify.append(f"Type de preuve `to-verify` pour {e.get('id', '?')}")
            by_modality[e.get("modality", "?")] += 1
            by_indication[e.get("indication", "?")] += 1
            by_evidence[ev] += 1
            by_source[e.get("source", "?")] += 1
            by_open[e.get("open_access", "unknown")] += 1
            by_peer[PEER_LABEL.get(e.get("peer_reviewed"), "inconnu")] += 1
            yr = e.get("year")
            by_year[str(yr) if yr else "inconnu"] += 1
            by_venue[(vid, name)] += 1
            by_publisher[publisher] += 1
            if week:
                by_week[week] += 1
                week_venues[week].add(vid)

    return {
        "total": total, "by_modality": by_modality, "by_indication": by_indication,
        "by_evidence": by_evidence, "by_source": by_source, "by_open": by_open,
        "by_peer": by_peer, "by_year": by_year, "by_venue": by_venue,
        "by_publisher": by_publisher, "by_week": by_week, "week_venues": week_venues,
        "off_registry": off_registry, "to_verify": to_verify, "reg": reg,
        "n_weeks": len(weeks), "weeks": sorted(by_week),
    }


def _bar(p):
    return "█" * int(round(p / 5.0))


def _table(title, counter, total, order=None):
    items = list(counter.most_common())
    if order:
        items.sort(key=lambda kv: order.index(kv[0]) if kv[0] in order else 999)
    lines = [f"### {title}", "", "| Valeur | Études | Part | |", "|---|---:|---:|---|"]
    for value, n in items:
        p = pct(n, total)
        lines.append(f"| {value or '—'} | {n} | {p:.1f} % | {_bar(p)} |")
    lines.append("")
    return "\n".join(lines)


def render_markdown(s):
    total = s["total"]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ["# Statistiques de la veille tDCS / rTMS", "",
           f"*Généré automatiquement par `tools/build_stats.py` — ne pas éditer à la main. "
           f"Dernière génération : {generated}.*", "",
           "## Résumé", "",
           f"- **Semaines de veille** : {s['n_weeks']}",
           f"- **Études résumées (cumul)** : {total}",
           f"- **Revues distinctes** : {len({vid for (vid, _) in s['by_venue']})}",
           f"- **Part accès ouvert** : {pct(s['by_open'].get('open', 0), total):.1f} %", ""]

    out += ["## Alertes / à vérifier", ""]
    al = list(s["to_verify"])
    for vid, n in s["off_registry"].items():
        al.append(f"⚠️ Venue hors-registre : `{vid}` ({n}) — ajouter une ligne à `registry.csv`.")
    for venue, n in s["by_venue"].most_common():
        p = pct(n, total)
        if venue[0] and p > VENUE_CONCENTRATION:
            al.append(f"🏢 Concentration : {venue[1]} = {p:.1f} % des études (seuil {VENUE_CONCENTRATION:.0f} %).")
    out += ([f"- {a}" for a in al] if al else
            ["Aucune alerte : pas de venue hors-registre ni `to-verify`, pas de concentration excessive."])
    out += [""]

    out += ["## Répartitions", ""]
    out.append(_table("Par modalité", s["by_modality"], total, order=["tDCS", "rTMS"]))
    out.append(_table("Par indication", s["by_indication"], total))
    out.append(_table("Par niveau de preuve", s["by_evidence"], total))
    out.append(_table("Par source", s["by_source"], total,
                      order=["pubmed", "medrxiv", "clinicaltrials"]))
    out.append(_table("Par accès", s["by_open"], total, order=["open", "paywall", "unknown"]))
    out.append(_table("Par relecture", s["by_peer"], total,
                      order=["revu par les pairs", "non revu par les pairs", "inconnu"]))
    out.append(_table("Par année", s["by_year"], total, order=sorted(s["by_year"], reverse=True)))

    out += [f"## Revues les plus citées (top {TOP_VENUES})", "",
            "| Revue | Études | Part |", "|---|---:|---:|"]
    for (vid, name), n in s["by_venue"].most_common(TOP_VENUES):
        out.append(f"| {name} | {n} | {pct(n, total):.1f} % |")
    out += [""]

    out += ["## Évolution hebdomadaire", "",
            "| Semaine | Études résumées | Revues distinctes |", "|---|---:|---:|"]
    for w in s["weeks"]:
        out.append(f"| {w} | {s['by_week'][w]} | {len(s['week_venues'][w])} |")
    out += [""]
    return "\n".join(out)


def main():
    s = compute_stats(load_registry(), load_reported())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(render_markdown(s))
    print(f"STATS.md généré : {s['total']} étude(s) sur {s['n_weeks']} semaine(s) -> {OUT}")


if __name__ == "__main__":
    main()
