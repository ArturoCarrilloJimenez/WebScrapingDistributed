#!/bin/sh
set -e 

echo "----------- Initializing Resilient Infrastructure -----------"

# Delete existing queues to avoid attribute conflicts if database is persistent
awslocal sqs delete-queue --queue-url "http://localhost:4566/000000000000/scraping-tasks" 2>/dev/null || true
awslocal sqs delete-queue --queue-url "http://localhost:4566/000000000000/scraping-tasks-dlq" 2>/dev/null || true

# 1. Crear la DLQ y CAPTURAR su URL inmediatamente
DLQ_URL=$(awslocal sqs create-queue \
    --queue-name scraping-tasks-dlq \
    --query 'QueueUrl' \
    --output text)

echo "DLQ created: $DLQ_URL"

# 2. Obtener el ARN usando la URL que acabamos de capturar
DLQ_ARN=$(awslocal sqs get-queue-attributes \
    --queue-url "$DLQ_URL" \
    --attribute-names QueueArn \
    --query 'Attributes.QueueArn' \
    --output text)

echo "DLQ ARN: $DLQ_ARN"

# 3. Crear la Cola Principal vinculada a la DLQ
awslocal sqs create-queue \
    --queue-name scraping-tasks \
    --attributes '{
        "VisibilityTimeout": "300",
        "RedrivePolicy": "{\"deadLetterTargetArn\":\"'"$DLQ_ARN"'\",\"maxReceiveCount\":\"10\"}"
    }'

echo "Main queue 'scraping-tasks' created and linked to DLQ successfully."

# 4. Crear el Bucket S3 para elData Lake
# Mapeado directamente con settings.s3_bucket_name
awslocal s3 mb s3://scraping-raw-data || true

echo "S3 Bucket 'scraping-raw-data' created successfully."

echo "----------- Infrastructure Ready -----------"