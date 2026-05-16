################################################################################
# Resource group.
################################################################################

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
}

################################################################################
# Storage account. Holds:
#   - nbn-state/state.json (production snapshot, written by poll_nbn timer)
#   - flex-deploy/ (One Deploy artifact for the Flex Function App)
#   - tfstate/ (this Terraform module's backend; bootstrapped out of band)
#   - github-actions-deploy/ (legacy from the old Consumption deploy; cleaned up post-migration)
#   - azure-webjobs-* (platform-managed; not declared here)
#
# allowSharedKeyAccess MUST stay true: the azurerm Terraform backend uses
# storage account keys, not Azure AD, in this configuration.
################################################################################

resource "azurerm_storage_account" "this" {
  name                            = "examplestore"
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = true
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 30
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "state" {
  name                  = "nbn-state"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "flex_deploy" {
  name                  = "flex-deploy"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}
