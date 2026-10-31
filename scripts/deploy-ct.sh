#!/usr/bin/env bash
# Despliegue de jarvis-sapo dentro del CT (p.ej. CT 215, Debian 12).
# Se ejecuta como root DENTRO del contenedor, nunca en el host Proxmox.
#
# Uso:
#   scripts/deploy-ct.sh [ref]
# [ref] es opcional (rama, tag o commit), por defecto "main".
#
# Qué hace:
#   1. git fetch + checkout --detach <sha> en /opt/jarvis-sapo
#   2. Instala dependencias Python en /opt/jarvis-sapo/.venv (requirements.txt)
#   3. Compila el frontend (npm ci && npm run build)
#   4. Reinicia jarvis-sapo.service y comprueba que responde en 127.0.0.1:8340
#   5. Si algo falla tras el cambio, hace rollback automático al SHA anterior
#
# No instala Node si falta, no imprime secretos, no usa flags de omisión de
# permisos ni confirmaciones automáticas de tipo "skip-permissions".

set -euo pipefail

REPO_DIR="/opt/jarvis-sapo"
VENV_DIR="${REPO_DIR}/.venv"
FRONTEND_DIR="${REPO_DIR}/frontend"
STATE_DIR="/var/lib/jarvis-sapo/deploy"
PREVIOUS_SHA_FILE="${STATE_DIR}/previous_sha"
SERVICE_NAME="jarvis-sapo.service"
HEALTH_URL="http://127.0.0.1:8340/"
HEALTH_TIMEOUT_SECONDS=30
REF="${1:-main}"

log() {
	printf '[deploy-ct] %s\n' "$1"
}

require_cmd() {
	if ! command -v "$1" >/dev/null 2>&1; then
		log "ERROR: falta el comando requerido '$1' en el PATH. No se instala automáticamente."
		exit 1
	fi
}

current_sha() {
	git -C "$REPO_DIR" rev-parse HEAD
}

install_python_deps() {
	log "Instalando dependencias Python en ${VENV_DIR}"
	if [ ! -d "$VENV_DIR" ]; then
		python3 -m venv "$VENV_DIR"
	fi
	"${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
	"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/requirements.txt"
}

build_frontend() {
	log "Compilando frontend"
	require_cmd npm
	(cd "$FRONTEND_DIR" && npm ci && npm run build)
}

restart_service() {
	log "Reiniciando ${SERVICE_NAME}"
	systemctl restart "$SERVICE_NAME"
}

health_check() {
	log "Comprobando salud en ${HEALTH_URL} (hasta ${HEALTH_TIMEOUT_SECONDS}s)"
	local waited=0
	local code
	while [ "$waited" -lt "$HEALTH_TIMEOUT_SECONDS" ]; do
		code="$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || true)"
		if [ "$code" != "000" ]; then
			log "Servidor responde (HTTP ${code})"
			return 0
		fi
		sleep 1
		waited=$((waited + 1))
	done
	log "ERROR: el servidor no respondió tras ${HEALTH_TIMEOUT_SECONDS}s"
	return 1
}

deploy_ref() {
	local ref="$1"
	log "git fetch"
	git -C "$REPO_DIR" fetch --quiet origin
	local sha
	sha="$(git -C "$REPO_DIR" rev-parse "origin/${ref}" 2>/dev/null || git -C "$REPO_DIR" rev-parse "$ref")"
	log "Checkout a ${sha} (ref solicitada: ${ref})"
	git -C "$REPO_DIR" checkout --detach --quiet "$sha"
	install_python_deps
	build_frontend
	restart_service
	health_check
}

rollback_to() {
	local sha="$1"
	log "ROLLBACK: volviendo a ${sha}"
	git -C "$REPO_DIR" checkout --detach --quiet "$sha"
	install_python_deps
	build_frontend
	restart_service
	if health_check; then
		log "Rollback completado correctamente"
	else
		log "ERROR CRÍTICO: el rollback tampoco responde. Requiere intervención manual."
	fi
}

main() {
	require_cmd git
	require_cmd systemctl
	require_cmd curl
	require_cmd python3

	if [ ! -d "$REPO_DIR/.git" ]; then
		log "ERROR: ${REPO_DIR} no es un repositorio git"
		exit 1
	fi

	mkdir -p "$STATE_DIR"
	local previous_sha
	previous_sha="$(current_sha)"
	printf '%s\n' "$previous_sha" > "$PREVIOUS_SHA_FILE"
	log "SHA anterior guardado: ${previous_sha}"

	if deploy_ref "$REF"; then
		log "Despliegue completado correctamente (ref: ${REF})"
		exit 0
	else
		log "Fallo en el despliegue de ${REF}, iniciando rollback"
		rollback_to "$previous_sha"
		exit 1
	fi
}

main "$@"
