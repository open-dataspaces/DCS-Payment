#!/bin/bash
#
# OpenAPI specification generator
# Generates openapi.json from FastAPI app and converts to HTML using Redoc
#
# Usage:
#   ./scripts/generate-openapi.sh          # Run locally (requires Python environment)
#   ./scripts/generate-openapi.sh --docker # Run via Docker container
#

set -e

# Script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${PROJECT_ROOT}/docs/openapi"

# Output files
OPENAPI_JSON="${OUTPUT_DIR}/openapi.json"
OPENAPI_HTML="${OUTPUT_DIR}/openapi.html"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Create output directory
create_output_dir() {
    if [ ! -d "${OUTPUT_DIR}" ]; then
        log_info "Creating output directory: ${OUTPUT_DIR}"
        mkdir -p "${OUTPUT_DIR}"
    fi
}

# Generate openapi.json from FastAPI app
generate_openapi_json() {
    log_info "Generating openapi.json..."

    cd "${PROJECT_ROOT}"

    PYTHONPATH="${PROJECT_ROOT}" python -c "
import json
from app.main import app

openapi_schema = app.openapi()
with open('${OPENAPI_JSON}', 'w', encoding='utf-8') as f:
    json.dump(openapi_schema, f, indent=2, ensure_ascii=False)
print('OpenAPI schema generated successfully')
"

    if [ -f "${OPENAPI_JSON}" ]; then
        log_info "Generated: ${OPENAPI_JSON}"
    else
        log_error "Failed to generate openapi.json"
        exit 1
    fi
}

# Generate openapi.json via Docker
generate_openapi_json_docker() {
    log_info "Generating openapi.json via Docker..."

    cd "${PROJECT_ROOT}/docker"

    # Check if container is running
    if ! docker compose ps --status running | grep -q "app"; then
        log_info "Starting Docker containers..."
        docker compose up -d
        log_info "Waiting for container to be ready..."
        sleep 5
    fi

    # Fetch from running container
    curl -s http://localhost:8000/openapi.json | jq . > "${OPENAPI_JSON}"

    if [ -f "${OPENAPI_JSON}" ] && [ -s "${OPENAPI_JSON}" ]; then
        log_info "Generated: ${OPENAPI_JSON}"
    else
        log_error "Failed to generate openapi.json from Docker"
        exit 1
    fi
}

# Convert openapi.json to HTML using Redoc
generate_html() {
    log_info "Generating openapi.html using Redoc..."

    # Check if npx is available
    if command -v npx &> /dev/null; then
        npx @redocly/cli build-docs "${OPENAPI_JSON}" \
            --output "${OPENAPI_HTML}" \
            --title "Payment API Documentation"

        if [ -f "${OPENAPI_HTML}" ]; then
            log_info "Generated: ${OPENAPI_HTML}"
        else
            log_error "Failed to generate openapi.html"
            exit 1
        fi
    else
        log_warn "npx not found. Trying Docker method..."
        generate_html_docker
    fi
}

# Generate HTML using Docker (fallback)
generate_html_docker() {
    log_info "Generating openapi.html using Docker..."

    docker run --rm \
        -v "${OUTPUT_DIR}:/spec" \
        redocly/cli build-docs /spec/openapi.json \
        --output /spec/openapi.html \
        --title "Payment API Documentation"

    if [ -f "${OPENAPI_HTML}" ]; then
        log_info "Generated: ${OPENAPI_HTML}"
    else
        log_error "Failed to generate openapi.html via Docker"
        exit 1
    fi
}

# Main execution
main() {
    local use_docker=false

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --docker)
                use_docker=true
                shift
                ;;
            --help|-h)
                echo "Usage: $0 [--docker]"
                echo ""
                echo "Options:"
                echo "  --docker    Use Docker to generate openapi.json"
                echo "  --help      Show this help message"
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    log_info "Starting OpenAPI documentation generation..."
    echo ""

    # Step 1: Create output directory
    create_output_dir

    # Step 2: Generate openapi.json
    if [ "$use_docker" = true ]; then
        generate_openapi_json_docker
    else
        generate_openapi_json
    fi

    echo ""

    # Step 3: Generate HTML
    generate_html

    echo ""
    log_info "Documentation generation complete!"
    log_info "Output files:"
    log_info "  - JSON: ${OPENAPI_JSON}"
    log_info "  - HTML: ${OPENAPI_HTML}"
}

main "$@"
