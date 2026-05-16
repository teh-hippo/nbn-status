################################################################################
# Entra ID app registration used by Easy Auth on the Function App.
# Reused across the migration. Redirect URIs:
#   - Add the new app's hostname during Phase 2.
#   - Remove the old app's hostname and the retired custom domain in Phase 4
#     (cleanup-entra-redirects).
################################################################################

resource "azuread_application" "easy_auth" {
  display_name     = "nbn-status-auth"
  sign_in_audience = "AzureADMyOrg"

  web {
    redirect_uris = [
      "https://${local.default_hostname}/.auth/login/aad/callback",
    ]
    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = true
    }
  }

  # The client secret itself is not managed by Terraform. It was created
  # out of band and lives in the Function App's
  # MICROSOFT_PROVIDER_AUTHENTICATION_SECRET app setting. Rotation is a
  # separate, deliberate operation.
  lifecycle {
    ignore_changes = [
      password,
      identifier_uris,
      api,
      required_resource_access,
      app_role,
      optional_claims,
      tags,
    ]
  }
}

resource "azuread_service_principal" "easy_auth" {
  client_id = azuread_application.easy_auth.client_id

  lifecycle {
    ignore_changes = [
      tags,
      app_role_assignment_required,
    ]
  }
}
