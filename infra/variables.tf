variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "tenant_id" {
  type        = string
  description = "Entra ID tenant id."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group name."
}

variable "location" {
  type    = string
  default = "australiaeast"
}

variable "app_name" {
  type        = string
  description = "Randomised name for the Flex Consumption Function App. Must be globally unique on *.azurewebsites.net."
}

variable "storage_account_name" {
  type        = string
  description = "Storage account holding state, deploy artifacts, and the Terraform backend."
}

variable "app_insights_name" {
  type        = string
  description = "Application Insights component name."
}

variable "easy_auth_app_object_id" {
  type        = string
  description = "Object id (not appId) of the existing Entra ID app registration used by Easy Auth."
}

variable "easy_auth_sp_object_id" {
  type        = string
  description = "Object id of the existing service principal for the Easy Auth app registration."
}

variable "easy_auth_client_id" {
  type        = string
  description = "App (client) id of the Entra ID app registration used by Easy Auth."
}

# Sensitive runtime configuration. Populate via TF_VAR_* environment variables at
# apply time. See infra/README.md for the helper that pulls these from the live
# Linux Consumption app.
variable "microsoft_provider_authentication_secret" {
  type        = string
  sensitive   = true
  description = "Entra ID app registration client secret used by Easy Auth."
}

variable "ntfy_server" {
  type    = string
  default = "https://ntfy.sh"
}

variable "ntfy_topic" {
  type      = string
  sensitive = true
}

variable "nbn_addresses" {
  type      = string
  sensitive = true
}
