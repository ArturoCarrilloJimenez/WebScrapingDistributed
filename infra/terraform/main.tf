# ==============================================================================
# WebScrapingDistributed — Terraform Infrastructure as Code (AWS SQS & IAM)
# ==============================================================================

variable "aws_region" {
  type        = string
  default     = "eu-central-1"
  description = "Región de AWS para las colas SQS e IAM (eu-central-1 Fráncfort recomendada)"
}

variable "is_local" {
  type        = bool
  default     = false
  description = "Define si el despliegue es contra Floci/LocalStack (true) o AWS real (false)"
}

# --- AWS Provider ---
provider "aws" {
  region                      = var.aws_region
  access_key                  = var.is_local ? "test" : null
  secret_key                  = var.is_local ? "test" : null
  skip_credentials_validation = var.is_local
  skip_metadata_api_check     = var.is_local
  skip_requesting_account_id  = var.is_local

  dynamic "endpoints" {
    for_each = var.is_local ? [1] : []
    content {
      sqs = "http://localhost:31000"
    }
  }
}

# ==============================================================================
# 1. Colas Amazon SQS & Dead Letter Queues (DLQ)
# ==============================================================================

# DLQ para tareas estáticas (14 días de retención)
resource "aws_sqs_queue" "scraping_tasks_dlq_static" {
  name                      = "scraping-tasks-dlq-static"
  receive_wait_time_seconds = 20
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = {
    Project     = "WebScrapingDistributed"
    Environment = var.is_local ? "local" : "production"
    Type        = "DLQ-Static"
  }
}

# Cola principal para tareas estáticas (Fast HTML / httpx / curl_cffi)
resource "aws_sqs_queue" "scraping_tasks_static" {
  name                       = "scraping-tasks-static"
  visibility_timeout_seconds = 300
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scraping_tasks_dlq_static.arn
    maxReceiveCount     = 10
  })

  tags = {
    Project     = "WebScrapingDistributed"
    Environment = var.is_local ? "local" : "production"
    Type        = "Worker-Static"
  }
}

# DLQ para tareas dinámicas (14 días de retención)
resource "aws_sqs_queue" "scraping_tasks_dlq_dynamic" {
  name                      = "scraping-tasks-dlq-dynamic"
  receive_wait_time_seconds = 20
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = {
    Project     = "WebScrapingDistributed"
    Environment = var.is_local ? "local" : "production"
    Type        = "DLQ-Dynamic"
  }
}

# Cola principal para tareas dinámicas (Playwright Headless Chromium)
resource "aws_sqs_queue" "scraping_tasks_dynamic" {
  name                       = "scraping-tasks-dynamic"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scraping_tasks_dlq_dynamic.arn
    maxReceiveCount     = 10
  })

  tags = {
    Project     = "WebScrapingDistributed"
    Environment = var.is_local ? "local" : "production"
    Type        = "Worker-Dynamic"
  }
}

# ==============================================================================
# 2. Seguridad & IAM: Usuario con Menor Privilegio para la VPS Hetzner
# ==============================================================================

resource "aws_iam_user" "scraper_bot" {
  name = "scraper_vps_bot"

  tags = {
    Project     = "WebScrapingDistributed"
    Role        = "Worker and Producer SQS Broker Client"
    Environment = var.is_local ? "local" : "production"
  }
}

resource "aws_iam_access_key" "scraper_bot_key" {
  user = aws_iam_user.scraper_bot.name
}

# Política restrictiva: solo permite operar sobre las colas del proyecto
resource "aws_iam_user_policy" "scraper_bot_sqs_policy" {
  name = "ScraperSQSLeastPrivilegePolicy"
  user = aws_iam_user.scraper_bot.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSQSOperationsOnScrapingQueues"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = [
          aws_sqs_queue.scraping_tasks_static.arn,
          aws_sqs_queue.scraping_tasks_dlq_static.arn,
          aws_sqs_queue.scraping_tasks_dynamic.arn,
          aws_sqs_queue.scraping_tasks_dlq_dynamic.arn
        ]
      },
      {
        Sid      = "AllowListQueues"
        Effect   = "Allow"
        Action   = ["sqs:ListQueues"]
        Resource = "*"
      }
    ]
  })
}

# ==============================================================================
# 3. Outputs (Valores listos para inyectar en 02-secret.yaml de Kubernetes)
# ==============================================================================

output "aws_region" {
  description = "Región de AWS configurada"
  value       = var.aws_region
}

output "sqs_endpoint_url" {
  description = "Endpoint oficial de SQS en la región seleccionada"
  value       = "https://sqs.${var.aws_region}.amazonaws.com"
}

output "sqs_queue_url_static" {
  description = "URL de la cola SQS estática para 02-secret.yaml"
  value       = aws_sqs_queue.scraping_tasks_static.url
}

output "sqs_queue_url_dynamic" {
  description = "URL de la cola SQS dinámica para 02-secret.yaml"
  value       = aws_sqs_queue.scraping_tasks_dynamic.url
}

output "scraper_access_key_id" {
  description = "Access Key ID del usuario IAM scraper-vps-bot para 02-secret.yaml"
  value       = aws_iam_access_key.scraper_bot_key.id
}

output "scraper_secret_access_key" {
  description = "Secret Access Key del usuario IAM scraper-vps-bot para 02-secret.yaml"
  value       = aws_iam_access_key.scraper_bot_key.secret
  sensitive   = true
}
