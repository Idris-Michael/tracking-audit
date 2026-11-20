output "api_dns_name" {
  description = "Point the API's DNS record at this load balancer."
  value       = aws_lb.api.dns_name
}

output "secret_names" {
  description = "Set these values after apply (aws secretsmanager put-secret-value)."
  value       = [for s in aws_secretsmanager_secret.app : s.name]
}
