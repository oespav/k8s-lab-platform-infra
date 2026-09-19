# platform-infra

Owned by **platform-team**. Everything that runs the cluster is declared here
and deployed by Argo CD; nobody runs `helm` or `kubectl apply` by hand except
the one-time bootstrap.

## Layout

```
bootstrap/             one-time: install Argo CD, apply the root app
argocd/                synced by the root app (app-of-apps)
  projects/            AppProject "platform"
  apps/                one Application per platform component (ordered by sync wave)
  tenants/             one file per dev team: Namespace + AppProject + Application
components/            what those Applications deploy (values, manifests, charts)
  gateway-api-crds/    Gateway API v1.5.1 CRDs
  istio/               istio/base + istio/istiod 1.30.4 values
  cert-manager/        cert-manager values
  trust-manager/       trust-manager values
  metrics-server/      metrics-server values (feeds HPAs and kubectl top)
  pki/                 lab root CA, lab-ca ClusterIssuer, CA trust bundle
  namespaces/          platform-owned namespaces + gateway access labels
  platform-gateway/    Helm chart: shared public/internal HTTPS gateways
  argocd/              Argo CD values (self-managed) + its HTTPRoute
  traffic-console/     traffic generator + live dashboard for tenant apps
```

## Sync order

| Wave | Applications |
|---|---|
| -10 | AppProjects (platform, team-alpha, team-beta) |
| -9 | gateway-api-crds |
| -8 | istio-base |
| -7 | istiod, cert-manager, metrics-server |
| -6 | trust-manager |
| -5 | pki |
| -4 | platform-namespaces |
| -3 | platform-gateway |
| 0 | argocd (self-managed) |
| 5 | traffic-console |
| 10 | tenant apps (hello-alpha, hello-beta) |

The root app waits for each wave to be **Healthy** before starting the next
(custom Application health check in `components/argocd/values.yaml`).

## Bootstrap

```bash
bootstrap/install.sh
```

## Onboard a team

Copy `argocd/tenants/team-alpha.yaml`, change the team name, repo and
gateway label, open a PR. Merging it creates the namespace, a locked-down
AppProject (own repo, own namespace, no cluster-scoped resources) and the
Application that deploys `chart/` with `environments/lab/values.yaml` from
the team's repo.

## Upgrade a component

Change `targetRevision` in `argocd/apps/<component>.yaml` (or the values
under `components/`), commit, push. Argo CD rolls it out within ~30 s.
