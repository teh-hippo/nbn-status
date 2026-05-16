variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
  default     = "ff42784f-c735-4dd7-81df-cf3e5f853dd5"
}

variable "tenant_id" {
  type        = string
  description = "Entra ID tenant id."
  default     = "d6c43692-ea82-403a-b890-2fa99de3b7a6"
}

variable "resource_group_name" {
  type    = string
  default = "nbn-status-rg"
}

variable "location" {
  type    = string
  default = "australiaeast"
}

variable "app_name" {
  type        = string
  description = "Randomised name for the new Flex Consumption Function App. Must be globally unique on *.azurewebsites.net."
  default     = "nbn-status-604"
}

variable "easy_auth_app_object_id" {
  type        = string
  description = "Object id (not appId) of the existing Entra ID app registration used by Easy Auth."
  default     = "00b7304b-3fd4-458f-88d0-83f656f84d90"
}

variable "easy_auth_sp_object_id" {
  type        = string
  description = "Object id of the existing service principal for the Easy Auth app registration."
  default     = "a6d5d61a-65f1-41bc-8ae4-f735e28dadde"
}

variable "easy_auth_client_id" {
  type        = string
  description = "App (client) id of the Entra ID app registration used by Easy Auth."
  default     = "bd4a0ca1-2499-4a74-a0da-9a6a2bacfd91"
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
