locals {
  default_hostname = "${var.app_name}.azurewebsites.net"
  status_page_url  = "https://${local.default_hostname}/"

  # Distinct host id so the new app's Functions runtime does not collide with
  # the old Linux Consumption app while both share nbnstatusstore. Removed
  # implicitly when the old app is destroyed in Phase 4.
  azure_functions_host_id = "flex-${var.app_name}"
}
