output "function_app_name" {
  value = azurerm_function_app_flex_consumption.this.name
}

output "function_app_default_hostname" {
  value = azurerm_function_app_flex_consumption.this.default_hostname
}

output "function_app_principal_id" {
  value       = azurerm_function_app_flex_consumption.this.identity[0].principal_id
  description = "System-assigned MSI principal id; required for any out-of-band RBAC grants."
}

output "status_page_url" {
  value = local.status_page_url
}
