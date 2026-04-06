#!/bin/sh
echo "----------- Initializing Infrastructure -----------"

# Crear la cola SQS
awslocal  sqs create-queue \
 --queue-name message-to-sqs.fifo \
 --attributes FifoQueue=true

 echo "----------- Initializing Ready -----------"