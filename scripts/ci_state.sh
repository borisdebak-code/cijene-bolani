#!/usr/bin/env bash
# Stanje baze između dnevnih pokretanja na GitHub Actions.
#
#   scripts/ci_state.sh restore   – preuzmi state.dump iz GitHub Releasea "state" i vrati ga
#   scripts/ci_state.sh save      – ostavi samo sažetak (bez cijena po poslovnicama) i spremi ga
#
# Čuva se: trgovci, poslovnice, proizvodi, artikli trgovaca, kategorije i dnevni sažeci
# cijena (povijest za graf, zadnjih 180 dana). Cijene po poslovnicama (stotine MB dnevno)
# se ne čuvaju – svaki dan se ionako unose iznova iz službenih cjenika.
set -euo pipefail
: "${PGURL:?PGURL (postgresql://...) nije postavljen}"
: "${GITHUB_REPOSITORY:?}"
TAG=state
WORK="${RUNNER_TEMP:-/tmp}/cijene-state"
mkdir -p "$WORK"

case "${1:-}" in
  restore)
    if gh release download "$TAG" --repo "$GITHUB_REPOSITORY" --pattern state.dump --dir "$WORK" --clobber 2>/dev/null; then
      pg_restore --no-owner --no-privileges --dbname "$PGURL" "$WORK/state.dump"
      echo "Stanje vraćeno ($(du -h "$WORK/state.dump" | cut -f1))."
    else
      echo "Nema spremljenog stanja – kreće se od prazne baze."
    fi
    ;;
  save)
    psql "$PGURL" -v ON_ERROR_STOP=1 -c "TRUNCATE price, ingest_error;" \
      -c "DELETE FROM chain_daily WHERE day < current_date - 180;" \
      -c "DELETE FROM ingest_run WHERE started_at < now() - interval '60 days';"
    pg_dump --format=custom --compress=6 --no-owner --file "$WORK/state.dump" "$PGURL"
    if ! gh release view "$TAG" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
      gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --title "Stanje baze (automatski)" \
        --notes "Sažeta baza za dnevno osvježavanje cijena. Ne briši – bez nje se gubi povijest cijena." \
        --latest=false
    fi
    gh release upload "$TAG" "$WORK/state.dump" --repo "$GITHUB_REPOSITORY" --clobber
    echo "Stanje spremljeno ($(du -h "$WORK/state.dump" | cut -f1))."
    ;;
  *)
    echo "upotreba: $0 restore|save" >&2; exit 2 ;;
esac
