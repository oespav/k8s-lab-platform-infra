# platform-gateway chart

Shared Istio Gateway API gateways, owned by the platform team, deployed by
Argo CD (`argocd/apps/08-platform-gateway.yaml`) into `infra-gw`.

| Gateway | Hostnames | Access label | Replicas (HPA) | PDB |
|---|---|---|---|---|
| `public` | `*.apps.localhost` | `gateway-access/public=true` | 2–5 | minAvailable 1 |
| `internal` | `*.internal.localhost` | `gateway-access/internal=true` | 1–2 | none |

Per gateway the chart renders:

- `Certificate <name>-tls`: wildcard cert from the `lab-ca` ClusterIssuer (cert-manager)
- `Gateway <name>`: `https` listener (443, apps attach here) + `http` listener (80)
- `HTTPRoute <name>-https-redirect`: 301 from HTTP to HTTPS
- `ConfigMap <name>-gateway-options`: HPA/PDB/resources that Istio merges into
  the proxy Deployment it generates (`spec.infrastructure.parametersRef`)

App routes attach with:

```yaml
parentRefs:
  - name: public          # or internal
    namespace: infra-gw
    sectionName: https
```

## Exposure

In the lab each gateway Service is a `NodePort` with fixed ports (pinned via
the `service` key of the options ConfigMap), fronted by the edge load
balancer in `k8s-lab/cluster/edge-lb`:

| Gateway | NodePorts (https/http) | Laptop port | Redirect port |
|---|---|---|---|
| `public` | 30443 / 30080 | 8443 / 8080 | 8443 |
| `internal` | 31443 / 31080 | 9443 | 9443 |

On a cloud cluster set `serviceType: LoadBalancer`, drop `nodePorts` and
`redirectPort`, and the provider creates the load balancer for you.
