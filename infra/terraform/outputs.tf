output "github_variables" {
  description = "Values for GitHub → Settings → Secrets and variables → Actions → Variables"
  value = {
    GCP_PROJECT_ID    = var.project_id
    GCP_REGION        = var.region
    SERVICE_NAME      = var.service_name
    IMAGE_REPO        = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
    RUNTIME_SA        = google_service_account.runtime.email
    DEPLOYER_SA       = google_service_account.deployer.email
    WIF_PROVIDER      = google_iam_workload_identity_pool_provider.github.name
    PREDICTIONS_TABLE = local.predictions
    AUDIT_TABLE       = local.audit
    REPORTS_URI       = "gs://${local.bucket}/reports"
  }
}

output "service_url" {
  value = google_cloud_run_v2_service.api.uri
}
