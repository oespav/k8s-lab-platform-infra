# platform-infra

Owned by **platform-team**. Everything that runs the cluster is declared here
and deployed by Argo CD; nobody runs `helm` or `kubectl apply` by hand except
the one-time bootstrap.

## Layout

```
bootstrap/             one-time: install Argo CD, apply the root app
argocd/                synced by the root app (app-of-apps)
  projects/            AppProject "platform"
  apps/                one Application per platform component, named after it
                       (deploy order comes from the sync-wave annotation, not the filename)
  tenants/             one file per dev team: Namespace + AppProject + Application
components/            what those Applications deploy (values, manifests, charts)
  gateway-api-crds/    Gateway API v1.5.1 CRDs
  istio/               istio/base, istiod (ambient profile), cni, ztunnel 1.30.4 values
  cert-manager/        cert-manager values
  trust-manager/       trust-manager values
  metrics-server/      metrics-server values (feeds HPAs and kubectl top)
  pki/                 lab root CA, lab-ca ClusterIssuer, CA trust bundle
  mesh-policy/         mesh-wide STRICT mTLS (PeerAuthentication)
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
| -7 | istiod, istio-cni, cert-manager, metrics-server |
| -6 | ztunnel, trust-manager |
| -5 | mesh-policy (STRICT mTLS), pki |
| -4 | platform-namespaces |
| -3 | platform-gateway |
| 0 | argocd (self-managed) |
| 5 | traffic-console |
| 10 | tenant apps (hello-alpha, hello-beta) + team-alpha waypoint |

This table is the human-readable order; the machine-readable one is the
`argocd.argoproj.io/sync-wave` annotation in each file. Filenames carry no
ordering, so adding a component never renames anything.

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

## Hostnames

Gateways serve `*.apps.localhost` (public) and `*.internal.localhost`
(internal). Browsers, curl and git resolve every `*.localhost` name to the
local machine by themselves, so the lab needs no DNS or /etc/hosts entries,
and the two domains don't overlap (a Gateway API wildcard covers every
subdomain depth, so `*.example.com` would also have matched internal names).

## Upgrade a component

Change `targetRevision` in `argocd/apps/<component>.yaml` (or the values
under `components/`), commit, push. Argo CD rolls it out within ~30 s.
