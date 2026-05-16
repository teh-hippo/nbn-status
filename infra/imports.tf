################################################################################
# Imports of existing resources we keep as-is across the migration.
# Resource bodies live in storage.tf, insights.tf, auth.tf.
################################################################################

import {
  to = azurerm_resource_group.this
  id = "/subscriptions/ff42784f-c735-4dd7-81df-cf3e5f853dd5/resourceGroups/nbn-status-rg"
}

import {
  to = azurerm_storage_account.this
  id = "/subscriptions/ff42784f-c735-4dd7-81df-cf3e5f853dd5/resourceGroups/nbn-status-rg/providers/Microsoft.Storage/storageAccounts/nbnstatusstore"
}

import {
  to = azurerm_storage_container.state
  id = "https://nbnstatusstore.blob.core.windows.net/nbn-state"
}

import {
  to = azurerm_application_insights.this
  id = "/subscriptions/ff42784f-c735-4dd7-81df-cf3e5f853dd5/resourceGroups/nbn-status-rg/providers/Microsoft.Insights/components/nbn-status"
}

import {
  to = azuread_application.easy_auth
  id = "/applications/00b7304b-3fd4-458f-88d0-83f656f84d90"
}

import {
  to = azuread_service_principal.easy_auth
  id = "/servicePrincipals/a6d5d61a-65f1-41bc-8ae4-f735e28dadde"
}
