locals {
  apis = [
    "run.googleapis.com", "artifactregistry.googleapis.com", "bigquery.googleapis.com",
    "storage.googleapis.com", "cloudscheduler.googleapis.com", "logging.googleapis.com",
    "monitoring.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "cloudresourcemanager.googleapis.com",
  ]
  bucket          = "${var.project_id}-batchguard"
  dataset         = "batchguard"
  predictions     = "${var.project_id}.${local.dataset}.predictions"
  audit           = "${var.project_id}.${local.dataset}.audit_log"
  placeholder_api = "us-docker.pkg.dev/cloudrun/container/hello"
  placeholder_job = "us-docker.pkg.dev/cloudrun/container/job"
}

# ---------------- APIs ----------------
resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ---------------- storage ----------------
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "batchguard"
  format        = "DOCKER"
  description   = "BatchGuard release images (one image per validated release)"
  depends_on    = [google_project_service.apis]
}

resource "google_storage_bucket" "reports" {
  name                        = local.bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  versioning { enabled = true }
  depends_on = [google_project_service.apis]
}

resource "google_bigquery_dataset" "main" {
  dataset_id                 = local.dataset
  location                   = "US"
  description                = "BatchGuard predictions and release audit trail"
  delete_contents_on_destroy = true
  depends_on                 = [google_project_service.apis]
}

resource "google_bigquery_table" "predictions" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "predictions"
  deletion_protection = false
  time_partitioning {
    type  = "DAY"
    field = "ts"
  }
  schema = jsonencode([
    { name = "request_id", type = "STRING", mode = "REQUIRED" },
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "revision", type = "STRING", mode = "NULLABLE" },
    { name = "model_version", type = "STRING", mode = "NULLABLE" },
    { name = "batch_id", type = "STRING", mode = "NULLABLE" },
    { name = "line", type = "STRING", mode = "NULLABLE" },
    { name = "supplier", type = "STRING", mode = "NULLABLE" },
    { name = "risk", type = "FLOAT", mode = "NULLABLE" },
    { name = "decision", type = "STRING", mode = "NULLABLE" },
    { name = "features", type = "STRING", mode = "NULLABLE", description = "monitored features as JSON" },
    { name = "top_factors", type = "STRING", mode = "REPEATED" },
  ])
}

resource "google_bigquery_table" "audit" {
  dataset_id          = google_bigquery_dataset.main.dataset_id
  table_id            = "audit_log"
  deletion_protection = false
  description         = "Append-only record of every release event"
  time_partitioning {
    type  = "DAY"
    field = "ts"
  }
  schema = jsonencode([
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "event", type = "STRING", mode = "REQUIRED" },
    { name = "actor", type = "STRING", mode = "NULLABLE" },
    { name = "git_sha", type = "STRING", mode = "NULLABLE" },
    { name = "run_url", type = "STRING", mode = "NULLABLE" },
    { name = "image", type = "STRING", mode = "NULLABLE" },
    { name = "revision", type = "STRING", mode = "NULLABLE" },
    { name = "model_version", type = "STRING", mode = "NULLABLE" },
    { name = "details", type = "STRING", mode = "NULLABLE", description = "JSON" },
  ])
}

# ---------------- service accounts ----------------
resource "google_service_account" "runtime" {
  account_id   = "batchguard-runtime"
  display_name = "BatchGuard API + monitoring job"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "deployer" {
  account_id   = "batchguard-deployer"
  display_name = "BatchGuard GitHub Actions releases"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "scheduler" {
  account_id   = "batchguard-scheduler"
  display_name = "Triggers the BatchGuard monitoring job"
  depends_on   = [google_project_service.apis]
}

# runtime: write predictions, query them, write reports and logs
resource "google_bigquery_dataset_iam_member" "runtime_data" {
  dataset_id = google_bigquery_dataset.main.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_project" {
  for_each = toset(["roles/bigquery.jobUser", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_reports" {
  bucket = google_storage_bucket.reports.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# deployer: push images, deploy revisions and move traffic, write the audit trail, read logs for canary checks
resource "google_project_iam_member" "deployer_project" {
  for_each = toset(["roles/run.admin", "roles/bigquery.jobUser", "roles/logging.viewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_bigquery_dataset_iam_member" "deployer_audit" {
  dataset_id = google_bigquery_dataset.main.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# ---------------- GitHub sign-in (Workload Identity Federation, no keys) ----------------
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-batchguard"
  display_name              = "GitHub Actions (BatchGuard)"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == '${var.github_repo}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# lets the workflows mint identity tokens to call the private service (operational qualification tests)
resource "google_service_account_iam_member" "github_mints_id_tokens" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.serviceAccountOpenIdTokenCreator"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# ---------------- Cloud Run service (private) ----------------
# Terraform creates it with a placeholder image; GitHub Actions owns revisions and traffic afterwards.
resource "google_cloud_run_v2_service" "api" {
  name                = var.service_name
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  custom_audiences    = [var.service_name]

  template {
    service_account = google_service_account.runtime.email
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    containers {
      image = local.placeholder_api
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
    }
  }

  lifecycle {
    ignore_changes = all
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "deployer_invokes" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# ---------------- monitoring job + daily schedule ----------------
resource "google_cloud_run_v2_job" "monitor" {
  name                = "${var.service_name}-monitor"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1
    template {
      service_account = google_service_account.runtime.email
      max_retries     = 1
      timeout         = "600s"
      containers {
        image   = local.placeholder_job
        command = ["python", "-m", "batchguard.monitor"]
        env {
          name  = "PREDICTION_SINK"
          value = "bigquery:${local.predictions}"
        }
        env {
          name  = "REPORTS_URI"
          value = "gs://${local.bucket}/reports"
        }
        resources {
          limits = { cpu = "1", memory = "1Gi" }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = all
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_job" {
  name     = google_cloud_run_v2_job.monitor.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "monitor_daily" {
  name      = "${var.service_name}-monitor-daily"
  region    = var.region
  schedule  = var.monitor_schedule
  time_zone = var.monitor_time_zone

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.monitor.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_runs_job]
}

# ---------------- alerting ----------------
resource "google_monitoring_notification_channel" "email" {
  display_name = "BatchGuard alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "drift_alerts" {
  name   = "batchguard_drift_alerts"
  filter = "resource.type=\"cloud_run_job\" AND jsonPayload.message=\"batchguard monitoring\" AND severity=\"WARNING\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "drift" {
  display_name = "BatchGuard: process or material drift"
  combiner     = "OR"
  conditions {
    display_name = "Monitoring job reported drift"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.drift_alerts.name}\" AND resource.type=\"cloud_run_job\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  documentation {
    content   = "The daily BatchGuard monitoring job found drift. Read the latest report: gs://${local.bucket}/reports/monitoring/latest.md. It names the likely cause (raw material vs. sensor calibration)."
    mime_type = "text/markdown"
  }
  notification_channels = [google_monitoring_notification_channel.email.name]
}

resource "google_monitoring_alert_policy" "server_errors" {
  display_name = "BatchGuard: API server errors"
  combiner     = "OR"
  conditions {
    display_name = "5xx responses"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.service_name=\"${var.service_name}\" AND metric.label.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.name]
}
