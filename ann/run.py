# run.py
#
# Runs the AnnTools pipeline
#
# NOTE: This file lives on the AnnTools instance and
# replaces the default AnnTools run.py
#
# Copyright (C) 2015-2024 Vas Vasiliadis
# University of Chicago
##
__author__ = "Vas Vasiliadis <vas@uchicago.edu>"

import sys
import time
import driver
import os
import boto3
import json

# Add the parent directory to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

# Import helpers from util to get user profile (to check for Free users)
from util import helpers

# Get configuration
from configparser import ConfigParser, ExtendedInterpolation

config = ConfigParser(os.environ, interpolation=ExtendedInterpolation())
config.read("annotator_config.ini")

"""A rudimentary timer for coarse-grained profiling
"""

# Initializing AWS clients with config
s3 = boto3.client('s3', region_name=config.get('aws', 'AwsRegionName'))
dynamodb = boto3.resource('dynamodb', region_name=config.get('aws', 'AwsRegionName'))
table = dynamodb.Table(config.get('gas', 'AnnotationsTable'))
sfn = boto3.client('stepfunctions', region_name=config.get('aws', 'AwsRegionName'))

class Timer(object):
    def __init__(self, verbose=True):
        self.verbose = verbose

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.secs = self.end - self.start
        if self.verbose:
            print(f"Approximate runtime: {self.secs:.2f} seconds")

def upload_to_s3(file_path, bucket_name, s3_directory, original_file_name):
    """
    Upload a file to an S3 bucket
    
    :param file_path: File to upload
    :param bucket_name: Bucket to upload to
    :param s3_directory: The directory in the bucket where the file will be stored
    :param original_file_name: The original name of the file
    """
    s3_key = f"{s3_directory}/{original_file_name}"
    try:
        # Uploading the file to S3
        # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-uploading-files.html
        s3.upload_file(file_path, bucket_name, s3_key)
        return s3_key
    except ClientError as e:
        print(f"ClientError when uploading {file_path}: {e}") 
    except NoCredentialsError:
        print(f"No AWS credentials found for uploading {file_path}")
    except Exception as e:
        print(f"Error uploading {file_path}: {e}")

def update_dynamodb(job_id, s3_results_bucket, s3_key_result_file, s3_key_log_file):
    """
    Update the DynamoDB table with the results of the annotation job
    
    :param job_id : The UUID of the job to update
    :param s3_results_bucket : The S3 bucket where the results file is stored
    :param s3_key_result_file : The S3 key for the results file
    :param s3_key_log_file : The S3 key for the log file
    """
    # Current time as epoch for complete_time
    # https://www.programiz.com/python-programming/datetime/current-time
    complete_time = int(time.time())
    
    try:
        # Updating the DynamoDB table with the results of the annotation job
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/table.html#DynamoDB.Table.update_item
        response = table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='SET s3_results_bucket = :resBucket, s3_key_result_file = :resKey, s3_key_log_file = :logKey, complete_time = :compTime, job_status = :status',
            ExpressionAttributeValues={
                ':resBucket': s3_results_bucket,
                ':resKey': s3_key_result_file,
                ':logKey': s3_key_log_file,
                ':compTime': complete_time,
                ':status': 'COMPLETED'
            },
            ReturnValues='UPDATED_NEW'
        )
        
    except ClientError as e:
        print(f"Error updating DynamoDB: {e.response['Error']['Message']}")
    except Exception as e:
        print(f"Unexpected error when updating DynamoDB: {str(e)}")


def start_sfn(job_id, user_id, results_s3_key):

    user = helpers.get_user_profile(user_id)
    role = user['role']

    if role == "free_user":
        input_data = {
        "job_id" : job_id,
        "user_id" : user_id,
        "results_s3_key" : results_s3_key
        }

        try:
            response = sfn.start_execution(
                stateMachineArn = config.get('sfn', 'SfnArn'),
                input = json.dumps(input_data)
            )
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ValidationException':
                print("Input data or parameters are not valid.")
            elif error_code == 'ExecutionLimitExceeded':
                print("Execution limit exceeded.")
            elif error_code == 'StateMachineDoesNotExist':
                print("State machine does not exist.")
            elif error_code == 'StateMachineDeleting':
                print("State machine is currently deleting.")
            else:
                print(f"A client error occurred: {e}")
        except Exception as e:
            print(f"Unexpected error when updating DynamoDB: {str(e)}")


def delete_local_file(file_path):
    """
    Delete a file from the local file system
    
    :param file_path: File to delete
    """
    try:
        # Deleting the file in local  
        # https://www.geeksforgeeks.org/python-os-remove-method/ 
        os.remove(file_path)
    except OSError as e:
        print(f"OSError when deleting {file_path}: {e}")
    except Exception as e:
        print(f"Error deleting {file_path}: {e}")



def main():

    # Get job parameters
    input_file_name = sys.argv[1]
    base_file_name = os.path.basename(input_file_name)  # Get the base file name

    job_id = sys.argv[2]
    user_id = sys.argv[3]

    # Run the AnnTools pipeline
    with Timer():
        driver.run(input_file_name, "vcf")

    s3_bucket_name = config.get('s3', 'ResultsBucketName')
    s3_directory = f"{config.get('s3', 'KeyPrefix')}{user_id}"

    # Defining the results path on instance
    results_file_path = input_file_name.replace('.vcf', '.annot.vcf')
    log_file_path = input_file_name.replace('.vcf', '.vcf.count.log')

    # Defining the S3 paths for results and log files
    results_file = base_file_name.replace('.vcf', '.annot.vcf')
    log_file = base_file_name.replace('.vcf', '.vcf.count.log')

    # Upload the result and log files to S3
    results_s3_key = upload_to_s3(results_file_path, s3_bucket_name, s3_directory, results_file)
    log_s3_key = upload_to_s3(log_file_path, s3_bucket_name, s3_directory, log_file) 

    # Update annotations database
    update_dynamodb(job_id, s3_bucket_name, results_s3_key, log_s3_key)

    # Start the step function for archival process if user is FREE
    start_sfn(job_id, user_id, results_s3_key)

    # Clean up local job files
    delete_local_file(results_file_path)
    delete_local_file(log_file_path)
    delete_local_file(input_file_name)


if __name__ == "__main__":
    main()

### EOF
