terraform {
  backend "azurerm" {
    resource_group_name  = "example-rg"
    storage_account_name = "examplestore"
    container_name       = "tfstate"
    key                  = "nbn-status.tfstate"
    use_azuread_auth     = false
  }
}
