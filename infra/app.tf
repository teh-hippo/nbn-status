################################################################################
# Flex Consumption plan (FC1) and Function App.
#
# The plan can host exactly one Function App. Deployment is via One Deploy to
# the flex-deploy container, authenticated with the app's system-assigned
# managed identity (Storage Blob Data Contributor on the container only).
################################################################################

resource "azurerm_service_plan" "flex" {
  name                = "${var.app_name}-plan"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  os_type             = "Linux"
  sku_name            = "FC1"
}

resource "azurerm_function_app_flex_consumption" "this" {
  name                = var.app_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  service_plan_id     = azurerm_service_plan.flex.id

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.this.primary_blob_endpoint}${azurerm_storage_container.flex_deploy.name}"
  storage_authentication_type = "SystemAssignedIdentity"

  runtime_name           = "python"
  runtime_version        = "3.13"
  instance_memory_in_mb  = 2048
  maximum_instance_count = 40

  https_only = true

  identity {
    type = "SystemAssigned"
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.this.connection_string
  }

  app_settings = {
    AzureWebJobsStorage                      = azurerm_storage_account.this.primary_connection_string
    AzureFunctionsWebHost__hostid            = local.azure_functions_host_id
    AzureWebJobsDisableHomepage              = "true"
    REQUIRE_EASY_AUTH                        = "true"
    STATUS_PAGE_URL                          = local.status_page_url
    NTFY_SERVER                              = var.ntfy_server
    NTFY_TOPIC                               = var.ntfy_topic
    NBN_ADDRESSES                            = var.nbn_addresses
    MICROSOFT_PROVIDER_AUTHENTICATION_SECRET = var.microsoft_provider_authentication_secret
  }

  auth_settings_v2 {
    auth_enabled           = true
    runtime_version        = "~2"
    require_authentication = true
    unauthenticated_action = "RedirectToLoginPage"
    default_provider       = "azureactivedirectory"

    active_directory_v2 {
      client_id                  = var.easy_auth_client_id
      tenant_auth_endpoint       = "https://sts.windows.net/${var.tenant_id}/v2.0"
      client_secret_setting_name = "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET"
      login_parameters = {
        scope = "openid profile email"
      }
    }

    login {
      token_store_enabled = true
    }
  }

  lifecycle {
    ignore_changes = [
      # WEBSITE_AUTH_ENABLED, WEBSITE_AUTH_*, FUNCTIONS_WORKER_RUNTIME, and
      # similar platform-managed app settings can drift; ignore so plan stays
      # clean.
      tags,
    ]
  }
}

################################################################################
# Storage Blob Data Contributor on the flex-deploy container only.
# Container-scoped (least-privilege) so the MSI cannot reach nbn-state or
# tfstate.
################################################################################

resource "azurerm_role_assignment" "flex_deploy_writer" {
  scope                = azurerm_storage_container.flex_deploy.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_function_app_flex_consumption.this.identity[0].principal_id

  depends_on = [
    azurerm_function_app_flex_consumption.this,
    azurerm_storage_container.flex_deploy,
  ]
}
