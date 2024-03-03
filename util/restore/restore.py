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
DYNAMODB_TABLE = "dhairyakarna_annotations"
GLACIER_VAULT_NAME = "ucmpcs"
AWS_REGION_NAME = "us-east-1"


# Initiating AWS clients 
s3 = boto3.client('s3', region_name=AWS_REGION_NAME)
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION_NAME)
table = dynamodb.Table(DYNAMODB_TABLE)
glacier = boto3.client('glacier', region_name=AWS_REGION_NAME)

def lambda_handler(event, context):
    # print("Received event: " + json.dumps(event, indent=2))

     # Extract job details from the event
    job_id = event['job_id']
    thaw_id = event['thaw_id']
    archive_id = event['archive_id']
    s3_results_bucket = event['s3_results_bucket']
    s3_key_result_file = event['s3_key_result_file']
    
    # Retriving streaming body 
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/get_job_output.html
    try:
        response = glacier.get_job_output(
            vaultName=GLACIER_VAULT_NAME,
            jobId=thaw_id
        )
        body = response['body']
    except ClientError as e:
        print("Client error when retrieving thaw job output")
        return {
            'statusCode': 500,
            'body': json.dumps('Client error when retrieving thaw job output')
        }
    except Exception as e:
        print(f"Error retrieving thaw job output: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Error retrieving thaw job output')
        }

    data = body.read()
    
    
    # Upload the retrieved content to S3
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/put_object.html
    try:
        s3_client.put_object(Bucket=s3_results_bucket, Key=s3_key_result_file, Body=data)
    except ClientError as e:
        print("Client error when putting output to s3")
        return {
            'statusCode': 500,
            'body': json.dumps('Client error when putting output to s3')
        }
    except Exception as e:
        print(f"Error putting output to s3: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Error copying object back to S3')
        }
    
    # Delete the archive from Glacier
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/delete_archive.html
    try:
        glacier.delete_archive(vaultName=GLACIER_VAULT, archiveId=archive_id)
        print(f"Archive {archive_id} deleted from Glacier successfully.")
    except ClientError as e:
        print(f"Error deleting archive from Glacier: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps('Error deleting archive from Glacier.')
        }
    except Exception as e:
        print(f"Error deleting archive: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Error deleting archive')
        }
        
    # Update DynamoDB item to mark as completed
    try:
        table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='SET job_status = :status REMOVE results_file_archive_id',
            ExpressionAttributeValues={':status': 'COMPLETED'}
        )
    except ClientError as e:
        print(f"Error updating DynamoDB: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps('Error updating job status in DynamoDB.')
        }
    except Exception as e:
        print(f"Error updating DynamoDB: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Unexpected error when updating DynamoDB')
        }
    
    
    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }


### EOF
