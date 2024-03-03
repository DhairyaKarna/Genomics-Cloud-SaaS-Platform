# thaw_script.py
#
# Thaws upgraded (premium) user data
#
# Copyright (C) 2015-2024 Vas Vasiliadis
# University of Chicago
##
__author__ = "Vas Vasiliadis <vas@uchicago.edu>"

import boto3
import json
import os
import sys
import time

from botocore.exceptions import ClientError

# Import utility helpers
sys.path.insert(1, os.path.realpath(os.path.pardir))
import helpers

# Get configuration
from configparser import ConfigParser, ExtendedInterpolation

config = ConfigParser(os.environ, interpolation=ExtendedInterpolation())
config.read("../util_config.ini")
config.read("thaw_script_config.ini")

"""A16
Initiate thawing of archived objects from Glacier
"""

# Initializing AWS clients with config
s3 = boto3.client('s3', region_name=config.get('aws', 'AwsRegionName'))
dynamodb = boto3.resource('dynamodb', region_name=config.get('aws', 'AwsRegionName'))
table = dynamodb.Table(config.get('gas', 'AnnotationsTable'))
glacier = boto3.client('glacier', region_name=config.get('aws', 'AwsRegionName'))

def initiate_restore(archive_id, retrieval_type='Expedited'):

    try:
        # Initiate archive retrieval job
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/initiate_job.html
        response = glacier.initiate_job(
            vaultName=config.get('glacier', 'VaultName'),
            jobParameters={
                'Type': 'archive-retrieval',
                'ArchiveId': archive_id,
                'Tier': retrieval_type
            }
        )
        print(f"Initiated {retrieval_type} retrieval for archive {archive_id}.")
        # Check if the response contains a jobId
        if 'jobId' in response:
            print(f"Successfully initiated {retrieval_type} retrieval for archive {archive_id}. Job ID: {response['jobId']}")
            return True, response['jobId']  # Indicate success and return the Job ID
        else:
            print(f"Failed to initiate retrieval for archive {archive_id}. No Job ID returned.")
            return False, None

    except ClientError as e:
        if e.response['Error']['Code'] == 'InsufficientCapacityException':
            if retrieval_type == 'Expedited':
                print("Expedited retrieval limit exceeded, falling back to Standard retrieval.")
                return initiate_restore(archive_id, 'Standard')
            else:
                print(f"Error restoring file through {retrieval_type}, due to {e.response['Error']}")
                return False, None
        else:
            print(f"Error initiating restore: {e}")
            return False, None

    except Exception as e:
        print(f"Error initiating restore: {e}")
        return False, None


def handle_thaw_queue(sqs=None):

    messages = {}

    # Read messages from the queue
    # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/sqs-example-sending-receiving-msgs.html
    try:
        messages = sqs.receive_message(
                    QueueUrl=config.get('sqs', 'SqsUrl'),
                    MaxNumberOfMessages=config.getint('sqs', 'MaxMessages'),
                    WaitTimeSeconds=config.getint('sqs', 'WaitTime')
                )
    except ClientError as e:
        print(f"Client error: {e}")

    except Exception as e:
        print(f"Error receiving messages: {e}")


    # Process messages
    if 'Messages' in messages: # To check if any messages were returned
        for message in messages['Messages']:
            body = json.loads(message['Body'])
            job_details = json.loads(body['Message'])

            if job_details['thaw_status'] == "STARTED":
                # Process messages --> initiate restore from Glacier
                archive_id = job_details['results_file_archive_id']
                success, thaw_id = initiate_restore(archive_id)
                job_id = job_details['job_id']

                if success:
                    sns_client = boto3.client('sns', region_name=config.get('aws', 'AwsRegionName'))

                    # Change the thaw status and push it as a new message in the queue
                    job_details['thaw_status'] = "IN PROGRESS"
                    job_details['thaw_id'] = thaw_id

                    try:
                        # Publish new Process message to the SNS topic
                        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sns/client/publish.html
                        sns_client.publish(TopicArn=config.get('sns', 'TopicArn'), Message = json.dumps(job_details), Subject = 'Thaw Process Submission')
                    except ClientError as e:
                        print("SNS Clinet Error when publishing Thaw process message")
                        break
                    except Exception as e:
                        print(f"Error publishing process message to SNS: {e} ")
                        break


                    try:
                        response = table.update_item(
                            Key={'job_id': job_id},
                            UpdateExpression='SET job_status = :status',
                            ExpressionAttributeValues={
                                ':status': 'RESTORING'
                            },
                            ReturnValues='UPDATED_NEW'
                        )
                        print(f"Successfully updated job {job_id} to RESTORING status.")
                    except ClientError as e:
                        print(f"Error updating DynamoDB: {e.response['Error']['Message']}")
                        break
                    except Exception as e:
                        print(f"Unexpected error when updating DynamoDB: {str(e)}")
                        break

                    try:
                        # Delete message from queue after successful processing
                        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs.html#SQS.Client.delete_message
                        sqs.delete_message(QueueUrl=config.get('sqs', 'SqsUrl'), ReceiptHandle=message['ReceiptHandle'])
                    except ClientError as e:
                        print(f"Client error deleting thaw status message: {e}")
                    except Exception as e:
                        # General exception for any other unforeseen errors
                        print(f"Error message deleting thaw status message: {str(e)}")


                else:
                    print("Could not start Thaw Process")


            if job_details['thaw_status'] == "IN PROGRESS":
                thaw_id = job_details['thaw_id']

                try:
                    # To get the retrieval job status 
                    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/describe_job.html
                    response = glacier.describe_job(
                        vaultName=config.get('glacier', 'VaultName'),
                        jobId=thaw_id
                    )
                    status = response['Completed']

                except ClientError as e:
                    print("Client error when retrieving thaw job details")
                except Exception as e:
                    print(f"Error retrieving thaw job details: {e}")

                if status:
                    print("Thaw completed")

                    # Creating Payload to start the Lambda Function
                    lambda_client = boto3.client('lambda', region_name=config.get('aws', 'AwsRegionName'))

                    payload = {
                        "job_id": job_details['job_id'],
                        "user_id": job_details['user_id'],
                        "s3_results_bucket": job_details['s3_results_bucket'],
                        "s3_key_result_file": job_details['s3_key_result_file'],
                        "archive_id": job_details['results_file_archive_id'],
                        "thaw_id": job_details['thaw_id']
                    }

                    try:
                        # Invoking lambda with the payload
                        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/lambda/client/invoke.html
                        response = lambda_client.invoke(
                            FunctionName=config.get('lambda', 'FunctionName'),
                            Payload=json.dumps(payload),
                        )
                    except ClientError as e:
                        print("Client error when invoking lambda")
                        break
                    except Exception as e:
                        print(f"Error invoking lambda: {e}")
                        break


                    try:
                        # Delete message from queue after successful processing
                        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs.html#SQS.Client.delete_message
                        sqs.delete_message(QueueUrl=config.get('sqs', 'SqsUrl'), ReceiptHandle=message['ReceiptHandle'])
                    except ClientError as e:
                        print(f"Client error deleting thaw process message: {e}")
                    except Exception as e:
                        # General exception for any other unforeseen errors
                        print(f"Error message deleting thaw process message: {str(e)}")


def main():

    # Get handles to resources
    sqs = boto3.client('sqs', region_name=config.get('aws', 'AwsRegionName'))

    # Poll queue for new results and process them
    while True:
        handle_thaw_queue(sqs)


if __name__ == "__main__":
    main()

### EOF
