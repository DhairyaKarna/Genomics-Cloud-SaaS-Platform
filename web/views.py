# views.py
#
# Copyright (C) 2015-2023 Vas Vasiliadis
# University of Chicago
#
# Application logic for the GAS
#
##
__author__ = "Vas Vasiliadis <vas@uchicago.edu>"

import uuid
import time
import json
from datetime import datetime, timedelta, timezone

import boto3
from botocore.client import Config
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from flask import abort, flash, redirect, render_template, request, session, url_for

from app import app, db
from decorators import authenticated, is_premium

"""Start annotation request
Create the required AWS S3 policy document and render a form for
uploading an annotation input file using the policy document

Note: You are welcome to use this code instead of your own
but you can replace the code below with your own if you prefer.
"""


@app.route("/annotate", methods=["GET"])
@authenticated
def annotate():
    # Open a connection to the S3 service
    s3 = boto3.client(
        "s3",
        region_name=app.config["AWS_REGION_NAME"],
        config=Config(signature_version="s3v4"),
    )

    bucket_name = app.config["AWS_S3_INPUTS_BUCKET"]
    user_id = session["primary_identity"]

    # Generate unique ID to be used as S3 key (name)
    key_name = (
        app.config["AWS_S3_KEY_PREFIX"]
        + user_id
        + "/"
        + str(uuid.uuid4())
        + "~${filename}"
    )

    # Create the redirect URL
    redirect_url = str(request.url) + "/job"

    # Define policy conditions
    encryption = app.config["AWS_S3_ENCRYPTION"]
    acl = app.config["AWS_S3_ACL"]
    fields = {
        "success_action_redirect": redirect_url,
        "x-amz-server-side-encryption": encryption,
        "acl": acl,
        "csrf_token": app.config["SECRET_KEY"],
    }
    conditions = [
        ["starts-with", "$success_action_redirect", redirect_url],
        {"x-amz-server-side-encryption": encryption},
        {"acl": acl},
        ["starts-with", "$csrf_token", ""],
    ]

    # Generate the presigned POST call
    try:
        presigned_post = s3.generate_presigned_post(
            Bucket=bucket_name,
            Key=key_name,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=app.config["AWS_SIGNED_REQUEST_EXPIRATION"],
        )
    except ClientError as e:
        app.logger.error(f"Unable to generate presigned URL for upload: {e}")
        return abort(500)

    # Render the upload form which will parse/submit the presigned POST
    return render_template(
        "annotate.html", s3_post=presigned_post, role=session["role"]
    )


"""Fires off an annotation job
Accepts the S3 redirect GET request, parses it to extract 
required info, saves a job item to the database, and then
publishes a notification for the annotator service.

Note: Update/replace the code below with your own from previous
homework assignments
"""


@app.route("/annotate/job", methods=["GET"])
@authenticated
def create_annotation_job_request():

    region = app.config["AWS_REGION_NAME"]

    # Parse redirect URL query parameters for S3 object info
    bucket_name = request.args.get("bucket")
    s3_key = request.args.get("key")

    if not bucket_name or not s3_key:
        app.logger.error("Missing bucket or key parameters")
        abort(400, description="Missing bucket or key parameters")

    # Extract the job ID from the S3 key
    file_name = s3_key.split('/')[-1]
    job_id, file_name = file_name.split('~')

    # Current time as epoch for submit_time
    # https://www.programiz.com/python-programming/time
    submit_time = int(time.time())

    # Get the user ID from the session
    user_id = session["primary_identity"]

    # Preparing data for DynamoDB
    data = {
        'job_id': job_id,
        'user_id': user_id,
        "input_file_name": file_name, 
        "s3_inputs_bucket": bucket_name,
        "s3_key_input_file": s3_key,
        "submit_time": submit_time,
        'job_status': 'PENDING'
    }


    # Persist job to database
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        # Persist job details to DynamoDB
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/put_item.html
        table.put_item(Item=data)
    except ClientError as e:
        app.logger.exception("DynamoDB Client Error")
        abort(500, description=f"DynamoDB client error: {e.response['Error']['Code']}")
    except Exception as e:
        app.logger.exception("Encountered an error persisting details to DynamoDB")
        abort(500, description=f"Error persisting job details to DynamoDB: {e}")

    # Send message to request queue
    sns = boto3.client('sns', region_name=app.config["AWS_REGION_NAME"])

    try:
        # Construct and send the POST request to the annotator
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sns/client/publish.html
        sns.publish(TopicArn=app.config['AWS_SNS_JOB_REQUEST_TOPIC'], Message = json.dumps(data), Subject = 'Job Submission')

    except ClientError as e:
        app.logger.exception("SNS Client Error")
        abort(500, description=f"SNS client error: {e.response['Error']['Code']}")
    except Exception as e:
        app.logger.exception("Encountered an error publishing details to SNS")
        abort(500, description=f"Error publishing job details to SNS: {e}")

    return render_template("annotate_confirm.html", job_id=job_id)


