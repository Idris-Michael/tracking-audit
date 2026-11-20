# Infrastructure (deploy-ready, not deployed)

Two equivalent targets for the same image. Both are checked in CI with `terraform validate`,
tflint and checkov; neither has been applied. Every checkov skip carries its reason inline.

| | Azure (`azure/`) | AWS (`aws/`) |
|---|---|---|
| Compute | Container Apps: `api` (external ingress), `worker` (no ingress) | ECS Fargate services: `api` behind an HTTPS ALB, `worker` |
| Database | Postgres Flexible Server, VNet-injected, no public endpoint | RDS Postgres in private subnets, KMS-encrypted |
| Secrets | Key Vault (RBAC), read by a user-assigned identity | Secrets Manager under a customer-managed KMS key |
| Network | New VNet, NSGs, private DNS zone | Existing VPC passed in (`vpc_id`, subnet ids) |
| Logs | Log Analytics | CloudWatch Logs, 365 days, KMS |

The API token's value is a placeholder in Terraform and is set out of band after apply, so it never
sits in state. The database URL is generated and stored by Terraform.

**Egress.** The worker opens arbitrary public websites, so it needs ports 80 and 443 to the internet.
The rule that it may never reach private addresses is enforced in the app (an egress proxy every
browser connection goes through, ADR 3). On Azure an NSG adds a second line, denying web ports to
private ranges. AWS security groups cannot deny, so there it is the app alone, plus egress limited
to web ports and Postgres.

**Worker size.** Chromium needs 1 vCPU and 2 GiB for two audits at a time. The root filesystem is
read-only on AWS; Chromium's scratch files go to an ephemeral `/tmp` volume.

Run migrations once per release before rolling the services:
`alembic upgrade head` as a one-off task / job with the same image and `DATABASE_URL`.
