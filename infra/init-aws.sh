#!/bin/sh
echo "----------- Initializing Resilient Infrastructure -----------"

# 1. Crear la Dead Letter Queue (DLQ)
awslocal sqs create-queue --queue-name scraping-tasks-dlq

# 2. Obtener el ARN de la DLQ (Necesario para vincularlas)
DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/scraping-tasks-dlq \
  --attribute-names QueueArn | jq -r '.Attributes.QueueArn')

# 3. Crear la Cola Principal con Redrive Policy y Visibility Timeout
awslocal sqs create-queue \
  --queue-name scraping-tasks \
  --attributes '{
    "VisibilityTimeout": "60",
    "RedrivePolicy": "{\"deadLetterTargetArn\":\"'"$DLQ_ARN"'\",\"maxReceiveCount\":\"3\"}"
  }'

echo "----------- Infrastructure Ready: main & dlq created -----------"