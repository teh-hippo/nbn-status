terraform {
  # Partial backend configuration. The sensitive resource group / storage
  # account names are supplied at init time via a gitignored backend.hcl:
  #   terraform init -backend-config=backend.hcl
  # See backend.hcl.example.
  backend "azurerm" {
    use_azuread_auth = false
  }
}
