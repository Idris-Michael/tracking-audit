output "api_url" {
  description = "Public URL of the API and operator console."
  value       = "https://${azurerm_container_app.this["api"].ingress[0].fqdn}"
}

output "key_vault_name" {
  description = "Set the app secrets here after apply."
  value       = azurerm_key_vault.this.name
}
