#!/usr/bin/env bash
set -euo pipefail

# NBN Status Monitor - Azure deployment script
# Prerequisites: az CLI logged in, uv installed

APP_NAME="${APP_NAME:-nbn-status}"
RESOURCE_GROUP="${RESOURCE_GROUP:-nbn-status-rg}"
LOCATION="${LOCATION:-australiaeast}"
STORAGE_NAME="${STORAGE_NAME:-nbnstatusstore}"

echo "=== Deploying $APP_NAME to $RESOURCE_GROUP ($LOCATION) ==="

# Create resource group
echo "Creating resource group..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

# Create storage account (required by Functions)
echo "Creating storage account..."
az storage account create \
  --name "$STORAGE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --output none

# Create Function App (Consumption plan, Python 3.12)
echo "Creating function app..."
az functionapp create \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --storage-account "$STORAGE_NAME" \
  --consumption-plan-location "$LOCATION" \
  --runtime python \
  --runtime-version 3.12 \
  --functions-version 4 \
  --os-type Linux \
  --output none

# Configure app settings
echo "Configuring app settings..."
if [ -f .env ]; then
  while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ "$key" =~ ^#.*$ ]] && continue
    [[ -z "$key" ]] && continue
    # Strip surrounding quotes from value
    value="${value%\'}"
    value="${value#\'}"
    value="${value%\"}"
    value="${value#\"}"
    az functionapp config appsettings set \
      --name "$APP_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --settings "$key=$value" \
      --output none 2>/dev/null || true
  done < .env
  echo "  App settings configured from .env"
else
  echo "  WARNING: No .env file found. Configure app settings manually."
fi

# Deploy the function app
echo "Deploying function code..."
func azure functionapp publish "$APP_NAME" --python

echo ""
echo "=== Deployment complete ==="
echo "Status page: https://$APP_NAME.azurewebsites.net/api/status"
echo "Timer: polls every 5 minutes automatically"
