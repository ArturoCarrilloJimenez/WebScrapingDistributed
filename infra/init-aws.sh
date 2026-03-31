#!/bin/bash
awslocal sqs create-queue --queue-name scraping-tasks-queue
awslocal s3 mb s3://raw-data-tfm
echo "Infrastructure initialized: SQS queue and S3 bucket created."