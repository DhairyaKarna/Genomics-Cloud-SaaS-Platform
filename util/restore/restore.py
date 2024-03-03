# restore.py
#
# Restores thawed data, saving objects to S3 results bucket
# NOTE: This code is for an AWS Lambda function
#
# Copyright (C) 2015-2023 Vas Vasiliadis
# University of Chicago
##

import boto3
import json
from botocore.exceptions import ClientError

# Define constants here; no config file is used for Lambdas
DYNAMODB_TABLE = "<CNetID>_annotations"


def lambda_handler(event, context):
    # print("Received event: " + json.dumps(event, indent=2))

    # Retriving streaming body 
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/get_job_output.html
    try:
        response = glacier.get_job_output(
            vaultName=config.get('glacier', 'VaultName'),
            jobId=thaw_id
        )
        body = response['body']
    except ClientError as e:
        print("Client error when retrieving thaw job output")
    except Exception as e:
        print(f"Error retrieving thaw job output: {e}")

    data = body.read()


    try:
        response = table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='SET job_status = :status',
            ExpressionAttributeValues={
                ':status': 'COMPLETED'
            },
            ReturnValues='UPDATED_NEW'
        )
        print(f"Successfully updated job {job_id} back to COMPLETED status.")
    except ClientError as e:
        print(f"Error updating DynamoDB: {e.response['Error']['Message']}")
        break
    except Exception as e:
        print(f"Unexpected error when updating DynamoDB: {str(e)}")
        break

### EOF
