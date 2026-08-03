.PHONY: dev serve refresh refresh-counts refresh-force flush flush-all help \
        serve-remote migrate-remote refresh-remote refresh-force-remote flush-remote \
        serve-prod export-json export-json-remote update update-counts

# Local targets default to the local Turso backend; override from the environment,
# e.g.  make serve DB_BACKEND=sqlite
export DB_BACKEND ?= turso
export TURSO_DATABASE_URL ?= data/turso.db

# Year + semester scope for refresh/flush (both required), e.g.
#   make refresh YEAR=2026 SEM="spring fall"
SEM ?=
export YEAR ?=
COLLECT ?= catalog,enrollment,grading

# Production (Turso cloud) credentials: prod.env = READ-ONLY (web tier),
# prod-admin.env = READ-WRITE (migrate/refresh/flush). Both chmod 600, never committed.
ADMIN_ENV ?= prod-admin.env

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | sort

dev:             ## run via uv (with dev/admin panels):  make dev PORT=9000
	@set -a; . ./collect.env; set +a; WEB_INDEX=index-dev.html uv run scraper/server.py

serve:           ## launch on Turso (with dev/admin panels):  make serve PORT=9000
	@WEB_INDEX=index-dev.html ./serve.sh

refresh:         ## refresh:  make refresh YEAR=2026 SEM="fall" [COLLECT=catalog,enrollment,grading]
	@./refresh.sh --collect "$(COLLECT)" $(SEM)

refresh-counts:  ## counts-only:  make refresh-counts YEAR=2026 SEM="fall"
	@./refresh.sh --counts-only $(SEM)

refresh-force:   ## force counts for an ENDED term (마감):  make refresh-force YEAR=2025 SEM=fall
	@./refresh.sh --counts-only --force $(SEM)

flush:           ## flush (confirms):  make flush YEAR=2026 SEM="spring fall"
	@./flush.sh $(SEM)

flush-all:       ## wipe the entire catalog (confirms)
	@./flush.sh all

# ---- production (Turso cloud) ----
serve-remote:    ## web tier on production, READ-ONLY:  make serve-remote PORT=9000
	@./serve.sh --remote

migrate-remote:  ## push the full local catalog -> production (write token)
	@set -a; . ./$(ADMIN_ENV); set +a; .venv/bin/python scraper/migrate_to_turso.py --src data/turso.db

refresh-remote:  ## refresh production:  make refresh-remote YEAR=2026 SEM=fall
	@set -a; . ./$(ADMIN_ENV); set +a; ./refresh.sh --collect "$(COLLECT)" $(SEM)

refresh-force-remote: ## force counts for an ENDED term on production:  make refresh-force-remote YEAR=2025 SEM=fall
	@set -a; . ./$(ADMIN_ENV); set +a; ./refresh.sh --counts-only --force $(SEM)

flush-remote:    ## flush production (confirms):  make flush-remote YEAR=2026 SEM=fall
	@set -a; . ./$(ADMIN_ENV); set +a; ./flush.sh $(SEM)

# ---- production preview / static export ----
serve-prod:      ## local preview of the EXACT prod page: index.html + web/data/*.json, NO DB
	@./serve.sh --static

export-json:     ## dump local catalog -> web/data/classes/ + trend/ (per term + index)
	@.venv/bin/python scraper/export_json.py

export-json-remote: ## dump REMOTE catalog -> web/data/classes/ + trend/ (read-only token)
	@set -a; . ./prod.env; set +a; .venv/bin/python scraper/export_json.py

# ---- update ----
update: ## run update script DB
	@./scripts/update.sh

update-counts: ## run the windowed fast trend update
	@./scripts/update-counts.sh
