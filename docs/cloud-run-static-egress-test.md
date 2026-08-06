# Cloud Run static-egress experiment

This is a reversible diagnostic experiment for Smart Paste URL retrieval. It
routes only the public `nearhome-api` service—the component that performs the
initial HTTP request and Playwright fallback—through a dedicated Direct VPC
egress subnet and Cloud NAT address. The enrichment worker is deliberately not
attached to this network.

The experiment does not bypass access controls. It sends a small, rate-limited
set of requests to one fixed PropertyGuru listing and never uses CAPTCHA
solving, proxy rotation, stealth automation, cookies, or challenge
circumvention.

## Safety and baseline

Before changing anything, save the service export:

```bash
gcloud run services describe nearhome-api --region=asia-southeast1 --format=export \
  > /tmp/nearhome-cloud-run-before-static-egress.yaml
```

The protected endpoint is disabled by default. For a tagged experiment revision
only, set `ENABLE_EGRESS_DIAGNOSTICS=true` and attach the server-only Secret
Manager value `EGRESS_DIAGNOSTICS_TOKEN`. It accepts no URLs: it calls two
fixed IP echo services and one fixed PropertyGuru listing, and returns only
status, final URL, content type, bounded title, byte count, challenge result,
and usable-text length. It never returns HTML, headers, cookies, credentials,
or listing evidence, and it does not send a challenge response to Groq.

Call it with a token supplied outside shell history:

```bash
curl -H "X-NearHome-Diagnostic-Token: <token>" \
  https://<tag>---nearhome-api-<hash>.asia-southeast1.run.app/api/v1/internal/egress-diagnostics
```

Record the response before and after routing through NAT. Do not run repeated
tests against PropertyGuru; one baseline and one static-egress test are enough
unless debugging a transient infrastructure failure.

## Provision the dedicated network

The script is idempotent for matching resources and stops if a name or CIDR is
unexpected. It creates `nearhome-egress-vpc`, a dedicated `/26` subnet,
router, Cloud NAT gateway, and regional reserved address. It does **not** alter
Cloud Run networking or traffic.

```bash
GOOGLE_CLOUD_PROJECT=<project-id> \
./scripts/gcp/configure-static-egress.sh <project-id> asia-southeast1 nearhome-api
```

The script grants only `roles/compute.networkUser` to the Cloud Run service
agent, which Direct VPC egress requires. It does not create service-account
keys or broaden project Owner/Editor access.

## Test revision and verification

Use `gcloud run deploy --no-traffic --tag=static-egress-test` with the current
image, all current service settings retained, and only these additional flags:

```text
--network=nearhome-egress-vpc
--subnet=nearhome-egress-subnet
--vpc-egress=all-traffic
--update-env-vars=ENABLE_EGRESS_DIAGNOSTICS=true
--update-secrets=EGRESS_DIAGNOSTICS_TOKEN=nearhome-egress-diagnostics-token:latest
```

The diagnostic must report the reserved `nearhome-egress-ip` from both echo
services before any production traffic change. Compare the same fixed listing
against the no-traffic baseline revision. A successful IP check alone does not
prove that PropertyGuru retrieval improved.

Validate health/readiness, normal Smart Paste text input, Smart Paste URL input,
database access, routes/geocoding, Groq, enrichment queueing, and browser/API
CORS before considering any traffic shift. Cloud Run’s Direct VPC documentation
notes cold starts can be delayed by 30 seconds or more; measure it instead of
assuming no latency impact. See [Direct VPC egress](https://cloud.google.com/run/docs/configuring/vpc-direct-vpc)
and [static outbound IP](https://cloud.google.com/run/docs/configuring/static-outbound-ip).

## Rollback

To send traffic back to the saved known-good revision without deleting any
experiment resource:

```bash
gcloud run services update-traffic nearhome-api --region=asia-southeast1 \
  --to-revisions=<previous-revision>=100
```

To create a new revision without Direct VPC egress, use the saved export as the
source of truth or redeploy the prior image/settings with `--clear-network`.
Confirm `/api/v1/ready` and Smart Paste behaviour after the change. Leave NAT
and its reserved address intact until the investigation is complete.

## Explicit cleanup

First ensure no Cloud Run revision is attached to the VPC. Then, and only when
the address is no longer needed, run:

```bash
CONFIRM_REMOVE_STATIC_EGRESS=DELETE_STATIC_EGRESS \
GOOGLE_CLOUD_PROJECT=<project-id> \
./scripts/gcp/remove-static-egress.sh <project-id> asia-southeast1
```

Cleanup order is NAT, router, subnet, VPC, then reserved IP. Releasing the IP
is irreversible: a later recreation may receive a different address.

Cloud NAT and an unused reserved external IPv4 address can incur charges. Use
the current [Cloud NAT pricing](https://cloud.google.com/nat/pricing) and
[external IP pricing](https://cloud.google.com/vpc/pricing#ipaddress) pages for
the applicable region rather than assuming the project’s free tier covers them.
