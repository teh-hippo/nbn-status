# infra/

Terraform module for the nbn-status Function App on Azure Flex Consumption.

## Layout

- `versions.tf` — provider pins (`azurerm ~> 4.63`, `azuread ~> 3.0`).
- `backend.tf` — state in `examplestore/tfstate/nbn-status.tfstate`.
- `variables.tf` — inputs. Sensitive ones (`microsoft_provider_authentication_secret`, `ntfy_topic`, `nbn_addresses`) come from `TF_VAR_*` env vars; `load-secrets.sh` populates them from the live Linux Consumption app.
- `main.tf` — locals.
- `imports.tf` — HCL-native import blocks for resources kept across the migration (resource group, storage account, `nbn-state` container, App Insights, Entra ID app reg, Entra ID service principal). Auto-removed by Terraform on first apply.
- `storage.tf`, `insights.tf`, `auth.tf` — the imported resources' bodies (with `lifecycle { prevent_destroy = true }` on the keepers).
- `app.tf` — Flex Consumption plan (`FC1`), the Function App, and the container-scoped MSI role assignment.
- `outputs.tf` — handy outputs (default hostname, MSI principal id).

## Constraints

- Linux Consumption is in feature freeze; this module deliberately replaces it with a Flex Consumption Function App at a new randomised name. See `AGENTS.md` "Runtime constraints" / "Runtime invariants".
- Flex does not support App Service Managed Certificates, so the previous custom hostname (`app.example.com`) is dropped and Microsoft's auto-supplied cert on the default `*.azurewebsites.net` is used instead.
- `examplestore.allowSharedKeyAccess` MUST stay `true`; the azurerm Terraform backend uses shared-key auth in this configuration.
- The Flex Function App's MSI gets `Storage Blob Data Contributor` on the `flex-deploy` container only — never on the storage account or on `nbn-state` / `tfstate`.

## Apply workflow

```bash
# Pull sensitive runtime config from the live Linux Consumption app.
source infra/load-secrets.sh

cd infra
terraform init
terraform plan
terraform apply
```

The first plan should show:
- Imports (no drift) for: resource group, storage account, `nbn-state`, App Insights, Entra ID app reg + SP.
- Creates for: `azurerm_service_plan.flex`, `azurerm_function_app_flex_consumption.this`, `azurerm_storage_container.flex_deploy`, `azurerm_role_assignment.flex_deploy_writer`.
- Updates: `azuread_application.easy_auth.web.redirect_uris` (adding the new hostname).

No destroys until Phase 4's old-app teardown step.

## Phase 4 old-app teardown

After the new app is validated and the cutover-flip is done, the old
Linux Consumption Function App (`nbn-status`), its Consumption plan
(`AustraliaEastLinuxDynamicPlan`), the App Service Managed Certificate
(`app.example.com`), and the custom hostname binding are added
to this module via further `import` blocks and immediately destroyed in the
same apply. The `nbn-status.azurewebsites.net` and
`app.example.com` redirect URIs are then removed from the
Entra ID app reg.
