provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    sqs = "http://localhost:31000"
    s3  = "http://localhost:31000"
  }
}


resource "aws_s3_bucket" "scraping-data-lake" {
  bucket = "scraping-data-lake"

  tags = {
    Name = "scraping-data-lake"
  }
}


resource "aws_sqs_queue" "scraping-tasks-dlq-static" {
  name                      = "scraping-tasks-dlq-static"
  receive_wait_time_seconds = 20
  message_retention_seconds = 1209600 # 14 días de margen para investigar fallos
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "scraping-tasks-static" {
  name                       = "scraping-tasks-static"
  visibility_timeout_seconds = 300
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scraping-tasks-dlq-static.arn
    maxReceiveCount     = 10
  })
}

resource "aws_sqs_queue" "scraping-tasks-dlq-dynamic" {
  name                      = "scraping-tasks-dlq-dynamic"
  receive_wait_time_seconds = 20
  message_retention_seconds = 1209600 # 14 días de margen para investigar fallos
  sqs_managed_sse_enabled   = true
}


resource "aws_sqs_queue" "scraping-tasks-dynamic" {
  name                       = "scraping-tasks-dynamic"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scraping-tasks-dlq-dynamic.arn
    maxReceiveCount     = 10
  })
}