""" Convert epoch time to CST
"""

def epoch_to_CST(epoch_time):
    """
    This methods convrets epoch time in to CST time
    https://ioflood.com/blog/python-timedelta/#:~:text=Python's%20timedelta%20is%20a%20function,between%20two%20dates%2C%20and%20more.&text=In%20this%20code%2C%20we're,the%20current%20date%20using%20datetime.
    """

    # Convert epoch to datetime in UTC
    utc_time = datetime.fromtimestamp(int(epoch_time), timezone.utc)

    # Defining the CST offset: UTC-6 hours
    cst_offset = timedelta(hours=-6)

    # Applying the CST offset to UTC time
    cst_time = utc_time + cst_offset

    # Formatting the time as needed
    return cst_time.strftime('%Y-%m-%d %H:%M:%S')

"""List all annotations for the user
"""


@app.route("/annotations", methods=["GET"])
@authenticated
def annotations_list():

    user_id = session["primary_identity"]

    
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        # Query the DynamoDB table using the secondary index
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/table/query.html
        response = table.query(
            IndexName='user_id_index',  
            KeyConditionExpression=Key('user_id').eq(user_id)
        )
    except ClientError as e:
        app.logger.error(f"Error querying DynamoDB: {e}")
        abort(500)  # Internal Server Error

    except Exception as e:
        # Handle all other possible exceptions
        app.logger.error(f"Unexpected error: {str(e)}")
        abort(500)  # Internal Server Error


    # Get list of annotations to display
    jobs = response.get('Items', [])


    # Check if there are no jobs
    if not jobs:
        message = "No annotations found."
        return render_template("annotations.html", message=message)

    # Convert epoch to human-readable date-time in CST
    for job in jobs:
        
        # Converting time to CST
        job['submit_time'] = epoch_to_CST(job['submit_time'])

    return render_template("annotations.html", annotations=jobs)


"""Display details of a specific annotation job
"""


@app.route("/annotations/<id>", methods=["GET"])
def annotation_details(id):
    user_id = session.get("primary_identity")
    job_id = id


    # In case of new user who just registered, the role field is not populated, To handle that the default value for role is free_user
    user_role = session.get("role", "free_user")
    
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        # Get job details from the DynamoDB table
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/table/get_item.html
        response = table.get_item(Key={'job_id': job_id})
        job = response.get('Item', None)
        
        # Check if the job exists and belongs to the user
        if not job:
            app.logger.error('Job not found')
            abort(404, description="Job not found")
        if job.get('user_id') != user_id:
            # Forbidden if job doesn't belong to the user
            app.logger.error('Job does not belong to the current user')
            abort(403, description="Job does not belong to the current user")     

        show_upgrade_link = False  # Default to not showing the upgrade link

        # Convert epoch times to CST
        job['request_time_formatted'] = epoch_to_CST(job['submit_time'])
        if 'complete_time' in job and (job['job_status'] == 'COMPLETED' or job['job_status'] == 'RESTORING'):
            job['complete_time_formatted'] = epoch_to_CST(job['complete_time'])

        
            current_time = int(time.time())
            current_time_string = epoch_to_CST(current_time)
            current_time_object = datetime.strptime(current_time_string, '%Y-%m-%d %H:%M:%S')
            completed_time_object = datetime.strptime(job['complete_time_formatted'], '%Y-%m-%d %H:%M:%S')

            # Calculating the difference between now and completed time
            difference = abs(current_time_object - completed_time_object)

            if user_role == "free_user" and difference.total_seconds() > 180:
                show_upgrade_link = True

        return render_template("annotation.html", job=job, show_upgrade_link =show_upgrade_link)
        
    except ClientError as e:
        app.logger.error(f"Error fetching job details from DynamoDB: {e}")
        abort(500)  # Internal Server Error


"""Download the input vcf file using pre signed url
"""


