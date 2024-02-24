# archive_script.py
#
# Archive free user data
#
# Copyright (C) 2015-2023 Vas Vasiliadis
# University of Chicago
##
__author__ = "Vas Vasiliadis <vas@uchicago.edu>"

import boto3
import time
import os
import sys
import json
from botocore.exceptions import ClientError, BotoCoreError

# Import utility helpers
sys.path.insert(1, os.path.realpath(os.path.pardir))
import helpers

# Get configuration
from configparser import ConfigParser, ExtendedInterpolation

config = ConfigParser(os.environ, interpolation=ExtendedInterpolation())
config.read("../util_config.ini")
config.read("archive_script_config.ini")

# Initializing AWS clients with config
s3 = boto3.client('s3', region_name=config.get('aws', 'AwsRegionName'))
glacier = boto3.client('glacier', region_name=config.get('aws', 'AwsRegionName'))

"""A14
Archive free user results files
"""


def handle_archive_queue(sqs=None):

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

    # Process messages --> archive results file
    if 'Messages' in messages: # To check if any messages were returned
        for message in messages['Messages']:

            # Extracting job parameters from each message
            body = json.loads(message['Body'])
            job_details = json.loads(body['Message'])

            job_id = job_details['job_id']
            user_id = job_details['user_id']

            user = helpers.get_user_profile(user_id)
            role = user['role']

            if role == "free_user":
                s3_bucket_name = job_details['s3_bucket_name']
                results_s3_key = job_details['results_s3_key']

                # Getting S3 object for the results s3 key
                try:
                    result_object = s3.get_object(Bucket = s3_bucket_name, Key=results_s3_key)
                    data = result_object['Body'].read()

                except ClientError as e:
                    if e.response['Error']['Code'] == 'NoSuchKey':
                        print("The specified key does not exist.")
                    else:
                        print(f"ClientError: {e}")
                except BotoCoreError as e:
                    print(f"BotoCoreError: {e}")
                except Exception as e:
                    print(f"Unexpected error when getting s3 object: {str(e)}")

                # Uploading the Results file to glacier
                try:
                    response = glacier.upload_archive(vaultName=config.get('glacier', 'VaultName'), body=data)

                except ClientError as e:
                    print(f"ClientError: {e}")
                except BotoCoreError as e:
                    print(f"BotoCoreError: {e}")
                except Exception as e:
                    print(f"Unexpected error when uploading to glacier: {str(e)}")

                # Deleting the file from S3
                try:
                    s3.delete_object(Bucket=s3_bucket_name, Key=results_s3_key)
                    print(f"File {results_s3_key} deleted from bucket {s3_bucket_name}.")
                except s3.exceptions.NoSuchKey:
                    print("The specified key does not exist.")
                except Exception as e:
                    print(f"Error deleting object: {e}")

            try:
                # Delete message from queue after successful processing
                # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs.html#SQS.Client.delete_message
                sqs.delete_message(QueueUrl=config.get('sqs', 'SqsUrl'), ReceiptHandle=message['ReceiptHandle'])
            except ClientError as e:
                print(f"Client error: {e}")
            except Exception as e:
                # General exception for any other unforeseen errors
                print(f"Error message: {str(e)}")


def main():

    # Get handles to SQS
    sqs = boto3.client('sqs', region_name=config.get('aws', 'AwsRegionName'))

    # Poll queue for new results and process them
    while True:
        handle_archive_queue(sqs)


if __name__ == "__main__":
    main()

### EOF
