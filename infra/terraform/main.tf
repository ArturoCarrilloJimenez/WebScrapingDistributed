# --- Provider parametrizable (Local vs AWS) ---
variable "is_local" {
  type        = bool
  default     = true
  description = "Define si el despliegue es contra Floci/LocalStack o AWS real"
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = var.is_local ? "test" : null
  secret_key                  = var.is_local ? "test" : null
  skip_credentials_validation = var.is_local
  skip_metadata_api_check     = var.is_local
  skip_requesting_account_id  = var.is_local
  s3_use_path_style           = var.is_local

  dynamic "endpoints" {
    for_each = var.is_local ? [1] : []
    content {
      sqs = "http://localhost:31000"
      s3  = "http://localhost:31000"
    }
  }
}

# --- Migración de estado sin destrucción ---
moved {
  from = aws_s3_bucket.scraping-data-lake
  to   = aws_s3_bucket.scraping_data_lake
}

moved {
  from = aws_sqs_queue.scraping-tasks-dlq-static
  to   = aws_sqs_queue.scraping_tasks_dlq_static
}

moved {
  from = aws_sqs_queue.scraping-tasks-static
  to   = aws_sqs_queue.scraping_tasks_static
}

moved {
  from = aws_sqs_queue.scraping-tasks-dlq-dynamic
  to   = aws_sqs_queue.scraping_tasks_dlq_dynamic
}

moved {
  from = aws_sqs_queue.scraping-tasks-dynamic
  to   = aws_sqs_queue.scraping_tasks_dynamic
}

# --- Data Lake Principal ---
resource "aws_s3_bucket" "scraping_data_lake" {
  bucket = "scraping-data-lake"

  tags = {
    Name        = "scraping-data-lake"
    Environment = var.is_local ? "local" : "production"
  }
}

# Bloqueo estricto de acceso público para Data Lake Principal
resource "aws_s3_bucket_public_access_block" "scraping_data_lake" {
  bucket                  = aws_s3_bucket.scraping_data_lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Cifrado en reposo predeterminado AES256
resource "aws_s3_bucket_server_side_encryption_configuration" "scraping_data_lake" {
  bucket = aws_s3_bucket.scraping_data_lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Control de versiones para evitar borrado accidental
resource "aws_s3_bucket_versioning" "scraping_data_lake" {
  bucket = aws_s3_bucket.scraping_data_lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Política de obligatoriedad de acceso por HTTPS (Cifrado en Tránsito)
resource "aws_s3_bucket_policy" "scraping_data_lake" {
  count  = var.is_local ? 0 : 1
  bucket = aws_s3_bucket.scraping_data_lake.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnforceHTTPSOnly"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.scraping_data_lake.arn,
          "${aws_s3_bucket.scraping_data_lake.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# --- Infraestructura de Logging ---
resource "aws_s3_bucket" "scraping_data_lake_logs" {
  bucket = "scraping-data-lake-logs"

  tags = {
    Name        = "scraping-data-lake-logs"
    Environment = var.is_local ? "local" : "production"
  }
}

# Bloqueo estricto de acceso público para Bucket de Logs
resource "aws_s3_bucket_public_access_block" "scraping_data_lake_logs" {
  bucket                  = aws_s3_bucket.scraping_data_lake_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Cifrado en reposo para Bucket de Logs
resource "aws_s3_bucket_server_side_encryption_configuration" "scraping_data_lake_logs" {
  bucket = aws_s3_bucket.scraping_data_lake_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Control de versiones para Bucket de Logs
resource "aws_s3_bucket_versioning" "scraping_data_lake_logs" {
  bucket = aws_s3_bucket.scraping_data_lake_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Permisos de entrega de logs para el bucket receptor (AWS log-delivery-write)
resource "aws_s3_bucket_ownership_controls" "scraping_data_lake_logs" {
  bucket = aws_s3_bucket.scraping_data_lake_logs.id

  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "scraping_data_lake_logs" {
  depends_on = [aws_s3_bucket_ownership_controls.scraping_data_lake_logs]

  bucket = aws_s3_bucket.scraping_data_lake_logs.id
  acl    = "log-delivery-write"
}

# Política de obligatoriedad de acceso por HTTPS para Bucket de Logs
resource "aws_s3_bucket_policy" "scraping_data_lake_logs" {
  count  = var.is_local ? 0 : 1
  bucket = aws_s3_bucket.scraping_data_lake_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnforceHTTPSOnly"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.scraping_data_lake_logs.arn,
          "${aws_s3_bucket.scraping_data_lake_logs.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# Registro de accesos del Data Lake hacia el Bucket de Logs (Solo en Producción AWS)
resource "aws_s3_bucket_logging" "scraping_data_lake" {
  count         = var.is_local ? 0 : 1
  bucket        = aws_s3_bucket.scraping_data_lake.id
  target_bucket = aws_s3_bucket.scraping_data_lake_logs.id
  target_prefix = "access-logs/"
}

# Purga automática de logs a los 90 días para optimizar costes (Solo en Producción AWS)
resource "aws_s3_bucket_lifecycle_configuration" "scraping_data_lake_logs" {
  count  = var.is_local ? 0 : 1
  bucket = aws_s3_bucket.scraping_data_lake_logs.id

  rule {
    id     = "auto-delete-old-logs"
    status = "Enabled"

    filter {
      prefix = "access-logs/"
    }

    expiration {
      days = 90
    }
  }
}

# --- Colas SQS Estáticas & DLQ ---
resource "aws_sqs_queue" "scraping_tasks_dlq_static" {
  name                      = "scraping-tasks-dlq-static"
  receive_wait_time_seconds = 20
  message_retention_seconds = 1209600 # 14 días
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "scraping_tasks_static" {
  name                       = "scraping-tasks-static"
  visibility_timeout_seconds = 300
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scraping_tasks_dlq_static.arn
    maxReceiveCount     = 10
  })
}

# --- Colas SQS Dinámicas (Playwright) & DLQ ---
resource "aws_sqs_queue" "scraping_tasks_dlq_dynamic" {
  name                      = "scraping-tasks-dlq-dynamic"
  receive_wait_time_seconds = 20
  message_retention_seconds = 1209600 # 14 días
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "scraping_tasks_dynamic" {
  name                       = "scraping-tasks-dynamic"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scraping_tasks_dlq_dynamic.arn
    maxReceiveCount     = 10
  })
}
