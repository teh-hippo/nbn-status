################################################################################
# Imports of existing resources we keep as-is across the migration.
# Resource bodies live in storage.tf, insights.tf, auth.tf.
################################################################################

import {
  to = azurerm_resource_group.this
  id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-rg"
}

import {
  to = azurerm_storage_account.this
  id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-rg/providers/Microsoft.Storage/storageAccounts/examplestore"
}

import {
  to = azurerm_storage_container.state
  id = "https://examplestore.blob.core.windows.net/nbn-state"
}

import {
  to = azurerm_application_insights.this
  id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-rg/providers/Microsoft.Insights/components/nbn-status"
}

import {
  to = azuread_application.easy_auth
  id = "/applications/22222222-2222-2222-2222-222222222222"
}

import {
  to = azuread_service_principal.easy_auth
  id = "/servicePrincipals/33333333-3333-3333-3333-333333333333"
}
