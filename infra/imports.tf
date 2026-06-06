################################################################################
# Imports of existing resources we keep as-is across the migration.
# Resource bodies live in storage.tf, insights.tf, auth.tf.
################################################################################

import {
  to = azurerm_resource_group.this
  id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
}

import {
  to = azurerm_storage_account.this
  id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.Storage/storageAccounts/${var.storage_account_name}"
}

import {
  to = azurerm_storage_container.state
  id = "https://${var.storage_account_name}.blob.core.windows.net/nbn-state"
}

import {
  to = azurerm_application_insights.this
  id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.Insights/components/${var.app_insights_name}"
}

import {
  to = azuread_application.easy_auth
  id = "/applications/${var.easy_auth_app_object_id}"
}

import {
  to = azuread_service_principal.easy_auth
  id = "/servicePrincipals/${var.easy_auth_sp_object_id}"
}
