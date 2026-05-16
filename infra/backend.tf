terraform {
  backend "azurerm" {
    resource_group_name  = "nbn-status-rg"
    storage_account_name = "nbnstatusstore"
    container_name       = "tfstate"
    key                  = "nbn-status.tfstate"
    use_azuread_auth     = false
  }
}
