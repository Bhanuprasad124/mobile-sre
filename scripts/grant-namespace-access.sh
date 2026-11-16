#!/usr/bin/env bash
set -euo pipefail
NS="${1:-}"
if [[ -z "$NS" ]]; then echo "Usage: $0 <approved-namespace>"; exit 1; fi
if [[ ! "$NS" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then echo "Invalid namespace"; exit 1; fi
sed "s/REPLACE_NAMESPACE/${NS}/g" "$(dirname "$0")/../k8s/namespace-access.yaml" | kubectl apply -f -
echo "Read-only dashboard access granted in namespace: $NS"
