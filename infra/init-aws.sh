#!/bin/sh
set -e 

echo "----------- Initializing Resilient Infrastructure -----------"

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
        "VisibilityTimeout": "60",
        "RedrivePolicy": "{\"deadLetterTargetArn\":\"'"$DLQ_ARN"'\",\"maxReceiveCount\":\"3\"}"
    }'

echo "----------- Infrastructure Ready -----------"