@app.route('/download_input/<job_id>', methods=["GET"])
@authenticated
def download_input(job_id):
    user_id = session["primary_identity"]

    
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        # Get input file details from the DynamoDB table
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/table/get_item.html
        response = table.get_item(Key={'job_id': job_id})
        job = response.get('Item', None)
        
        # Check if the job exists and belongs to the user
        if not job:
            app.logger.error('Job not found')
            abort(404, description="Job not found")
        if job.get('user_id') != user_id:
            # Forbidden if job doesn't belong to the user
            app.logger.error('Job does not belong to the current user')
            abort(403, description="Job does not belong to the current user")   
        
        # Generate the pre-signed URL for the input file
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/generate_presigned_url.html
        s3_client = boto3.client('s3', region_name=app.config["AWS_REGION_NAME"])
        presigned_url = s3_client.generate_presigned_url('get_object',
                                                         Params={'Bucket': job['s3_inputs_bucket'],
                                                                 'Key': job['s3_key_input_file']},
                                                         ExpiresIn=3600)
        return redirect(presigned_url)
    except ClientError as e:
        # Log the error and abort
        app.logger.error(f"Error fetching job details or generating presigned URL: {e}")
        abort(500)  # Internal Server Error


"""Download the result annot.vcf file using presigned url
"""


@app.route('/download_result/<job_id>', methods=["GET"])
@authenticated
def download_result(job_id):
    user_id = session["primary_identity"]

    # Query the DynamoDB table for the job details
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        # Get results details from the DynamoDB table
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/table/get_item.html
        response = table.get_item(Key={'job_id': job_id})
        job = response.get('Item', None)
        
        # Check if the job exists and belongs to the user and is completed
        if not job:
            app.logger.error('Job not found')
            abort(404, description="Job not found")
        if job.get('user_id') != user_id:
            # Forbidden if job doesn't belong to the user
            app.logger.error('Job does not belong to the current user')
            abort(403, description="Job does not belong to the current user") 
        if job.get('job_status') != 'COMPLETED':
            app.logger.error('Job  is not completed')
            abort(404, description="Job is not completed")
        
        # Generate the pre-signed URL for the results file
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/generate_presigned_url.html
        s3_client = boto3.client('s3')
        presigned_url = s3_client.generate_presigned_url('get_object',
                                                         Params={'Bucket': job['s3_results_bucket'],
                                                                 'Key': job['s3_key_result_file']},
                                                         ExpiresIn=3600)
        return redirect(presigned_url)
    except ClientError as e:
        # Log the error and abort
        app.logger.error(f"Error fetching job details or generating presigned URL: {e}")
        abort(500)  # Internal Server Error


"""Display the log file contents for an annotation job
"""


@app.route('/annotations/<id>/log', methods=["GET"])
@authenticated
def view_log(id):
    user_id = session["primary_identity"]
    job_id = id
    
    dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
    table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

    try:
        # Get log details from the DynamoDB table
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/table/get_item.html
        response = table.get_item(Key={'job_id': job_id})
        job = response.get('Item', None)
        
        # Check if the job exists, belongs to the user and is completed
        if not job:
            app.logger.error('Job not found')
            abort(404, description="Job not found")
        if job.get('user_id') != user_id:
            # Forbidden if job doesn't belong to the user
            app.logger.error('Job does not belong to the current user')
            abort(403, description="Job does not belong to the current user") 
        if job.get('job_status') != 'COMPLETED':
            app.logger.error('Job  is not completed')
            abort(404, description="Job is not completed")

        # Read the log file content from S3
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/get_object.html
        s3_client = boto3.client('s3')
        log_file_object = s3_client.get_object(Bucket=job['s3_results_bucket'],
                                               Key=job['s3_key_log_file'])
        log_contents = log_file_object['Body'].read().decode('utf-8')

        return render_template('view_log.html', log_content=log_contents, job_id=job_id)
        
    except ClientError as e:
        # Log the error and abort
        app.logger.error(f"Error fetching job details or reading log file: {e}")
        abort(500)  # Internal Server Error

"""Subscription management handler
"""
import stripe
from auth import update_profile


