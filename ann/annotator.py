# annotator.py
#
# NOTE: This file lives on the AnnTools instance
#
# Copyright (C) 2013-2023 Vas Vasiliadis
# University of Chicago
##
__author__ = "Vas Vasiliadis <vas@uchicago.edu>"

import boto3
import json
import os
import sys
import time
from subprocess import Popen, PIPE
from botocore.exceptions import ClientError

# Get configuration
from configparser import ConfigParser, ExtendedInterpolation

config = ConfigParser(os.environ, interpolation=ExtendedInterpolation())
config.read("annotator_config.ini")


"""Reads request messages from SQS and runs AnnTools as a subprocess.

Move existing annotator code here
"""
# Initializing AWS clients with config
s3 = boto3.client('s3', region_name=config.get('aws', 'AwsRegionName'))
dynamodb = boto3.resource('dynamodb', region_name=config.get('aws', 'AwsRegionName'))
table = dynamodb.Table(config.get('gas', 'AnnotationsTable'))

# Getting the current path to save the data
BASE_DIR = os.path.abspath(os.path.dirname(__file__)) + "/"
ANN_DIR = BASE_DIR + "run.py"
DATA_DIR = BASE_DIR + "data/" 


def update_dynamodb(job_id):
    # To catch error while updating the table
        try:
            # Update job status to "RUNNING" only when the current status is "PENDING" in DynamoDB
            # https://stackoverflow.com/questions/34447304/example-of-update-item-in-dynamodb-boto3
            response = table.update_item(
                Key={'job_id': job_id},
                UpdateExpression='SET job_status = :newStatus',
                ConditionExpression='job_status = :expectedStatus',
                ExpressionAttributeValues={
                    ':newStatus': 'RUNNING',
                    ':expectedStatus': 'PENDING'
                },
                ReturnValues='ALL_NEW'
            )
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            if error_code == 'ConditionalCheckFailedException':
                print(f"Job ID {job_id} status update condition not met (likely not 'PENDING'). Code={error_code}, Message={error_message}")
            else:
                # Return a more generic error response to the client
                print(f"Failed to update job status in DynamoDB Code={error_code}, Message={error_message}")
        except Exception as e:
            # Handle unexpected errors
            print(f"An unexpected error occurred: {str(e)}")

def handle_requests_queue(sqs=None):

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

            successful_download = False  # Flag to track download success

            # Extracting job parameters from each message
            body = json.loads(message['Body'])
            job_details = json.loads(body['Message'])

            job_id = job_details['job_id']
            s3_key_input_file = job_details['s3_key_input_file']

            user_id = s3_key_input_file.split('/')[1]
            file_name = s3_key_input_file.split('/')[-1]  # Extract file name from s3_key_input_file

            # Checking for Data directory and creating a file path for download 
            if not os.path.exists(DATA_DIR):
                os.makedirs(DATA_DIR)
            file_path = os.path.join(DATA_DIR, file_name)  


            # To catch error while downloading files from s3 bucket
            # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-example-download-file.html
            try:
                s3.download_file(config.get('s3', 'InputsBucketName'), s3_key_input_file, file_path)
                successful_download = True

            except ClientError as e:
                print(f"Client error: {e}")
            except NoCredentialsError:
                print("Credential Error")
            except BotoCoreError as e:
                # Handle any boto core errors that are not covered above
                print(f"BotoCore error: {e}")
            except Exception as e:
                # General exception for any other unforeseen errors
                print(f"Error message: {str(e)}")

            # Only start subprocess if download is successful
            if successful_download:
                # To Catch errors in subprocess or when deleting message
                try:
                    Popen(['python', ANN_DIR, file_path, job_id, user_id])

                    update_dynamodb(job_id)

                    # Delete message from queue after successful processing
                    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs.html#SQS.Client.delete_message
                    sqs.delete_message(QueueUrl=config.get('sqs', 'SqsUrl'), ReceiptHandle=message['ReceiptHandle'])

                except ClientError as e:
                    print(f"Client error: {e}")
                except OSError as e:
                    print(f"OSError: {e}")
                except ValueError as e:
                    # Handle errors related to invalid arguments
                    print(f"ValueError: {e}")
                except Exception as e:
                    # General exception for any other unforeseen errors
                    print(f"Error message: {str(e)}")


def main():

    # Get handles to queue
    sqs = boto3.client('sqs', region_name=config.get('aws', 'AwsRegionName'))

    # Poll queue for new results and process them
    while True:
        handle_requests_queue(sqs)


if __name__ == "__main__":
    main()

### EOF
