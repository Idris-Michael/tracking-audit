variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "uksouth"
}

variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "taudit"
}

variable "image" {
  description = "Container image, e.g. myregistry.azurecr.io/tracking-audit:1.0.0"
  type        = string
}

variable "operator_ip_ranges" {
  description = "CIDR ranges allowed to reach Key Vault's data plane (deployment pipeline, operators)."
  type        = list(string)
  default     = []
}

variable "worker_replicas" {
  description = "Worker replicas; Postgres SKIP LOCKED lets them run in parallel."
  type        = number
  default     = 2
}

variable "prepared_by" {
  description = "Name in the scorecard footer."
  type        = string
  default     = "Fieldnote Digital"
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = { project = "tracking-audit", environment = "pilot" }
}
