variable "project_id" {
  description = "Google Cloud project ID"
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry, Scheduler and the bucket"
  type        = string
  default     = "us-east1"
}

variable "github_repo" {
  description = "GitHub repository allowed to deploy, as owner/name (case-sensitive)"
  type        = string
}

variable "alert_email" {
  description = "Email address that receives drift and error alerts"
  type        = string
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "batchguard"
}

variable "monitor_schedule" {
  description = "Cron schedule for the monitoring job (in monitor_time_zone)"
  type        = string
  default     = "0 7 * * *"
}

variable "monitor_time_zone" {
  type    = string
  default = "America/New_York"
}