@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "GET":
        # Display form to get subscriber credit card info

        return render_template("subscribe.html")

    elif request.method == "POST":
        try:
            # Process the subscription request
            stripe_token = request.form['stripe_token']

            stripe.api_key = app.config['STRIPE_SECRET_KEY']

            # Create a customer on Stripe
            # https://www.altcademy.com/codedb/examples/create-a-stripe-customer-from-an-email-address-in-python
            customer = stripe.Customer.create(
                email=session['email'],  
                name=session.get('name'), 
                card=stripe_token
            )

            # Subscribe customer to pricing plan
            # https://docs.stripe.com/api/subscriptions/create
            subscription = stripe.Subscription.create(
                customer=customer.id,
                items=[
                    {"price": app.config['STRIPE_PRICE_ID']},
                ],
            )

        except stripe.error.StripeError as e:
            # Handle Stripe errors (e.g., invalid token, network issues)
            return render_template("error.html", title="Stripe Error", message=str(e), alert_level="warning")

        except Exception as e:
            # Handle other exceptions
            return render_template("error.html", title="Error", message=str(e), alert_level="warning")


        user_id = session["primary_identity"]

        # Update user role in accounts database
        update_profile(user_id, role='premium_user')  


        # Update role in the session
        session['role'] = 'premium_user'

        
        dynamodb = boto3.resource('dynamodb', region_name=app.config["AWS_REGION_NAME"])
        table = dynamodb.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])


        try:
            # Query the DynamoDB table using the secondary index and filter for jobs with a results_file_archive_id
            response = table.query(
                IndexName='user_id_index',
                KeyConditionExpression=Key('user_id').eq(user_id),
                FilterExpression=Attr('results_file_archive_id').exists()
            )
        except ClientError as e:
            app.logger.error(f"Error querying DynamoDB: {e}")
            abort(500)  # Internal Server Error
        except Exception as e:
            app.logger.error(f"Unexpected error: {str(e)}")
            abort(500)  # Internal Server Error



        # Get list of annotations to display
        jobs = response.get('Items', [])


        # Request restoration of the user's data from Glacier
        sns_client = boto3.client('sns', region_name=app.config['AWS_REGION_NAME'])

        for job in jobs:
            if 'results_file_archive_id' in job:
                message = {
                    "job_id": job['job_id'],
                    "user_id": user_id,
                    "results_file_archive_id": job['results_file_archive_id'],
                    "s3_results_bucket": job['s3_results_bucket'],
                    "s3_key_result_file": job['s3_key_result_file'],
                    "thaw_status": "STARTED"
                }

                try:
                    # Publish message to the SNS topic
                    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sns/client/publish.html
                    sns_client.publish(TopicArn=app.config['AWS_SNS_THAW_REQUEST_TOPIC'], Message = json.dumps(message), Subject = 'Thaw Request Submission')

                except ClientError as e:
                    app.logger.exception("SNS Client Error")
                    abort(500, description=f"SNS client error: {e.response['Error']['Code']}")
                except Exception as e:
                    app.logger.exception("Encountered an error publishing details to SNS")
                    abort(500, description=f"Error publishing job details to SNS: {e}")


        # Display confirmation page
        return render_template("subscribe_confirm.html", stripe_id=subscription.id)

        


"""DO NOT CHANGE CODE BELOW THIS LINE
*******************************************************************************
"""

"""Set premium_user role
"""


@app.route("/make-me-premium", methods=["GET"])
@authenticated
def make_me_premium():
    # Hacky way to set the user's role to a premium user; simplifies testing
    update_profile(identity_id=session["primary_identity"], role="premium_user")
    return redirect(url_for("profile"))


"""Reset subscription
"""


@app.route("/unsubscribe", methods=["GET"])
@authenticated
def unsubscribe():
    # Hacky way to reset the user's role to a free user; simplifies testing
    update_profile(identity_id=session["primary_identity"], role="free_user")
    return redirect(url_for("profile"))


"""Home page
"""


@app.route("/", methods=["GET"])
def home():
    return render_template("home.html"), 200


"""Login page; send user to Globus Auth
"""


@app.route("/login", methods=["GET"])
def login():
    app.logger.info(f"Login attempted from IP {request.remote_addr}")
    # If user requested a specific page, save it session for redirect after auth
    if request.args.get("next"):
        session["next"] = request.args.get("next")
    return redirect(url_for("authcallback"))


"""404 error handler
"""


@app.errorhandler(404)
def page_not_found(e):
    return (
        render_template(
            "error.html",
            title="Page not found",
            alert_level="warning",
            message="The page you tried to reach does not exist. \
      Please check the URL and try again.",
        ),
        404,
    )


"""403 error handler
"""


@app.errorhandler(403)
def forbidden(e):
    return (
        render_template(
            "error.html",
            title="Not authorized",
            alert_level="danger",
            message="You are not authorized to access this page. \
      If you think you deserve to be granted access, please contact the \
      supreme leader of the mutating genome revolutionary party.",
        ),
        403,
    )


"""405 error handler
"""


@app.errorhandler(405)
def not_allowed(e):
    return (
        render_template(
            "error.html",
            title="Not allowed",
            alert_level="warning",
            message="You attempted an operation that's not allowed; \
      get your act together, hacker!",
        ),
        405,
    )


"""500 error handler
"""


@app.errorhandler(500)
def internal_error(error):
    return (
        render_template(
            "error.html",
            title="Server error",
            alert_level="danger",
            message="The server encountered an error and could \
      not process your request.",
        ),
        500,
    )


"""CSRF error handler
"""


from flask_wtf.csrf import CSRFError


@app.errorhandler(CSRFError)
def csrf_error(error):
    return (
        render_template(
            "error.html",
            title="CSRF error",
            alert_level="danger",
            message=f"Cross-Site Request Forgery error detected: {error.description}",
        ),
        400,
    )


### EOF
