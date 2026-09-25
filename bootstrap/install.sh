#!/usr/bin/env bash
# One-time bootstrap: install Argo CD with Helm, then hand control to Git.
# After this, every change (including Argo CD upgrades) is a commit to this repo.
set -euo pipefail
cd "$(dirname "$0")/.."

ARGOCD_CHART_VERSION=10.9.2   # keep in sync with argocd/apps/argocd.yaml

# The only kube context this script may touch. It installs cluster-wide
# resources and hands the cluster to Argo CD, so running it against the wrong
# context (a shared or production cluster) is not recoverable by re-running.
# Override for another lab cluster:  KUBE_CONTEXT=kind-other bootstrap/install.sh
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-k8s-lab}"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# 1. The context exists. (No grep -q: under pipefail its early exit fails the pipe.)
kubectl config get-contexts -o name | grep -xF "$KUBE_CONTEXT" >/dev/null \
  || die "kube context '$KUBE_CONTEXT' does not exist; is the lab cluster up? (k8s-lab-cluster/scripts/up.sh)"

# 2. It is the current context: a mismatch means intent and shell disagree, so
#    stop and let the operator decide instead of silently picking one.
current="$(kubectl config current-context 2>/dev/null || true)"
[[ "$current" == "$KUBE_CONTEXT" ]] \
  || die "current kube context is '${current:-<none>}', expected '$KUBE_CONTEXT'.
       Switch with:  kubectl config use-context $KUBE_CONTEXT
       or target another lab cluster with:  KUBE_CONTEXT=<context> $0"

# 3. Its cluster answers.
kubectl --context "$KUBE_CONTEXT" get --raw /readyz >/dev/null 2>&1 \
  || die "cluster behind '$KUBE_CONTEXT' is not reachable"

echo "Bootstrapping into kube context '$KUBE_CONTEXT'."

# Every command below is pinned to that context, so a context switch in
# another terminal mid-run cannot redirect the install.

helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null
helm upgrade --install argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" \
  --kube-context "$KUBE_CONTEXT" \
  --namespace argocd --create-namespace \
  --values components/argocd/values.yaml --wait --timeout 10m

kubectl --context "$KUBE_CONTEXT" apply -f argocd/projects/platform.yaml -f bootstrap/root-app.yaml
echo "Argo CD installed; the root app now syncs argocd/ from Git."
