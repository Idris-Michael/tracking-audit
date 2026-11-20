variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-west-2"
}

variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "taudit"
}

variable "image" {
  description = "Container image URI, e.g. 123456789012.dkr.ecr.eu-west-2.amazonaws.com/tracking-audit:1.0.0"
  type        = string
}

variable "vpc_id" {
  description = "Existing VPC (most organisations hand over networking rather than let a project create it)."
  type        = string
}

variable "public_subnet_ids" {
  description = "Subnets for the load balancer."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Subnets for the tasks and the database (need NAT to reach audited sites)."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener."
  type        = string
}

variable "worker_count" {
  description = "Worker tasks; Postgres SKIP LOCKED lets them run in parallel."
  type        = number
  default     = 2
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = { project = "tracking-audit", environment = "pilot" }
}

variable "prepared_by" {
  description = "Name in the scorecard footer."
  type        = string
  default     = "Fieldnote Digital"
}
