#!/usr/bin/env bash
# Pulls runtime secrets from the live Linux Consumption Function App and
# exports them as TF_VAR_* environment variables. Source this before running
# terraform plan/apply so the new Flex Function App is configured with the
# same Easy Auth secret, ntfy topic, and address list.
#
# Usage: source infra/load-secrets.sh

set -euo pipefail

OLD_APP_NAME="${OLD_APP_NAME:-nbn-status}"
RG="${RESOURCE_GROUP:-nbn-status-rg}"

_get() {
  az functionapp config appsettings list \
    --resource-group "$RG" \
    --name "$OLD_APP_NAME" \
    --query "[?name=='$1'].value | [0]" \
    --output tsv
}

export TF_VAR_microsoft_provider_authentication_secret
TF_VAR_microsoft_provider_authentication_secret="$(_get MICROSOFT_PROVIDER_AUTHENTICATION_SECRET)"

export TF_VAR_ntfy_topic
TF_VAR_ntfy_topic="$(_get NTFY_TOPIC)"

export TF_VAR_nbn_addresses
TF_VAR_nbn_addresses="$(_get NBN_ADDRESSES)"

if [ -z "$TF_VAR_microsoft_provider_authentication_secret" ] || \
   [ -z "$TF_VAR_ntfy_topic" ] || \
   [ -z "$TF_VAR_nbn_addresses" ]; then
  echo "ERROR: one or more secrets are empty. Check that you are logged in to az and have access to '$OLD_APP_NAME'." >&2
  return 1 2>/dev/null || exit 1
fi

echo "TF_VAR_microsoft_provider_authentication_secret: <set, ${#TF_VAR_microsoft_provider_authentication_secret} chars>"
echo "TF_VAR_ntfy_topic: <set, ${#TF_VAR_ntfy_topic} chars>"
echo "TF_VAR_nbn_addresses: <set, ${#TF_VAR_nbn_addresses} chars>"
