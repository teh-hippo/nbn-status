variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
  default     = "00000000-0000-0000-0000-000000000000"
}

variable "tenant_id" {
  type        = string
  description = "Entra ID tenant id."
  default     = "11111111-1111-1111-1111-111111111111"
}

variable "resource_group_name" {
  type    = string
  default = "example-rg"
}

variable "location" {
  type    = string
  default = "australiaeast"
}

variable "app_name" {
  type        = string
  description = "Randomised name for the new Flex Consumption Function App. Must be globally unique on *.azurewebsites.net."
  default     = "example-app"
}

variable "easy_auth_app_object_id" {
  type        = string
  description = "Object id (not appId) of the existing Entra ID app registration used by Easy Auth."
  default     = "22222222-2222-2222-2222-222222222222"
}

variable "easy_auth_sp_object_id" {
  type        = string
  description = "Object id of the existing service principal for the Easy Auth app registration."
  default     = "33333333-3333-3333-3333-333333333333"
}

variable "easy_auth_client_id" {
  type        = string
  description = "App (client) id of the Entra ID app registration used by Easy Auth."
  default     = "44444444-4444-4444-4444-444444444444"
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
