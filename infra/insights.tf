################################################################################
# Application Insights component. Both the old (Linux Consumption) and the
# new (Flex Consumption) Function Apps emit telemetry here, so the metric
# history survives the migration. Imported, marked prevent_destroy.
################################################################################

resource "azurerm_application_insights" "this" {
  name                = var.app_insights_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  application_type    = "web"
  sampling_percentage = 0

  lifecycle {
    prevent_destroy = true
  }
}
