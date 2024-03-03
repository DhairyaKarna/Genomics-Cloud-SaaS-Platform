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

def initiate_restore(archive_id, retrieval_type='Expedited'):

    glacier = boto3.client('glacier', region_name=config.get('aws', 'AwsRegionName'))

    try:
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

                if success:
                    sns_client = boto3.client('sns', region_name=config.get('aws', 'AwsRegionName'))

                    # Change the thaw status and push it as a new message in the queue
                    job_details['thaw_status'] = "IN PROGRESS"

                    try:
                        # Publish new Process message to the SNS topic
                        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sns/client/publish.html
                        sns_client.publish(TopicArn=config.get('sns', 'TopicArn'), Message = json.dumps(job_details), Subject = 'Thaw Process Submission')
                    except ClientError as e:
                        print("SNS Clinet Error when publishing Thaw process message")
                    except Exception as e:
                        print(f"Error publishing process message to SNS: {e} ")

                    try:
                        # Delete message from queue after successful processing
                        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs.html#SQS.Client.delete_message
                        sqs.delete_message(QueueUrl=config.get('sqs', 'SqsUrl'), ReceiptHandle=message['ReceiptHandle'])
                    except ClientError as e:
                        print(f"Client error: {e}")
                    except Exception as e:
                        # General exception for any other unforeseen errors
                        print(f"Error message: {str(e)}")

                else:
                    print("Could not start Thaw Process")

            



def main():

    # Get handles to resources
    sqs = boto3.client('sqs', region_name=config.get('aws', 'AwsRegionName'))

    # Poll queue for new results and process them
    while True:
        handle_thaw_queue(sqs)


if __name__ == "__main__":
    main()

### EOF
