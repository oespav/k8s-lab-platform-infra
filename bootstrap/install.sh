#!/usr/bin/env bash
# One-time bootstrap: install Argo CD with Helm, then hand control to Git.
# After this, every change (including Argo CD upgrades) is a commit to this repo.
set -euo pipefail
cd "$(dirname "$0")/.."

ARGOCD_CHART_VERSION=10.9.2   # keep in sync with argocd/apps/09-argocd.yaml

helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null
helm upgrade --install argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" \
  --namespace argocd --create-namespace \
  --values components/argocd/values.yaml --wait --timeout 10m

kubectl apply -f argocd/projects/platform.yaml -f bootstrap/root-app.yaml
echo "Argo CD installed; the root app now syncs argocd/ from Git."
