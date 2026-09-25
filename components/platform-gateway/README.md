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

Each gateway Service is a `LoadBalancer`. In the lab the load balancer comes
from cloud-provider-kind (run by `k8s-lab-cluster`), which publishes each
Service's ports 1:1 on the laptop. Istio takes the Service ports from the
listener ports, so those are set per gateway in `values.yaml` (`ports`), and
they must not overlap: two load balancers can't both bind the same laptop port.

| Gateway | Listener = Service = laptop port (https / http) | Redirect port |
|---|---|---|
| `public` | 8443 / 8080 | 8443 |
| `internal` | 9443 / 9080 | 9443 |

A Gateway is only `Programmed` once its Service has an address, so if the load
balancer isn't running this app never turns Healthy and every later sync wave
waits behind it.

On a cloud cluster drop `ports` and `redirectPort`: the listeners fall back to
443/80 and the provider's load balancer serves them.
