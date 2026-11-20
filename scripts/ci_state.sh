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

# pg_dump/pg_restore iz istog spremnika kao baza (PG_CONTAINER), da se verzije slažu –
# noviji pg_dump na GitHubovom računalu piše postavke koje PostgreSQL 16 ne poznaje
pgtool() {
  local tool=$1; shift
  if [ -n "${PG_CONTAINER:-}" ]; then
    docker exec -i "$PG_CONTAINER" "$tool" "$@"
  else
    "$tool" "$@"
  fi
}

case "${1:-}" in
  restore)
    if gh release download "$TAG" --repo "$GITHUB_REPOSITORY" --pattern state.dump --dir "$WORK" --clobber 2>/dev/null; then
      # spremnik (ista verzija kao baza); ako datoteku je napravio noviji pg_dump, noviji alat
      # (zna javiti bezopasnu grešku za postavku "transaction_timeout" – provjera je broj proizvoda)
      pgtool pg_restore --no-owner --no-privileges --dbname "${PGURL_INNER:-$PGURL}" < "$WORK/state.dump" \
        || pg_restore --no-owner --no-privileges --dbname "$PGURL" "$WORK/state.dump" \
        || echo "pg_restore je javio upozorenja – provjeravam podatke"
      n=$(psql "$PGURL" -tAc "SELECT count(*) FROM product")
      echo "Proizvoda u vraćenoj bazi: $n"
      [ "$n" -gt 0 ] || { echo "Vraćanje nije uspjelo" >&2; exit 1; }
      echo "Stanje vraćeno ($(du -h "$WORK/state.dump" | cut -f1))."
    else
      echo "Nema spremljenog stanja – kreće se od prazne baze."
    fi
    ;;
  save)
    psql "$PGURL" -v ON_ERROR_STOP=1 -c "TRUNCATE price, ingest_error;" \
      -c "DELETE FROM chain_daily WHERE day < current_date - 180;" \
      -c "DELETE FROM ingest_run WHERE started_at < now() - interval '60 days';"
    pgtool pg_dump --format=custom --compress=6 --no-owner "${PGURL_INNER:-$PGURL}" > "$WORK/state.dump"
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
